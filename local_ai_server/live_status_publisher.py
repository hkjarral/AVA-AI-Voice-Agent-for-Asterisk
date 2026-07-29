"""Best-effort publisher for Admin UI live-status updates."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping


ComponentMap = Mapping[str, Mapping[str, Any]]
ComponentBuilder = Callable[[], ComponentMap | Awaitable[ComponentMap]]


def live_status_component(
    *,
    state: str,
    summary: str,
    details: Mapping[str, Any] | None = None,
    metrics: Mapping[str, Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "summary": summary,
        "details": dict(details or {}),
        "metrics": dict(metrics or {}),
        "warnings": list(warnings or []),
        "errors": list(errors or []),
    }


@dataclass(frozen=True)
class LiveStatusPublisherConfig:
    source: str
    admin_url: str
    token: str
    interval_seconds: float = 10.0
    timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls, source: str) -> "LiveStatusPublisherConfig":
        port = (os.getenv("UVICORN_PORT") or "3003").strip() or "3003"
        admin_url = (
            os.getenv("LIVE_STATUS_ADMIN_URL")
            or os.getenv("ADMIN_UI_URL")
            or f"http://127.0.0.1:{port}"
        ).strip()
        token = (os.getenv("LIVE_STATUS_PUSH_TOKEN") or os.getenv("HEALTH_API_TOKEN") or "").strip()
        return cls(
            source=source,
            admin_url=admin_url.rstrip("/"),
            token=token,
            interval_seconds=_float_env("LIVE_STATUS_PUSH_INTERVAL_SECONDS", 10.0, minimum=2.0),
            timeout_seconds=_float_env("LIVE_STATUS_PUSH_TIMEOUT_SECONDS", 10.0, minimum=0.2),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.admin_url and self.token and self.source)

    @property
    def endpoint(self) -> str:
        return f"{self.admin_url}/api/system/live-status/publish"


def _float_env(name: str, default: float, *, minimum: float) -> float:
    try:
        value = float((os.getenv(name) or "").strip())
    except ValueError:
        value = default
    return max(minimum, value)


class LiveStatusPublisher:
    def __init__(
        self,
        config: LiveStatusPublisherConfig,
        *,
        logger: Any | None = None,
        post: Callable[[str, dict[str, Any], str, float], Any] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._logger = logger
        self._post = post or _post_json
        self._monotonic = monotonic
        self._closed = False
        self._publish_lock = asyncio.Lock()
        self._last_failure_log = 0.0

    @classmethod
    def from_env(cls, source: str, *, logger: Any | None = None) -> "LiveStatusPublisher":
        return cls(LiveStatusPublisherConfig.from_env(source), logger=logger)

    @property
    def enabled(self) -> bool:
        return self.config.enabled and not self._closed

    def publish_now(self, components: ComponentMap, *, name: str | None = None) -> asyncio.Task | None:
        if not self.enabled:
            return None
        task = asyncio.create_task(self.publish(components), name=name or f"live-status-publish-{self.config.source}")
        task.add_done_callback(self._log_task_exception)
        return task

    async def publish(self, components: ComponentMap) -> bool:
        if not self.enabled:
            return False
        payload = {"source": self.config.source, "components": dict(components)}
        async with self._publish_lock:
            try:
                await asyncio.to_thread(
                    self._post,
                    self.config.endpoint,
                    payload,
                    self.config.token,
                    self.config.timeout_seconds,
                )
                return True
            except Exception as exc:
                self._log_failure(exc)
                return False

    async def publish_loop(self, build_components: ComponentBuilder) -> None:
        if not self.enabled:
            return
        while not self._closed:
            try:
                components = build_components()
                if inspect.isawaitable(components):
                    components = await components
                await self.publish(components)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log_failure(exc)
            await asyncio.sleep(self.config.interval_seconds)

    async def close(self) -> None:
        self._closed = True

    def _log_task_exception(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            self._log_failure(exc)

    def _log_failure(self, exc: BaseException) -> None:
        if not self._logger:
            return
        now = self._monotonic()
        if now - self._last_failure_log < 60.0:
            return
        self._last_failure_log = now
        try:
            self._logger.debug("Live-status publish failed: %s", exc)
        except Exception:
            pass


def _post_json(endpoint: str, payload: dict[str, Any], token: str, timeout_seconds: float) -> None:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            if response.status >= 400:
                raise RuntimeError(f"HTTP {response.status}")
            response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
