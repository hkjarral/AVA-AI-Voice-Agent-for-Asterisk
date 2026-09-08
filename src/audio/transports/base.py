"""Small, transport-neutral types used by media adapters.

This deliberately doesn't pull the existing RTP or AudioSocket implementations
into a new hierarchy.  It gives callers a stable description of negotiated
media while the legacy adapters are migrated independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol


class CompletionQuality(str, Enum):
    """How precisely a transport can confirm that queued output completed.

    ``QUIET_TAIL`` is the legacy observation model: completion is inferred from
    local queue state plus a bounded quiet interval. ``CORRELATED_BOUNDARY``
    means the transport can acknowledge a generation/segment boundary that was
    ordered after the segment's audio.
    """

    QUIET_TAIL = "quiet_tail"
    CORRELATED_BOUNDARY = "correlated_boundary"


@dataclass(frozen=True)
class TransportCapabilities:
    """Stable behavior advertised by a selected media transport runtime."""

    completion_quality: CompletionQuality
    supports_remote_flush: bool = False
    supports_remote_flow_control: bool = False
    supports_per_call_codec: bool = False


@dataclass(frozen=True)
class CallMediaRequest:
    """Transport-prepared ARI channel request and readiness policy."""

    kind: str
    call_id: str
    codec: str
    sample_rate: int
    operation: str
    ari_params: Mapping[str, Any]
    ari_data: Mapping[str, Any] | None = None
    channel_id: str | None = None
    correlation_id: str | None = None
    attachment: str = "immediate"
    readiness: str = "bridge"
    fail_without_bridge: bool = True
    force_cleanup_on_failure: bool = False
    start_failure_reason: str | None = None
    attach_failure_reason: str | None = None
    setup_failure_reason: str | None = None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class CallMediaSetupResult:
    """Outcome consumed by the engine's caller/provider lifecycle owner."""

    request: CallMediaRequest | None
    channel_id: str | None = None
    created: bool = False
    ready: bool = False
    failure_reason: str | None = None
    force_cleanup: bool = False


class SelectedTransportRuntime(Protocol):
    """Thin selected-listener lifecycle; wire protocols remain independent."""

    kind: str
    server: Any
    capabilities: TransportCapabilities
    force_cleanup_on_prepare_failure: bool

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def close_call(
        self, call_id: str, *, connection_id: str | None = None
    ) -> None: ...

    async def prepare_call(self, session: Any) -> CallMediaRequest: ...

    async def create_call(
        self, owner: Any, session: Any, request: CallMediaRequest
    ) -> str | None: ...

    async def attach_call(
        self, owner: Any, session: Any, channel_id: str
    ) -> bool: ...

    async def wait_call_ready(
        self, owner: Any, session: Any, request: CallMediaRequest
    ) -> Any: ...

    async def finalize_call_ready(
        self, owner: Any, session: Any, request: CallMediaRequest, binding: Any
    ) -> bool: ...

    def is_aux_channel(
        self, channel: Mapping[str, Any], *, known_channel_ids: set[str]
    ) -> bool: ...

    async def handle_aux_channel(
        self, channel_id: str, channel: Mapping[str, Any]
    ) -> None: ...

    def ready(self, *, asterisk_version: str | None = None) -> bool: ...

    def health(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class MediaBinding:
    """A call's immutable negotiated wire-media parameters."""

    call_id: str
    channel_id: str
    codec: str
    sample_rate: int
    optimal_frame_size: int
    ptime: int
    connection_id: str
    state: str = "ready"
