"""
Call History API endpoints (Milestone 21).

Provides REST API for viewing, searching, and managing call history records.
"""

import csv
import io
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from zoneinfo import ZoneInfo

# Add project root to path for imports
project_root = os.environ.get("PROJECT_ROOT", "/app/project")
if project_root not in sys.path:
    sys.path.insert(0, project_root)

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_server_timezone():
    tz_name = (os.getenv("TZ") or "").strip() or "UTC"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return timezone.utc


_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_datetime_param(value: Optional[str], *, end_of_day_if_date_only: bool) -> Optional[datetime]:
    """
    Parse query datetime params.

    Supports either:
      - Date-only: YYYY-MM-DD (interpreted in server TZ, converted to UTC)
      - ISO datetime: with optional timezone offset/Z

    Returns a timezone-aware UTC datetime suitable for comparing to stored UTC timestamps.
    """
    if not value:
        return None
    value = value.strip()
    if not value:
        return None

    server_tz = _get_server_timezone()

    if _DATE_ONLY_RE.match(value):
        year, month, day = (int(p) for p in value.split("-"))
        if end_of_day_if_date_only:
            dt = datetime(year, month, day, 23, 59, 59, 999999, tzinfo=server_tz)
        else:
            dt = datetime(year, month, day, 0, 0, 0, tzinfo=server_tz)
        return dt.astimezone(timezone.utc)

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {value}")

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=server_tz)
    return dt.astimezone(timezone.utc)


class CallRecordSummaryResponse(BaseModel):
    """Summary response model for list views (excludes transcript/tool payloads)."""
    id: str
    call_id: str
    caller_number: Optional[str] = None
    caller_name: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_seconds: float = 0.0
    provider_name: str = "unknown"
    pipeline_name: Optional[str] = None
    context_name: Optional[str] = None
    outcome: str = "completed"
    error_message: Optional[str] = None
    avg_turn_latency_ms: float = 0.0
    total_turns: int = 0
    barge_in_count: int = 0
    created_at: Optional[str] = None


class CallRecordResponse(BaseModel):
    """Response model for a call record."""
    id: str
    call_id: str
    caller_number: Optional[str] = None
    caller_name: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_seconds: float = 0.0
    provider_name: str = "unknown"
    pipeline_name: Optional[str] = None
    pipeline_components: dict = {}
    context_name: Optional[str] = None
    conversation_history: list = []
    outcome: str = "completed"
    transfer_destination: Optional[str] = None
    error_message: Optional[str] = None
    tool_calls: list = []
    pre_call_tool_calls: list = []
    post_call_tool_calls: list = []
    avg_turn_latency_ms: float = 0.0
    max_turn_latency_ms: float = 0.0
    total_turns: int = 0
    caller_audio_format: str = "ulaw"
    codec_alignment_ok: bool = True
    barge_in_count: int = 0
    created_at: Optional[str] = None


class CallListResponse(BaseModel):
    """Response model for paginated call list."""
    calls: List[CallRecordSummaryResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class CallStatsResponse(BaseModel):
    """Response model for call statistics."""
    total_calls: int = 0
    avg_duration_seconds: float = 0.0
    max_duration_seconds: float = 0.0
    min_duration_seconds: float = 0.0
    total_duration_seconds: float = 0.0
    avg_latency_ms: float = 0.0
    total_turns: int = 0
    total_barge_ins: int = 0
    outcomes: dict = {}
    providers: dict = {}
    pipelines: dict = {}
    contexts: dict = {}
    calls_per_day: list = []
    top_callers: list = []
    calls_with_tools: int = 0
    top_tools: dict = {}
    active_calls: int = 0


class FilterOptionsResponse(BaseModel):
    """Response model for filter dropdown options."""
    providers: List[str] = []
    pipelines: List[str] = []
    contexts: List[str] = []
    outcomes: List[str] = []


class ProviderHealthStatus(BaseModel):
    """Health status for a single provider."""
    status: str
    total: int
    failures: int
    summary: str


class ProviderHealthResponse(BaseModel):
    """Response model for the /providers/health endpoint."""
    providers: Dict[str, ProviderHealthStatus]


def _get_call_history_store():
    """Get the call history store instance."""
    try:
        from src.core.call_history import get_call_history_store
        return get_call_history_store()
    except ImportError as e:
        logger.error(f"Failed to import call_history module: {e}")
        raise HTTPException(status_code=500, detail="Call history module not available")


def _normalize_tool_calls(tool_calls: list) -> list:
    """Normalize tool call records for UI consumption (params as object when possible)."""
    normalized: list = []
    for item in (tool_calls or []):
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        params = entry.get("params")
        if isinstance(params, str):
            try:
                parsed = json.loads(params)
                entry["params"] = parsed
            except Exception:
                pass
        normalized.append(entry)
    return normalized


def _normalize_phase_tool_calls(entries: list, phase: str) -> list:
    """
    Normalize pre-call / post-call tool execution entries for UI consumption.

    Ensures every entry has a ``phase`` field set (older rows might omit it).
    Filters non-dict entries defensively. Does NOT mutate the input.
    """
    normalized: list = []
    for item in (entries or []):
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        if not entry.get("phase"):
            entry["phase"] = phase
        normalized.append(entry)
    return normalized


def _record_to_response(record) -> CallRecordResponse:
    """Convert a CallRecord to a response model."""
    return CallRecordResponse(
        id=record.id,
        call_id=record.call_id,
        caller_number=record.caller_number,
        caller_name=record.caller_name,
        start_time=record.start_time.isoformat() if record.start_time else None,
        end_time=record.end_time.isoformat() if record.end_time else None,
        duration_seconds=record.duration_seconds,
        provider_name=record.provider_name,
        pipeline_name=record.pipeline_name,
        pipeline_components=record.pipeline_components or {},
        context_name=record.context_name,
        conversation_history=record.conversation_history or [],
        outcome=record.outcome,
        transfer_destination=record.transfer_destination,
        error_message=record.error_message,
        tool_calls=_normalize_tool_calls(record.tool_calls or []),
        pre_call_tool_calls=_normalize_phase_tool_calls(
            getattr(record, "pre_call_tool_calls", None) or [], "pre_call"
        ),
        post_call_tool_calls=_normalize_phase_tool_calls(
            getattr(record, "post_call_tool_calls", None) or [], "post_call"
        ),
        avg_turn_latency_ms=record.avg_turn_latency_ms,
        max_turn_latency_ms=record.max_turn_latency_ms,
        total_turns=record.total_turns,
        caller_audio_format=record.caller_audio_format,
        codec_alignment_ok=record.codec_alignment_ok,
        barge_in_count=record.barge_in_count,
        created_at=record.created_at.isoformat() if record.created_at else None,
    )


def _record_to_summary_response(record) -> CallRecordSummaryResponse:
    """Convert a CallRecord to a summary response model."""
    return CallRecordSummaryResponse(
        id=record.id,
        call_id=record.call_id,
        caller_number=record.caller_number,
        caller_name=record.caller_name,
        start_time=record.start_time.isoformat() if record.start_time else None,
        end_time=record.end_time.isoformat() if record.end_time else None,
        duration_seconds=record.duration_seconds,
        provider_name=record.provider_name,
        pipeline_name=record.pipeline_name,
        context_name=record.context_name,
        outcome=record.outcome,
        error_message=record.error_message,
        avg_turn_latency_ms=record.avg_turn_latency_ms,
        total_turns=record.total_turns,
        barge_in_count=record.barge_in_count,
        created_at=record.created_at.isoformat() if record.created_at else None,
    )


@router.get("/providers/health", response_model=ProviderHealthResponse)
async def get_providers_health():
    """
    Aggregate call outcomes per provider from the last 24 hours.

    Returns a map of provider name -> health status.
    Status values: healthy, degraded, error, no_data.
    """
    store = _get_call_history_store()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    provider_stats: dict[str, dict] = {}
    page_size = 1000
    offset = 0
    while True:
        records = await store.list(
            limit=page_size,
            offset=offset,
            start_date=cutoff,
            include_details=False,
        )
        if not records:
            break
        for r in records:
            # Normalize to lowercase so backend keys match frontend YAML config keys
            name = (r.provider_name or "unknown").lower()
            if name not in provider_stats:
                provider_stats[name] = {"total": 0, "succeeded": 0, "failed": 0}
            provider_stats[name]["total"] += 1
            # Valid CallRecord.outcome values: completed, transferred, error, abandoned
            if r.outcome in ("error", "abandoned"):
                provider_stats[name]["failed"] += 1
            else:
                provider_stats[name]["succeeded"] += 1
        if len(records) < page_size:
            break
        offset += page_size

    result: dict[str, ProviderHealthStatus] = {}
    for name, stats in provider_stats.items():
        total = stats["total"]
        succeeded = stats["succeeded"]
        failed = stats["failed"]
        if total == 0:
            status = "no_data"
        elif failed == 0:
            status = "healthy"
        elif failed / total < 0.3:
            status = "degraded"
        else:
            status = "error"
        result[name] = ProviderHealthStatus(
            status=status,
            total=total,
            failures=failed,
            summary=f"{succeeded}/{total} calls succeeded in last 24h",
        )

    return ProviderHealthResponse(providers=result)


@router.get("/calls", response_model=CallListResponse)
async def list_calls(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    start_date: Optional[str] = Query(None, description="Filter by start date (ISO format)"),
    end_date: Optional[str] = Query(None, description="Filter by end date (ISO format)"),
    caller_number: Optional[str] = Query(None, description="Filter by caller number (partial match)"),
    caller_name: Optional[str] = Query(None, description="Filter by caller name (partial match)"),
    provider_name: Optional[str] = Query(None, description="Filter by provider"),
    pipeline_name: Optional[str] = Query(None, description="Filter by pipeline"),
    context_name: Optional[str] = Query(None, description="Filter by context"),
    outcome: Optional[str] = Query(None, description="Filter by outcome"),
    has_tool_calls: Optional[bool] = Query(None, description="Filter calls with tool executions"),
    min_duration: Optional[float] = Query(None, description="Minimum duration in seconds"),
    max_duration: Optional[float] = Query(None, description="Maximum duration in seconds"),
    transcript_search: Optional[str] = Query(None, min_length=1, max_length=256, description="Search within conversation transcripts (case-insensitive substring match)"),
    order_by: str = Query("start_time", description="Column to order by"),
    order_dir: str = Query("DESC", description="Order direction (ASC/DESC)"),
):
    """
    List call history records with pagination and filtering.
    """
    store = _get_call_history_store()
    
    parsed_start = _parse_datetime_param(start_date, end_of_day_if_date_only=False)
    parsed_end = _parse_datetime_param(end_date, end_of_day_if_date_only=True)
    
    # Get total count (with all filters for accurate pagination)
    total = await store.count(
        start_date=parsed_start,
        end_date=parsed_end,
        caller_number=caller_number,
        caller_name=caller_name,
        provider_name=provider_name,
        pipeline_name=pipeline_name,
        context_name=context_name,
        outcome=outcome,
        has_tool_calls=has_tool_calls,
        min_duration=min_duration,
        max_duration=max_duration,
        transcript_search=transcript_search,
    )
    
    # Get paginated records
    offset = (page - 1) * page_size
    records = await store.list(
        limit=page_size,
        offset=offset,
        start_date=parsed_start,
        end_date=parsed_end,
        caller_number=caller_number,
        caller_name=caller_name,
        provider_name=provider_name,
        pipeline_name=pipeline_name,
        context_name=context_name,
        outcome=outcome,
        has_tool_calls=has_tool_calls,
        min_duration=min_duration,
        max_duration=max_duration,
        transcript_search=transcript_search,
        order_by=order_by,
        order_dir=order_dir,
        include_details=False,
    )
    
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    
    return CallListResponse(
        calls=[_record_to_summary_response(r) for r in records],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/calls/stats", response_model=CallStatsResponse)
async def get_call_stats(
    start_date: Optional[str] = Query(None, description="Filter by start date (ISO format)"),
    end_date: Optional[str] = Query(None, description="Filter by end date (ISO format)"),
):
    """
    Get aggregate statistics for the dashboard.
    """
    store = _get_call_history_store()
    
    parsed_start = _parse_datetime_param(start_date, end_of_day_if_date_only=False)
    parsed_end = _parse_datetime_param(end_date, end_of_day_if_date_only=True)
    
    stats = await store.get_stats(start_date=parsed_start, end_date=parsed_end)
    
    # Fetch active calls from ai_engine health endpoint (Milestone 21)
    active_calls = 0
    try:
        import aiohttp
        ai_engine_url = os.getenv("AI_ENGINE_HEALTH_URL", "http://localhost:15000")
        logger.info(f"Fetching active calls from {ai_engine_url}/sessions/stats")
        headers = {}
        health_token = (os.getenv("HEALTH_API_TOKEN") or "").strip()
        if health_token:
            headers["Authorization"] = f"Bearer {health_token}"
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{ai_engine_url}/sessions/stats",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                if resp.status == 200:
                    session_stats = await resp.json()
                    active_calls = session_stats.get("active_calls", 0)
                    logger.info(f"Active calls from ai_engine: {active_calls}")
                else:
                    logger.warning(f"ai_engine returned status {resp.status}")
    except Exception as e:
        logger.warning(f"Failed to fetch active calls from ai_engine: {e}")
    
    stats["active_calls"] = active_calls
    
    return CallStatsResponse(**stats)


@router.get("/calls/filters", response_model=FilterOptionsResponse)
async def get_filter_options():
    """
    Get distinct values for filter dropdowns.
    """
    store = _get_call_history_store()
    
    providers = await store.get_distinct_values("provider_name")
    pipelines = await store.get_distinct_values("pipeline_name")
    contexts = await store.get_distinct_values("context_name")
    outcomes = await store.get_distinct_values("outcome")
    
    return FilterOptionsResponse(
        providers=providers,
        pipelines=pipelines,
        contexts=contexts,
        outcomes=outcomes,
    )


@router.get("/calls/{record_id}", response_model=CallRecordResponse)
async def get_call(record_id: str):
    """
    Get a single call record by ID.
    """
    store = _get_call_history_store()
    
    record = await store.get(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Call record not found")
    
    return _record_to_response(record)


@router.get("/calls/{record_id}/transcript")
async def get_call_transcript(record_id: str):
    """
    Get just the conversation history for a call.
    """
    store = _get_call_history_store()
    
    record = await store.get(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Call record not found")
    
    return {
        "call_id": record.call_id,
        "conversation_history": record.conversation_history or [],
    }


# ---------------------------------------------------------------------------
# Call recording playback
# ---------------------------------------------------------------------------

_RECORDING_BASE = Path("/mnt/asterisk_recordings")
_MIN_VALID_WAV_SIZE = 44  # WAV header is 44 bytes; files <= header size have no audio


def _has_exact_call_id(filename: str, call_id: str) -> bool:
    """Verify the filename contains the exact call_id as a delimited token.

    Asterisk recording filenames use the pattern ``...-{epoch}.{seq}.wav``
    where ``{epoch}.{seq}`` is the channel unique ID (our ``call_id``).
    A naive ``*call_id*`` glob can false-match when one ID is a prefix of
    another (e.g. ``.26`` vs ``.265``).  We check that the call_id appears
    bounded by non-alphanumeric characters (typically ``-`` and ``.wav``).
    """
    import re
    return bool(re.search(rf"(?<![0-9]){re.escape(call_id)}(?![0-9])", filename))


def _find_recording(call_id: str, start_time=None) -> Optional[Path]:
    """Find a recording file matching the given Asterisk call_id."""
    base = _RECORDING_BASE
    if not base.is_dir():
        return None

    import glob as _glob_mod
    safe_id = _glob_mod.escape(call_id)
    pattern = f"*{safe_id}*.wav"

    def _check(match: Path) -> bool:
        return (
            match.is_file()
            and match.resolve().is_relative_to(base.resolve())
            and _has_exact_call_id(match.name, call_id)
        )

    # Fast path: date-scoped directory (YYYY/MM/DD)
    if start_time:
        dt = start_time if isinstance(start_time, datetime) else datetime.fromisoformat(str(start_time))
        date_dir = base / dt.strftime("%Y") / dt.strftime("%m") / dt.strftime("%d")
        if date_dir.is_dir():
            for match in date_dir.glob(pattern):
                if _check(match):
                    return match

    # Fallback: root directory (legacy flat layout)
    for match in base.glob(pattern):
        if _check(match):
            return match

    # Last resort: recursive search across all date folders
    for match in base.glob(f"*/*/*/*{safe_id}*.wav"):
        if _check(match):
            return match

    return None


class RecordingInfoResponse(BaseModel):
    has_recording: bool = False
    filename: Optional[str] = None
    file_path: Optional[str] = None
    file_size_bytes: int = 0
    duration_hint: Optional[str] = None


@router.get("/calls/{record_id}/recording", response_model=RecordingInfoResponse)
async def get_call_recording_info(record_id: str):
    """Check if a recording exists for a call and return metadata."""
    store = _get_call_history_store()
    record = await store.get(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Call record not found")

    recording = _find_recording(record.call_id, record.start_time)
    if not recording or not recording.is_file():
        return RecordingInfoResponse()

    size = recording.stat().st_size
    return RecordingInfoResponse(
        has_recording=True,
        filename=recording.name,
        file_path=str(recording),
        file_size_bytes=size,
        duration_hint="empty" if size <= _MIN_VALID_WAV_SIZE else None,
    )


@router.get("/calls/{record_id}/recording.wav")
async def stream_call_recording(record_id: str):
    """Stream the call recording WAV file for browser playback."""
    store = _get_call_history_store()
    record = await store.get(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Call record not found")

    recording = _find_recording(record.call_id, record.start_time)
    if not recording or not recording.is_file():
        raise HTTPException(status_code=404, detail="Recording file not found")

    if recording.stat().st_size <= _MIN_VALID_WAV_SIZE:
        raise HTTPException(status_code=404, detail="Recording is empty (no audio captured)")

    return FileResponse(
        path=str(recording),
        media_type="audio/wav",
        filename=recording.name,
    )


@router.delete("/calls/{record_id}")
async def delete_call(record_id: str):
    """
    Delete a single call record.
    """
    store = _get_call_history_store()
    
    success = await store.delete(record_id)
    if not success:
        raise HTTPException(status_code=404, detail="Call record not found")
    
    return {"status": "deleted", "id": record_id}


@router.delete("/calls")
async def bulk_delete_calls(
    before_date: Optional[str] = Query(None, description="Delete records before this date (ISO format)"),
    older_than_days: Optional[int] = Query(None, ge=1, description="Delete records older than N days"),
):
    """
    Bulk delete call records by date.
    """
    if not before_date and not older_than_days:
        raise HTTPException(
            status_code=400, 
            detail="Must specify either before_date or older_than_days"
        )
    
    store = _get_call_history_store()
    
    if older_than_days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    else:
        cutoff = _parse_datetime_param(before_date, end_of_day_if_date_only=False)
        if cutoff is None:
            raise HTTPException(status_code=400, detail="Invalid before_date format")
    
    deleted = await store.delete_before(cutoff)
    
    return {"status": "deleted", "count": deleted, "before": cutoff.isoformat()}


@router.get("/calls/export/csv")
async def export_calls_csv(
    start_date: Optional[str] = Query(None, description="Filter by start date (ISO format)"),
    end_date: Optional[str] = Query(None, description="Filter by end date (ISO format)"),
    caller_number: Optional[str] = Query(None, description="Filter by caller number"),
    caller_name: Optional[str] = Query(None, description="Filter by caller name"),
    provider_name: Optional[str] = Query(None, description="Filter by provider"),
    pipeline_name: Optional[str] = Query(None, description="Filter by pipeline"),
    context_name: Optional[str] = Query(None, description="Filter by context"),
    outcome: Optional[str] = Query(None, description="Filter by outcome"),
    has_tool_calls: Optional[bool] = Query(None, description="Filter by tool usage"),
    min_duration: Optional[float] = Query(None, description="Minimum duration in seconds"),
    max_duration: Optional[float] = Query(None, description="Maximum duration in seconds"),
):
    """
    Export call records as CSV with all filters matching the UI.
    """
    store = _get_call_history_store()
    
    parsed_start = _parse_datetime_param(start_date, end_of_day_if_date_only=False)
    parsed_end = _parse_datetime_param(end_date, end_of_day_if_date_only=True)
    
    # Get all matching records (limit to 10000 for safety)
    records = await store.list(
        limit=10000,
        offset=0,
        start_date=parsed_start,
        end_date=parsed_end,
        caller_number=caller_number,
        caller_name=caller_name,
        provider_name=provider_name,
        pipeline_name=pipeline_name,
        context_name=context_name,
        outcome=outcome,
        has_tool_calls=has_tool_calls,
        min_duration=min_duration,
        max_duration=max_duration,
        include_details=True,
    )
    
    # Generate CSV
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow([
        "ID", "Call ID", "Caller Number", "Caller Name",
        "Start Time", "End Time", "Duration (s)",
        "Provider", "Pipeline", "Context", "Outcome",
        "Transfer Destination", "Error Message",
        "Tool Calls", "Avg Latency (ms)", "Max Latency (ms)",
        "Total Turns", "Barge-ins"
    ])
    
    # Data rows
    for r in records:
        writer.writerow([
            r.id, r.call_id, r.caller_number or "", r.caller_name or "",
            r.start_time.isoformat() if r.start_time else "",
            r.end_time.isoformat() if r.end_time else "",
            round(r.duration_seconds, 2),
            r.provider_name, r.pipeline_name or "", r.context_name or "", r.outcome,
            r.transfer_destination or "", r.error_message or "",
            len(r.tool_calls), round(r.avg_turn_latency_ms, 2), round(r.max_turn_latency_ms, 2),
            r.total_turns, r.barge_in_count
        ])
    
    csv_content = output.getvalue()
    
    filename = f"call_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/calls/export/json")
async def export_calls_json(
    start_date: Optional[str] = Query(None, description="Filter by start date (ISO format)"),
    end_date: Optional[str] = Query(None, description="Filter by end date (ISO format)"),
    caller_number: Optional[str] = Query(None, description="Filter by caller number"),
    caller_name: Optional[str] = Query(None, description="Filter by caller name"),
    provider_name: Optional[str] = Query(None, description="Filter by provider"),
    pipeline_name: Optional[str] = Query(None, description="Filter by pipeline"),
    context_name: Optional[str] = Query(None, description="Filter by context"),
    outcome: Optional[str] = Query(None, description="Filter by outcome"),
    has_tool_calls: Optional[bool] = Query(None, description="Filter by tool usage"),
    min_duration: Optional[float] = Query(None, description="Minimum duration in seconds"),
    max_duration: Optional[float] = Query(None, description="Maximum duration in seconds"),
):
    """
    Export call records as JSON with all filters matching the UI.
    """
    store = _get_call_history_store()
    
    parsed_start = _parse_datetime_param(start_date, end_of_day_if_date_only=False)
    parsed_end = _parse_datetime_param(end_date, end_of_day_if_date_only=True)
    
    # Get all matching records (limit to 10000 for safety)
    records = await store.list(
        limit=10000,
        offset=0,
        start_date=parsed_start,
        end_date=parsed_end,
        caller_number=caller_number,
        caller_name=caller_name,
        provider_name=provider_name,
        pipeline_name=pipeline_name,
        context_name=context_name,
        outcome=outcome,
        has_tool_calls=has_tool_calls,
        min_duration=min_duration,
        max_duration=max_duration,
        include_details=True,
    )
    
    # Convert to JSON-serializable format
    data = {
        "exported_at": datetime.now().isoformat(),
        "total_records": len(records),
        "records": [_record_to_response(r).model_dump() for r in records]
    }
    
    json_content = json.dumps(data, indent=2)
    
    filename = f"call_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    return Response(
        content=json_content,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
