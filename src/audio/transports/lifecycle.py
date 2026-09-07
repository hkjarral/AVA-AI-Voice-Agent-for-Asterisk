"""Shared selected-transport call-media setup lifecycle."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

from .base import CallMediaSetupResult, SelectedTransportRuntime


class CallMediaLifecycle:
    """Own create/attach/readiness ordering without owning caller/provider state."""

    def __init__(
        self,
        runtime: SelectedTransportRuntime,
        *,
        attach_attempts: int = 25,
        attach_retry_seconds: float = 0.1,
        setup_timeout_seconds: float | None = None,
    ) -> None:
        self.runtime = runtime
        self.attach_attempts = max(1, int(attach_attempts))
        self.attach_retry_seconds = max(0.0, float(attach_retry_seconds))
        self.setup_timeout_seconds = (
            None
            if setup_timeout_seconds is None
            else max(0.001, float(setup_timeout_seconds))
        )

    async def _abort_interrupted_setup(
        self,
        owner: Any,
        session: Any,
        channel_id: str | None,
    ) -> None:
        """Release a partially created leg without swallowing cancellation."""
        if channel_id:
            hangup = getattr(getattr(owner, "ari_client", None), "hangup_channel", None)
            if callable(hangup):
                try:
                    await asyncio.wait_for(hangup(channel_id), timeout=2.0)
                except Exception:
                    pass
        try:
            await asyncio.wait_for(
                self.runtime.close_call(str(session.call_id)),
                timeout=2.0,
            )
        except Exception:
            pass

    async def setup(self, owner: Any, session: Any) -> CallMediaSetupResult:
        request = None
        channel_id = None
        try:
            async with asyncio.timeout(self.setup_timeout_seconds):
                is_active = getattr(owner, "_media_session_is_active", None)
                if callable(is_active) and not await is_active(session):
                    return CallMediaSetupResult(
                        request=None,
                        failure_reason=f"{self.runtime.kind}-media-start-failed",
                        force_cleanup=bool(
                            getattr(
                                self.runtime,
                                "force_cleanup_on_prepare_failure",
                                True,
                            )
                        ),
                    )
                request = await self.runtime.prepare_call(session)
                if callable(is_active) and not await is_active(session):
                    await self._abort_interrupted_setup(owner, session, None)
                    return CallMediaSetupResult(
                        request=request,
                        failure_reason=request.start_failure_reason
                        or f"{request.kind}-media-start-failed",
                        force_cleanup=request.force_cleanup_on_failure,
                    )
                channel_id = await self.runtime.create_call(owner, session, request)
                if not channel_id:
                    # prepare_call may allocate a UDP slot or register a nonce
                    # even when caller cleanup wins before create_call begins.
                    await self._abort_interrupted_setup(owner, session, None)
                    return CallMediaSetupResult(
                        request=request,
                        failure_reason=request.start_failure_reason
                        or f"{request.kind}-media-start-failed",
                        force_cleanup=request.force_cleanup_on_failure,
                    )
                if callable(is_active) and not await is_active(session):
                    await self._abort_interrupted_setup(
                        owner, session, channel_id
                    )
                    return CallMediaSetupResult(
                        request=request,
                        channel_id=channel_id,
                        created=True,
                        failure_reason=request.setup_failure_reason
                        or f"{request.kind}-media-setup-failed",
                        force_cleanup=request.force_cleanup_on_failure,
                    )

                if request.attachment == "aux_event":
                    return CallMediaSetupResult(
                        request=request,
                        channel_id=channel_id,
                        created=True,
                    )

                if not getattr(session, "bridge_id", None):
                    if not request.fail_without_bridge:
                        return CallMediaSetupResult(
                            request=request,
                            channel_id=channel_id,
                            created=True,
                        )
                    return CallMediaSetupResult(
                        request=request,
                        channel_id=channel_id,
                        created=True,
                        failure_reason=request.attach_failure_reason
                        or f"{request.kind}-media-bridge-missing",
                        force_cleanup=request.force_cleanup_on_failure,
                    )

                attached = False
                for attempt in range(1, self.attach_attempts + 1):
                    if await self.runtime.attach_call(owner, session, channel_id):
                        attached = True
                        break
                    if attempt < self.attach_attempts and self.attach_retry_seconds:
                        await asyncio.sleep(self.attach_retry_seconds)
                if not attached:
                    return CallMediaSetupResult(
                        request=request,
                        channel_id=channel_id,
                        created=True,
                        failure_reason=request.attach_failure_reason
                        or f"{request.kind}-media-attach-failed",
                        force_cleanup=request.force_cleanup_on_failure,
                    )

                binding = await self.runtime.wait_call_ready(owner, session, request)
                if request.readiness == "protocol" and binding is None:
                    return CallMediaSetupResult(
                        request=request,
                        channel_id=channel_id,
                        created=True,
                        failure_reason=request.setup_failure_reason
                        or f"{request.kind}-media-setup-failed",
                        force_cleanup=request.force_cleanup_on_failure,
                    )
                ready = await self.runtime.finalize_call_ready(
                    owner, session, request, binding
                )
                return CallMediaSetupResult(
                    request=request,
                    channel_id=channel_id,
                    created=True,
                    ready=bool(ready),
                    failure_reason=(
                        None
                        if ready
                        else request.setup_failure_reason
                        or f"{request.kind}-media-setup-failed"
                    ),
                    force_cleanup=request.force_cleanup_on_failure and not ready,
                )
        except asyncio.CancelledError:
            # Cancellation is an ownership hand-off, not a successful setup.
            # Complete bounded local/ARI cleanup, then preserve cancellation.
            await asyncio.shield(
                self._abort_interrupted_setup(
                    owner, session, channel_id or (request.channel_id if request else None)
                )
            )
            raise
        except TimeoutError:
            # A timed-out create/attach must not retain transport-local state.
            # Caller ownership remains with the engine via ``force_cleanup``.
            await self._abort_interrupted_setup(
                owner, session, channel_id or (request.channel_id if request else None)
            )
            return CallMediaSetupResult(
                request=request,
                channel_id=channel_id,
                created=bool(channel_id),
                failure_reason=(
                    request.setup_failure_reason
                    or f"{request.kind}-media-setup-timeout"
                    if request is not None
                    else f"{self.runtime.kind}-media-setup-timeout"
                ),
                force_cleanup=(
                    request.force_cleanup_on_failure
                    if request is not None
                    else bool(
                        getattr(
                            self.runtime,
                            "force_cleanup_on_prepare_failure",
                            True,
                        )
                    )
                ),
            )
        except Exception:
            if request is not None:
                await self._abort_interrupted_setup(owner, session, channel_id or request.channel_id)
            return CallMediaSetupResult(
                request=request,
                channel_id=channel_id,
                created=bool(channel_id),
                failure_reason=(
                    (
                        (
                            request.setup_failure_reason
                            or f"{request.kind}-media-setup-failed"
                        )
                        if channel_id
                        else (
                            request.start_failure_reason
                            or f"{request.kind}-media-start-failed"
                        )
                    )
                    if request is not None
                    else f"{self.runtime.kind}-media-start-failed"
                ),
                force_cleanup=(
                    True
                    if request is not None
                    else bool(
                        getattr(
                            self.runtime,
                            "force_cleanup_on_prepare_failure",
                            True,
                        )
                    )
                ),
            )

    def is_aux_channel(
        self, channel: Mapping[str, Any], *, known_channel_ids: set[str]
    ) -> bool:
        return self.runtime.is_aux_channel(
            channel, known_channel_ids=known_channel_ids
        )

    async def handle_aux_channel(
        self, channel_id: str, channel: Mapping[str, Any]
    ) -> None:
        await self.runtime.handle_aux_channel(channel_id, channel)
