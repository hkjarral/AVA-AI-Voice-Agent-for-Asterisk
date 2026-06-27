"""In-memory live-status registry for the Admin UI backend.

The hub stores normalized component status snapshots and fans updates out to
SSE clients. It deliberately has no dependency on FastAPI so the state machine
can be tested directly.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional


Component = Dict[str, Any]
Snapshot = Dict[str, Any]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_monotonic() -> float:
    import time

    return time.monotonic()


def _overall_state(components: Dict[str, Component]) -> str:
    states = {str(component.get("state") or "unknown") for component in components.values()}
    if not states:
        return "unknown"
    if "unreachable" in states or "error" in states:
        return "error"
    if "degraded" in states or "stale" in states or "warning" in states:
        return "degraded"
    if states == {"ready"}:
        return "ready"
    if "unknown" in states:
        return "unknown"
    return "degraded"


class StatusHub:
    """Small async-safe status registry with bounded subscriber queues."""

    def __init__(
        self,
        *,
        stale_after_seconds: float = 15.0,
        unreachable_after_seconds: float = 45.0,
        queue_size: int = 8,
        monotonic_clock: Callable[[], float] = _default_monotonic,
        wall_clock: Callable[[], str] = utc_now_iso,
    ) -> None:
        self.stale_after_seconds = stale_after_seconds
        self.unreachable_after_seconds = unreachable_after_seconds
        self.queue_size = queue_size
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._components: Dict[str, Component] = {}
        self._observed_at: Dict[str, float] = {}
        self._event_id = 0
        self._lock = asyncio.Lock()
        self._subscribers: set[asyncio.Queue[dict]] = set()

    async def upsert_components(self, components: Dict[str, Component]) -> Snapshot:
        """Merge component updates and broadcast the resulting snapshot."""
        now_mono = self._monotonic_clock()
        now_wall = self._wall_clock()
        async with self._lock:
            for name, component in components.items():
                normalized = copy.deepcopy(component)
                normalized.setdefault("updated_at", now_wall)
                normalized.setdefault("source", "probe")
                if self._should_keep_existing_locked(name, normalized, now_mono):
                    continue
                self._components[name] = normalized
                self._observed_at[name] = now_mono
            self._event_id += 1
            snapshot = self._build_snapshot_locked(now_mono, now_wall)

        await self._broadcast({"event": "snapshot", "id": snapshot["event_id"], "data": snapshot})
        return snapshot

    def _should_keep_existing_locked(self, name: str, incoming: Component, now_mono: float) -> bool:
        existing = self._components.get(name)
        if not existing:
            return False
        incoming_source = str(incoming.get("source") or "probe")
        existing_source = str(existing.get("source") or "probe")
        existing_age = max(0.0, now_mono - self._observed_at.get(name, now_mono))
        return (
            existing_source == "push"
            and incoming_source == "probe"
            and existing_age < self.stale_after_seconds
        )

    async def snapshot(self) -> Snapshot:
        now_mono = self._monotonic_clock()
        now_wall = self._wall_clock()
        async with self._lock:
            return self._build_snapshot_locked(now_mono, now_wall)

    async def subscribe(self) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=self.queue_size)
        async with self._lock:
            self._subscribers.add(queue)
            snapshot = self._build_snapshot_locked(self._monotonic_clock(), self._wall_clock())
        await self._put_latest(queue, {"event": "snapshot", "id": snapshot["event_id"], "data": snapshot})
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    def _build_snapshot_locked(self, now_mono: float, now_wall: str) -> Snapshot:
        components: Dict[str, Component] = {}
        for name, component in self._components.items():
            normalized = copy.deepcopy(component)
            age = max(0.0, now_mono - self._observed_at.get(name, now_mono))
            normalized["age_seconds"] = round(age, 3)
            if age >= self.unreachable_after_seconds:
                normalized["freshness"] = "expired"
                if normalized.get("state") not in {"disabled", "unknown"}:
                    normalized["state"] = "unreachable"
            elif age >= self.stale_after_seconds:
                normalized["freshness"] = "stale"
            else:
                normalized["freshness"] = "fresh"
            components[name] = normalized

        snapshot: Snapshot = {
            "version": 1,
            "event_id": self._event_id,
            "generated_at": now_wall,
            "summary": {
                "state": _overall_state(components),
                "component_count": len(components),
            },
            "components": components,
        }
        # Convenience aliases for the frontend migration. The canonical data
        # remains under `components`.
        snapshot.update(components)
        return snapshot

    async def _broadcast(self, event: dict) -> None:
        subscribers = list(self._subscribers)
        for queue in subscribers:
            await self._put_latest(queue, event)

    async def _put_latest(self, queue: asyncio.Queue[dict], event: dict) -> None:
        while queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        queue.put_nowait(copy.deepcopy(event))


def component(
    *,
    state: str,
    summary: str,
    source: str = "probe",
    details: Optional[dict] = None,
    metrics: Optional[dict] = None,
    warnings: Optional[list] = None,
    errors: Optional[list] = None,
    updated_at: Optional[str] = None,
) -> Component:
    return {
        "state": state,
        "summary": summary,
        "source": source,
        "updated_at": updated_at or utc_now_iso(),
        "details": details or {},
        "metrics": metrics or {},
        "warnings": warnings or [],
        "errors": errors or [],
    }
