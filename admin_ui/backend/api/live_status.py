"""Normalized live status endpoints for the dashboard.

Phase 0 intentionally uses existing Admin UI probes as the source of truth.
AI Engine and Local AI push publishers can feed the same hub later without
changing the browser contract.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from api import system as system_api
from services.status_hub import StatusHub, component


logger = logging.getLogger(__name__)
router = APIRouter()
publish_router = APIRouter()
status_hub = StatusHub()
_probe_task: asyncio.Task | None = None
_PUSH_COMPONENTS = {"ai_engine", "local_ai_server", "sessions"}
_PROBE_COMPONENTS = {"directories", "platform", "asterisk", "metrics"}


def _state_from_bool(ok: bool | None, *, degraded: bool = False) -> str:
    if ok is True:
        return "degraded" if degraded else "ready"
    if ok is False:
        return "unreachable"
    return "unknown"


def _exception_component(name: str, exc: BaseException) -> dict:
    return component(
        state="error",
        summary=f"{name} probe failed",
        errors=[f"{type(exc).__name__}: {exc}"],
    )


def _push_token() -> str:
    return (
        system_api._dotenv_value("LIVE_STATUS_PUSH_TOKEN")
        or system_api._dotenv_value("HEALTH_API_TOKEN")
        or os.getenv("LIVE_STATUS_PUSH_TOKEN")
        or os.getenv("HEALTH_API_TOKEN")
        or ""
    ).strip()


def _has_probe_components(snapshot: dict) -> bool:
    components = snapshot.get("components") or {}
    return _PROBE_COMPONENTS.issubset(components.keys())


def _require_push_auth(authorization: str | None) -> None:
    token = _push_token()
    if not token:
        raise HTTPException(status_code=503, detail="live-status push is not configured")
    expected = f"Bearer {token}"
    if authorization != expected:
        raise HTTPException(status_code=403, detail="invalid live-status push token")


def normalize_push_payload(payload: Dict[str, Any]) -> Dict[str, dict]:
    """Normalize service-pushed component updates into the hub contract."""
    raw_components = payload.get("components")
    if raw_components is None and payload.get("component"):
        raw_components = {payload["component"]: payload}
    if not isinstance(raw_components, dict) or not raw_components:
        raise HTTPException(status_code=400, detail="components is required")

    source = str(payload.get("source") or "push")
    updates: Dict[str, dict] = {}
    for name, raw in raw_components.items():
        if name not in _PUSH_COMPONENTS:
            raise HTTPException(status_code=400, detail=f"unsupported component: {name}")
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail=f"component {name} must be an object")
        state = str(raw.get("state") or "").strip()
        summary = str(raw.get("summary") or "").strip()
        if not state or not summary:
            raise HTTPException(status_code=400, detail=f"component {name} requires state and summary")
        updates[name] = component(
            state=state,
            summary=summary,
            source="push",
            details=raw.get("details") if isinstance(raw.get("details"), dict) else {},
            metrics=raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {},
            warnings=raw.get("warnings") if isinstance(raw.get("warnings"), list) else [],
            errors=raw.get("errors") if isinstance(raw.get("errors"), list) else [],
            updated_at=raw.get("updated_at") if isinstance(raw.get("updated_at"), str) else None,
        )
        updates[name]["publisher"] = source
    return updates


def normalize_probe_results(results: Dict[str, Any]) -> Dict[str, dict]:
    """Convert existing dashboard probe responses into one stable status model."""
    components: Dict[str, dict] = {}

    health = results.get("health")
    if isinstance(health, BaseException):
        components["ai_engine"] = _exception_component("AI Engine", health)
        components["local_ai_server"] = _exception_component("Local AI Server", health)
    else:
        health = health or {}
        ai = health.get("ai_engine") or {}
        ai_details = ai.get("details") or {}
        ai_connected = ai.get("status") == "connected"
        ai_health_status = str(ai_details.get("status") or "healthy").lower()
        providers = ai_details.get("providers") or {}
        provider_warnings = [
            f"{name}: {info.get('reason') or 'not ready'}"
            for name, info in providers.items()
            if isinstance(info, dict) and info.get("ready") is False
        ]
        ai_warnings = []
        if ai.get("warning"):
            ai_warnings.append(ai["warning"])
        ai_warnings.extend(provider_warnings)
        if ai_connected and ai_health_status not in {"healthy", "ok"}:
            ai_warnings.append(f"health status: {ai_health_status}")
        if ai_connected and ai_details.get("ari_connected") is False:
            ai_warnings.append("ARI disconnected")
        ai_degraded = ai_connected and (
            bool(provider_warnings)
            or ai_health_status not in {"healthy", "ok"}
            or ai_details.get("ari_connected") is False
        )
        components["ai_engine"] = component(
            state=_state_from_bool(ai_connected, degraded=ai_degraded),
            summary=(
                "AI Engine degraded"
                if ai_degraded
                else "AI Engine connected" if ai_connected else "AI Engine unreachable"
            ),
            details={
                "ari_connected": ai_details.get("ari_connected"),
                "audio_transport": ai_details.get("audio_transport"),
                "active_calls": ai_details.get("active_calls", 0),
                "active_sessions": ai_details.get("active_sessions", 0),
                "asterisk_channels": ai_details.get("asterisk_channels", 0),
                "providers": providers,
                "pipelines": ai_details.get("pipelines") or {},
                "probe": ai.get("probe") or {},
            },
            warnings=ai_warnings,
            errors=[] if ai_connected else [ai_details.get("error") or "AI Engine health endpoint unreachable"],
        )

        local = health.get("local_ai_server") or {}
        local_details = local.get("details") or {}
        local_connected = local.get("status") == "connected"
        local_config = local_details.get("config") or {}
        startup_errors = local_config.get("startup_errors") or {}
        models = local_details.get("models") or {}
        model_count = sum(1 for value in models.values() if isinstance(value, dict))
        loaded_count = sum(1 for value in models.values() if isinstance(value, dict) and value.get("loaded") is True)
        local_degraded = (
            bool(local_config.get("degraded"))
            or bool(startup_errors)
            or (model_count > 0 and loaded_count < model_count)
        )
        local_warnings = []
        if local.get("warning"):
            local_warnings.append(local["warning"])
        local_warnings.extend([f"{key}: {value}" for key, value in startup_errors.items()])
        components["local_ai_server"] = component(
            state=_state_from_bool(local_connected, degraded=local_degraded),
            summary=(
                f"Local AI connected, {loaded_count}/{model_count} models loaded"
                if local_connected
                else "Local AI unreachable"
            ),
            details={
                "models": models,
                "runtime_mode": local_config.get("runtime_mode"),
                "degraded": local_config.get("degraded"),
                "gpu": local_details.get("gpu") or {},
                "probe": local.get("probe") or {},
            },
            warnings=local_warnings,
            errors=[] if local_connected else [local_details.get("error") or "Local AI status WebSocket unreachable"],
        )

    sessions = results.get("sessions")
    if isinstance(sessions, BaseException):
        components["sessions"] = _exception_component("Sessions", sessions)
    else:
        sessions = sessions or {}
        reachable = sessions.get("reachable")
        components["sessions"] = component(
            state=_state_from_bool(reachable),
            summary=f"{sessions.get('active_calls', 0)} active calls",
            details={
                "active_calls": sessions.get("active_calls", 0),
                "sessions": sessions.get("sessions") or [],
                "reachable": reachable,
            },
        )

    directories = results.get("directories")
    if isinstance(directories, BaseException):
        components["directories"] = _exception_component("Audio directories", directories)
    else:
        directories = directories or {}
        overall = directories.get("overall")
        dir_state = "ready" if overall == "healthy" else "degraded" if overall == "warning" else "error"
        errors = [
            f"{name}: {check.get('message')}"
            for name, check in (directories.get("checks") or {}).items()
            if isinstance(check, dict) and check.get("status") == "error"
        ]
        warnings = [
            f"{name}: {check.get('message')}"
            for name, check in (directories.get("checks") or {}).items()
            if isinstance(check, dict) and check.get("status") == "warning"
        ]
        components["directories"] = component(
            state=dir_state,
            summary=f"Audio directories {overall or 'unknown'}",
            details=directories,
            warnings=warnings,
            errors=errors,
        )

    platform = results.get("platform")
    if isinstance(platform, BaseException):
        components["platform"] = _exception_component("Platform", platform)
    else:
        platform = platform or {}
        summary = platform.get("summary") or {}
        checks = platform.get("checks") or []
        ready = summary.get("ready")
        has_warnings = bool(summary.get("warnings"))
        has_errors = bool(summary.get("errors"))
        platform_state = "ready"
        if ready is not True:
            platform_state = "error"
        elif has_warnings or has_errors:
            platform_state = "degraded"
        components["platform"] = component(
            state=platform_state,
            summary=f"{summary.get('passed', 0)}/{summary.get('total_checks', 0)} platform checks passed",
            details=platform,
            warnings=[c.get("message") for c in checks if isinstance(c, dict) and c.get("status") == "warning"],
            errors=[c.get("message") for c in checks if isinstance(c, dict) and c.get("status") == "error"],
        )

    asterisk = results.get("asterisk")
    if isinstance(asterisk, BaseException):
        components["asterisk"] = _exception_component("Asterisk", asterisk)
    else:
        asterisk = asterisk or {}
        live = asterisk.get("live") or {}
        reachable = live.get("ari_reachable")
        app_registered = live.get("app_registered")
        degraded = reachable is True and app_registered is False
        components["asterisk"] = component(
            state=_state_from_bool(reachable, degraded=degraded),
            summary="Asterisk ARI reachable" if reachable else "Asterisk ARI unreachable",
            details=asterisk,
            warnings=[] if app_registered or reachable is not True else ["Stasis app is not registered"],
            errors=[] if reachable else ["Asterisk ARI is not reachable"],
        )

    metrics = results.get("metrics")
    if isinstance(metrics, BaseException):
        components["metrics"] = _exception_component("System metrics", metrics)
    else:
        components["metrics"] = component(
            state="ready",
            summary="System metrics available",
            metrics=metrics or {},
        )

    return components


async def probe_live_status() -> Dict[str, Any]:
    names = ["health", "sessions", "directories", "platform", "asterisk", "metrics"]
    values = await asyncio.gather(
        system_api.get_system_health(),
        system_api.get_active_sessions(),
        system_api.get_directory_health(),
        system_api.get_platform(),
        system_api.asterisk_status(),
        system_api.get_system_metrics(),
        return_exceptions=True,
    )
    return dict(zip(names, values))


async def refresh_live_status() -> dict:
    results = await probe_live_status()
    return await status_hub.upsert_components(normalize_probe_results(results))


def _poll_interval_seconds() -> float:
    raw = (
        system_api._dotenv_value("LIVE_STATUS_POLL_INTERVAL_SECONDS")
        or os.getenv("LIVE_STATUS_POLL_INTERVAL_SECONDS")
        or "30"
    ).strip()
    try:
        return max(2.0, float(raw))
    except ValueError:
        return 30.0


def _initial_probe_timeout_seconds() -> float:
    raw = (
        system_api._dotenv_value("LIVE_STATUS_INITIAL_PROBE_TIMEOUT_SECONDS")
        or os.getenv("LIVE_STATUS_INITIAL_PROBE_TIMEOUT_SECONDS")
        or "2"
    ).strip()
    try:
        return max(0.1, float(raw))
    except ValueError:
        return 2.0


def _ensure_probe_loop() -> None:
    global _probe_task
    if _probe_task and not _probe_task.done():
        return
    _probe_task = asyncio.create_task(_probe_loop(), name="live-status-probe-loop")


async def _probe_loop() -> None:
    while True:
        try:
            await refresh_live_status()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("live-status probe refresh failed: %s", exc)
        interval = _poll_interval_seconds()
        await asyncio.sleep(interval)


def encode_sse(event: str, data: dict, event_id: int | None = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True)
    for line in payload.splitlines() or [""]:
        lines.append(f"data: {line}")
    lines.append("")
    return "\n".join(lines) + "\n"


@router.get("/live-status")
async def get_live_status():
    _ensure_probe_loop()
    snapshot = await status_hub.snapshot()
    if snapshot.get("components") and _has_probe_components(snapshot):
        return snapshot
    try:
        return await asyncio.wait_for(refresh_live_status(), timeout=_initial_probe_timeout_seconds())
    except (asyncio.TimeoutError, Exception) as exc:
        logger.debug("live-status initial probe fallback failed: %s", exc)
        return await status_hub.snapshot()


@publish_router.post("/live-status/publish")
async def publish_live_status(payload: Dict[str, Any], authorization: str | None = Header(default=None)):
    _require_push_auth(authorization)
    snapshot = await status_hub.upsert_components(normalize_push_payload(payload))
    return {"ok": True, "event_id": snapshot["event_id"], "summary": snapshot["summary"]}


@router.get("/live-status/stream")
async def stream_live_status(request: Request):
    _ensure_probe_loop()
    queue = await status_hub.subscribe()

    async def events():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield encode_sse(event["event"], event["data"], event.get("id"))
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            await status_hub.unsubscribe(queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
