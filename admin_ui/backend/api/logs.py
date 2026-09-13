import asyncio
import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import docker
from fastapi import APIRouter, HTTPException, Query

from api.log_events import LogEvent, parse_log_line, should_hide_payload

router = APIRouter()
logger = logging.getLogger(__name__)

_DOCKER_LOG_TIMEOUT_SECONDS = 10


def _read_container_logs_sync(
    container_name: str,
    *,
    tail: Optional[int],
    since: Optional[int] = None,
    until: Optional[int] = None,
) -> Tuple[bytes, str, str]:
    """Read Docker logs without blocking FastAPI's asyncio event loop."""
    client = docker.from_env(timeout=_DOCKER_LOG_TIMEOUT_SECONDS)
    try:
        containers = client.containers.list(all=True, filters={"name": container_name})
        if not containers:
            try:
                container = client.containers.get(container_name)
            except docker.errors.NotFound as exc:
                raise HTTPException(
                    status_code=404,
                    detail=f"Container '{container_name}' not found",
                ) from exc
        else:
            container = containers[0]

        kwargs: Dict[str, Any] = {"tail": tail}
        if since is not None:
            kwargs["since"] = since
        if until is not None:
            kwargs["until"] = until
        if since is not None or until is not None:
            kwargs["timestamps"] = False

        logs = container.logs(**kwargs) or b""
        return logs, container.id, container.name
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.debug("Failed to close Docker client after log read", exc_info=True)


def _parse_iso_to_epoch_seconds(value: Optional[str], *, round_up: bool = False) -> Optional[int]:
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ts = dt.timestamp()
        # Docker expects second-resolution integers; for `until`, round up so we don't
        # accidentally exclude events that share the same second as the provided ISO.
        return int(math.ceil(ts)) if round_up else int(math.floor(ts))
    except Exception:
        return None


def _split_csv(values: Optional[Sequence[str]]) -> List[str]:
    out: List[str] = []
    for v in values or []:
        if not v:
            continue
        parts = [p.strip() for p in str(v).split(",")]
        out.extend([p for p in parts if p])
    return out


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _resolve_call_history_window(
    call_id: str, pad_seconds: int = 10
) -> Tuple[Optional[int], Optional[int], Optional[Dict[str, Any]]]:
    """
    Best-effort: resolve a call's start/end timestamps from Call History so we can
    pull the right slice of Docker logs even when `tail` would miss it.
    """
    try:
        from src.core.call_history import get_call_history_store
    except Exception:
        return None, None, None

    try:
        store = get_call_history_store()
        record = await store.get_by_call_id(call_id)
        if not record:
            return None, None, None

        start = _ensure_utc(record.start_time)
        end = _ensure_utc(record.end_time) or datetime.now(timezone.utc)
        pad = timedelta(seconds=max(0, int(pad_seconds)))
        since_epoch = int((start - pad).timestamp()) if start else None
        until_epoch = int(math.ceil((end + pad).timestamp())) if end else None

        call_meta: Dict[str, Any] = {
            "call_id": record.call_id,
            "caller_number": record.caller_number,
            "caller_name": record.caller_name,
            "start_time": start.isoformat() if start else None,
            "end_time": end.isoformat() if end else None,
            "duration_seconds": record.duration_seconds,
            "provider_name": record.provider_name,
            "pipeline_name": record.pipeline_name,
            "context_name": record.context_name,
            "outcome": record.outcome,
            "error_message": record.error_message,
            "barge_in_count": record.barge_in_count,
            "avg_turn_latency_ms": record.avg_turn_latency_ms,
            "total_turns": record.total_turns,
        }
        return since_epoch, until_epoch, call_meta
    except Exception:
        return None, None, None


def _extract_ids_from_kv(kv: Dict[str, str]) -> List[str]:
    ids: List[str] = []
    for k in ("call_id", "channel_id", "caller_channel_id", "local_channel_id", "external_media_id"):
        v = (kv.get(k) or "").strip()
        if v and v.lower() not in ("none", "null"):
            ids.append(v)
    return ids


def _event_matches_call(event: LogEvent, kv: Dict[str, str], wanted_ids: set, wanted_bridge_ids: set) -> bool:
    if event.call_id and event.call_id in wanted_ids:
        return True
    for v in _extract_ids_from_kv(kv):
        if v in wanted_ids:
            return True
    bridge_id = (kv.get("bridge_id") or "").strip()
    if bridge_id and bridge_id in wanted_bridge_ids:
        return True
    return False


def _compute_related_ids(parsed: List[Tuple[LogEvent, Dict[str, str]]], seed_call_id: str) -> Tuple[List[str], List[str]]:
    """
    Expand a call filter to include related channel ids (ExternalMedia / Local channels)
    using only information present in ai-engine logs.
    """
    wanted_ids = {seed_call_id}
    wanted_bridge_ids: set = set()

    changed = True
    while changed:
        changed = False
        for event, kv in parsed:
            ids_in_line = set(_extract_ids_from_kv(kv))
            meta = event.meta or {}
            if meta.get("ari_channel_id"):
                ids_in_line.add(meta.get("ari_channel_id", ""))
            if meta.get("external_media_id"):
                ids_in_line.add(meta.get("external_media_id", ""))
            ids_in_line.discard("")

            intersects = bool(ids_in_line & wanted_ids) or (event.call_id in wanted_ids if event.call_id else False)
            if not intersects:
                continue

            before = len(wanted_ids)
            wanted_ids.update(ids_in_line)
            if len(wanted_ids) != before:
                changed = True

            bridge_id = (kv.get("bridge_id") or "").strip()
            if bridge_id and bridge_id not in wanted_bridge_ids:
                wanted_bridge_ids.add(bridge_id)
                changed = True

    return sorted(wanted_ids), sorted(wanted_bridge_ids)


@router.get("/{container_name}")
async def get_container_logs(
    container_name: str,
    tail: int = 100,
    levels: Optional[List[str]] = Query(default=None),
    q: Optional[str] = None,
):
    """
    Fetch logs from a specific container.
    """
    try:
        wanted_levels = {v.strip().lower() for v in _split_csv(levels)} if levels else set()
        q_norm = (q or "").strip().lower() or None

        logs_bytes, container_id, resolved_name = await asyncio.to_thread(
            _read_container_logs_sync,
            container_name,
            tail=tail,
        )
        logs = logs_bytes.decode("utf-8", errors="replace")
        if not (wanted_levels or q_norm):
            return {"logs": logs, "container_id": container_id, "name": resolved_name}

        out_lines: List[str] = []
        for line in logs.splitlines():
            parsed = parse_log_line(line)
            # If we can't parse a line, keep it only when no level filter is specified.
            if not parsed:
                if not wanted_levels and (not q_norm or q_norm in line.lower()):
                    out_lines.append(line)
                continue
            event, _kv = parsed
            if wanted_levels and event.level not in wanted_levels:
                continue
            if q_norm and q_norm not in (event.raw or "").lower() and q_norm not in (event.msg or "").lower():
                continue
            # Preserve original ANSI formatting for the Raw Logs view.
            out_lines.append(line)

        return {"logs": "\n".join(out_lines), "container_id": container_id, "name": resolved_name}

    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Docker log read failed for %s", container_name, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{container_name}/events")
async def get_container_log_events(
    container_name: str,
    call_id: Optional[str] = None,
    q: Optional[str] = None,
    levels: Optional[List[str]] = Query(default=None),
    categories: Optional[List[str]] = Query(default=None),
    hide_payloads: bool = True,
    since: Optional[str] = None,
    until: Optional[str] = None,
    since_seconds_ago: Optional[int] = None,
    expand_related: bool = True,
    call_window_pad_seconds: int = 10,
    limit: int = 500,
) -> Dict[str, Any]:
    """
    Fetch parsed, filterable log events from a container.

    This is designed for the Admin UI "Events" view to enable fast troubleshooting.
    """
    try:
        q_norm = (q or "").strip().lower() or None
        call_id_norm = (call_id or "").strip() or None

        wanted_levels = {v.strip().lower() for v in _split_csv(levels)} if levels else set()
        wanted_categories = {v.strip().lower() for v in _split_csv(categories)} if categories else set()

        since_epoch = _parse_iso_to_epoch_seconds(since, round_up=False)
        until_epoch = _parse_iso_to_epoch_seconds(until, round_up=True)
        window_source = "query" if (since_epoch or until_epoch) else "tail"
        call_meta: Optional[Dict[str, Any]] = None

        # If user filtered by call_id but did not provide a time window, try to resolve
        # the call's start/end from call history to avoid tail-based truncation.
        if call_id_norm and since_epoch is None and until_epoch is None and not since_seconds_ago:
            ch_since, ch_until, ch_meta = await _resolve_call_history_window(call_id_norm, pad_seconds=call_window_pad_seconds)
            if ch_since is not None or ch_until is not None:
                since_epoch, until_epoch = ch_since, ch_until
                call_meta = ch_meta
                window_source = "call_history"

        if since_epoch is None and since_seconds_ago and since_seconds_ago > 0:
            since_epoch = int(datetime.now(timezone.utc).timestamp()) - int(since_seconds_ago)
            window_source = "relative"

        # Keep volume bounded: use time-window when provided, otherwise tail.
        logs_bytes, container_id, resolved_name = await asyncio.to_thread(
            _read_container_logs_sync,
            container_name,
            since=since_epoch,
            until=until_epoch,
            tail=None if (since_epoch or until_epoch) else 2000,
        )
        logs_text = (logs_bytes or b"").decode("utf-8", errors="replace")

        parsed_events: List[Tuple[LogEvent, Dict[str, str]]] = []
        for line in logs_text.splitlines():
            parsed = parse_log_line(line)
            if not parsed:
                continue
            parsed_events.append(parsed)

        related_ids: List[str] = []
        related_bridge_ids: List[str] = []
        if call_id_norm and expand_related:
            related_ids, related_bridge_ids = _compute_related_ids(parsed_events, call_id_norm)

        wanted_ids = set(related_ids or ([call_id_norm] if call_id_norm else []))
        wanted_bridge_ids = set(related_bridge_ids or [])

        events: List[LogEvent] = []
        for event, kv in parsed_events:
            if hide_payloads and should_hide_payload(event):
                continue
            if call_id_norm and not _event_matches_call(event, kv, wanted_ids, wanted_bridge_ids):
                continue
            if wanted_levels and event.level not in wanted_levels:
                continue
            # If a focused category filter is requested, still keep warning/error so
            # troubleshooting keeps critical context.
            if wanted_categories and event.category not in wanted_categories and event.level not in ("warning", "error"):
                continue
            if q_norm and q_norm not in (event.raw or "").lower() and q_norm not in (event.msg or "").lower():
                continue
            events.append(event)

        # Sort by timestamp when available, otherwise keep input order
        events_sorted = sorted(
            events,
            key=lambda e: (e.ts is None, e.ts or datetime.min.replace(tzinfo=timezone.utc)),
        )
        if limit and limit > 0:
            lim = int(limit)
            # For call-centric views, prefer a balanced slice (start + end) so users
            # see both setup and teardown without extra paging.
            if call_id_norm:
                if len(events_sorted) > lim:
                    head_n = max(1, lim // 2)
                    tail_n = lim - head_n
                    head = events_sorted[:head_n]
                    tail = events_sorted[-tail_n:] if tail_n > 0 else []
                    seen = set()
                    merged: List[LogEvent] = []
                    for e in head + tail:
                        k = (e.ts, e.level, e.category, e.msg, e.call_id, e.component)
                        if k in seen:
                            continue
                        seen.add(k)
                        merged.append(e)
                    events_sorted = merged
                else:
                    events_sorted = events_sorted
            else:
                events_sorted = events_sorted[-lim:]

        return {
            "events": [e.to_dict() for e in events_sorted],
            "container_id": container_id,
            "name": resolved_name,
            "call": call_meta,
            "window": {
                "source": window_source,
                "since": datetime.fromtimestamp(since_epoch, tz=timezone.utc).isoformat() if since_epoch else None,
                "until": datetime.fromtimestamp(until_epoch, tz=timezone.utc).isoformat() if until_epoch else None,
            },
            "related_ids": related_ids,
            "related_bridge_ids": related_bridge_ids,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Docker log event read failed for %s", container_name, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
