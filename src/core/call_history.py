"""
Call History persistence layer.

Stores call records in SQLite for historical analysis and debugging.
"""

import asyncio
import json
import logging
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CallRecord:
    """Persisted call record for history and analytics."""
    
    # Core identifiers
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    call_id: str = ""
    caller_number: Optional[str] = None
    caller_name: Optional[str] = None
    
    # Timing
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_seconds: float = 0.0
    
    # Configuration
    provider_name: str = "unknown"
    pipeline_name: Optional[str] = None
    pipeline_components: Dict[str, str] = field(default_factory=dict)
    context_name: Optional[str] = None
    
    # Conversation
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    
    # Outcome
    outcome: str = "completed"  # completed | transferred | error | abandoned
    transfer_destination: Optional[str] = None
    error_message: Optional[str] = None
    
    # Tool executions (debugging)
    # tool_calls = in-call tool invocations issued by the LLM during the conversation.
    # pre_call_tool_calls = pre-call enrichment tool execution metadata (lookup tools).
    # post_call_tool_calls = post-call webhook/notification execution metadata (fire-and-forget).
    # All three share the same per-entry shape (see ToolCallEntry typedef in admin_ui frontend).
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    pre_call_tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    post_call_tool_calls: List[Dict[str, Any]] = field(default_factory=list)

    # Latency metrics (debugging)
    avg_turn_latency_ms: float = 0.0
    max_turn_latency_ms: float = 0.0
    total_turns: int = 0
    
    # Audio stats (debugging)
    caller_audio_format: str = "ulaw"
    codec_alignment_ok: bool = True
    barge_in_count: int = 0
    
    # Metadata
    created_at: Optional[datetime] = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        # Convert datetime objects to ISO strings
        for key in ['start_time', 'end_time', 'created_at']:
            if data[key] is not None:
                data[key] = data[key].isoformat() if isinstance(data[key], datetime) else data[key]
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CallRecord":
        """Create CallRecord from dictionary."""
        # Parse datetime strings back to datetime objects
        for key in ['start_time', 'end_time', 'created_at']:
            if data.get(key) and isinstance(data[key], str):
                try:
                    data[key] = datetime.fromisoformat(data[key])
                except ValueError:
                    data[key] = None
        
        # Parse JSON strings for complex fields
        _list_fields = ['conversation_history', 'tool_calls', 'pre_call_tool_calls', 'post_call_tool_calls']
        for key in ['pipeline_components', *_list_fields]:
            if data.get(key) and isinstance(data[key], str):
                try:
                    data[key] = json.loads(data[key])
                except json.JSONDecodeError:
                    data[key] = [] if key in _list_fields else {}
            elif data.get(key) is None and key in _list_fields:
                # NULL columns (existing rows pre-migration) — surface as empty list.
                data[key] = []
        
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class CallHistoryStore:
    """SQLite-based call history storage."""

    @staticmethod
    def _escape_like(value: str) -> str:
        """Escape special characters for use in a SQL LIKE pattern with ESCAPE '\\'."""
        return (
            value
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )

    _CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS call_records (
        id TEXT PRIMARY KEY,
        call_id TEXT NOT NULL,
        caller_number TEXT,
        caller_name TEXT,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        duration_seconds REAL,
        provider_name TEXT,
        pipeline_name TEXT,
        pipeline_components TEXT,
        context_name TEXT,
        conversation_history TEXT,
        outcome TEXT,
        transfer_destination TEXT,
        error_message TEXT,
        tool_calls TEXT,
        pre_call_tool_calls TEXT,
        post_call_tool_calls TEXT,
        avg_turn_latency_ms REAL,
        max_turn_latency_ms REAL,
        total_turns INTEGER,
        caller_audio_format TEXT,
        codec_alignment_ok INTEGER,
        barge_in_count INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """
    
    _CREATE_INDEXES_SQL = [
        "CREATE INDEX IF NOT EXISTS idx_call_records_start_time ON call_records(start_time)",
        "CREATE INDEX IF NOT EXISTS idx_call_records_caller_number ON call_records(caller_number)",
        "CREATE INDEX IF NOT EXISTS idx_call_records_outcome ON call_records(outcome)",
        "CREATE INDEX IF NOT EXISTS idx_call_records_provider ON call_records(provider_name)",
        "CREATE INDEX IF NOT EXISTS idx_call_records_pipeline ON call_records(pipeline_name)",
        "CREATE INDEX IF NOT EXISTS idx_call_records_context ON call_records(context_name)",
    ]

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize call history store.
        
        Args:
            db_path: Path to SQLite database file. Defaults to data/call_history.db
        """
        self._db_path = db_path or os.getenv(
            "CALL_HISTORY_DB_PATH", 
            "/app/data/call_history.db"
        )
        self._retention_days = int(os.getenv("CALL_HISTORY_RETENTION_DAYS", "0"))
        self._enabled = os.getenv("CALL_HISTORY_ENABLED", "true").lower() in ("true", "1", "yes")
        self._lock = threading.Lock()
        self._initialized = False
        
        if self._enabled:
            self._init_db()
    
    def _init_db(self) -> None:
        """Initialize database and create tables."""
        try:
            # Ensure directory exists
            db_dir = os.path.dirname(self._db_path)
            if db_dir:
                Path(db_dir).mkdir(parents=True, exist_ok=True)
            
            with self._lock:
                conn = self._get_connection()
                try:
                    cursor = conn.cursor()
                    cursor.execute(self._CREATE_TABLE_SQL)
                    self._ensure_schema_sync(conn)
                    for idx_sql in self._CREATE_INDEXES_SQL:
                        cursor.execute(idx_sql)
                    conn.commit()
                    self._initialized = True
                    logger.info(f"Call history database initialized: {self._db_path}")
                finally:
                    conn.close()
        except Exception as e:
            logger.error(f"Failed to initialize call history database: {e}", exc_info=True)
            self._enabled = False

    def _ensure_schema_sync(self, conn: sqlite3.Connection) -> None:
        """
        Best-effort additive migrations for existing installs.

        SQLite has limited ALTER TABLE support; we only add nullable columns when
        missing — never drop or rename. Failures are logged and never block startup.
        """
        try:
            cur = conn.cursor()
            existing = {str(r[1]) for r in cur.execute("PRAGMA table_info(call_records)").fetchall()}
            if "pre_call_tool_calls" not in existing:
                cur.execute("ALTER TABLE call_records ADD COLUMN pre_call_tool_calls TEXT")
            if "post_call_tool_calls" not in existing:
                cur.execute("ALTER TABLE call_records ADD COLUMN post_call_tool_calls TEXT")
        except Exception:
            logger.debug("call_records schema migration failed (non-fatal)", exc_info=True)
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection with WAL mode and busy timeout for multi-process safety."""
        conn = sqlite3.connect(self._db_path, timeout=30.0, check_same_thread=False)  # 30s busy timeout
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for better concurrent read/write performance
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=30000;")  # 30s in milliseconds
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn
    
    async def save(self, record: CallRecord) -> bool:
        """
        Save a call record to the database.
        
        Args:
            record: CallRecord to save
            
        Returns:
            True if successful, False otherwise
        """
        if not self._enabled:
            return False
        
        def _save_sync():
            with self._lock:
                conn = self._get_connection()
                try:
                    cursor = conn.cursor()
                    # Check if record with same call_id already exists (prevent duplicates)
                    cursor.execute("SELECT id FROM call_records WHERE call_id = ?", (record.call_id,))
                    existing = cursor.fetchone()
                    if existing:
                        # Already saved, skip duplicate
                        return True
                    
                    cursor.execute("""
                        INSERT OR REPLACE INTO call_records (
                            id, call_id, caller_number, caller_name,
                            start_time, end_time, duration_seconds,
                            provider_name, pipeline_name, pipeline_components, context_name,
                            conversation_history, outcome, transfer_destination, error_message,
                            tool_calls, pre_call_tool_calls, post_call_tool_calls,
                            avg_turn_latency_ms, max_turn_latency_ms, total_turns,
                            caller_audio_format, codec_alignment_ok, barge_in_count, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        record.id,
                        record.call_id,
                        record.caller_number,
                        record.caller_name,
                        record.start_time.isoformat() if record.start_time else None,
                        record.end_time.isoformat() if record.end_time else None,
                        record.duration_seconds,
                        record.provider_name,
                        record.pipeline_name,
                        json.dumps(record.pipeline_components),
                        record.context_name,
                        json.dumps(record.conversation_history),
                        record.outcome,
                        record.transfer_destination,
                        record.error_message,
                        json.dumps(record.tool_calls),
                        json.dumps(record.pre_call_tool_calls),
                        json.dumps(record.post_call_tool_calls),
                        record.avg_turn_latency_ms,
                        record.max_turn_latency_ms,
                        record.total_turns,
                        record.caller_audio_format,
                        1 if record.codec_alignment_ok else 0,
                        record.barge_in_count,
                        record.created_at.isoformat() if record.created_at else None,
                    ))
                    conn.commit()
                    return True
                except Exception as e:
                    logger.error(f"Failed to save call record {record.call_id}: {e}")
                    return False
                finally:
                    conn.close()
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _save_sync)
    
    # ---------------------------------------------------------------
    # Phase-tool execution metadata (pre-call / post-call)
    #
    # Pre-call tools run synchronously before the AI greets, so their entries
    # could in principle be inlined into the initial save(). We still expose
    # the same append/update API as post-call to keep the engine code symmetric.
    #
    # Post-call tools are fire-and-forget; the call_records row is written
    # *before* tools complete, then each tool calls append_phase_tool() to
    # add a `pending` placeholder and update_phase_tool() to record its result.
    # Read-modify-write is serialized by self._lock + SQLite WAL so concurrent
    # tools for the same call don't clobber each other.
    # ---------------------------------------------------------------

    _PHASE_COLUMN = {"pre_call": "pre_call_tool_calls", "post_call": "post_call_tool_calls"}

    async def append_phase_tool(self, call_id: str, phase: str, record: Dict[str, Any]) -> bool:
        """
        Append a tool-execution entry to either pre_call_tool_calls or post_call_tool_calls.

        Used at scheduling time to write a `pending` placeholder. If the call_records
        row is missing (race with persist), returns False — the engine should ensure
        persist runs first.
        """
        column = self._PHASE_COLUMN.get(phase)
        if not self._enabled or column is None:
            return False

        def _sync():
            with self._lock:
                conn = self._get_connection()
                try:
                    cur = conn.cursor()
                    cur.execute(f"SELECT {column} FROM call_records WHERE call_id = ?", (call_id,))
                    row = cur.fetchone()
                    if row is None:
                        return False
                    existing_raw = row[0] if not isinstance(row, sqlite3.Row) else row[column]
                    try:
                        entries = json.loads(existing_raw) if existing_raw else []
                    except (TypeError, json.JSONDecodeError):
                        entries = []
                    if not isinstance(entries, list):
                        entries = []
                    entries.append(record)
                    cur.execute(
                        f"UPDATE call_records SET {column} = ? WHERE call_id = ?",
                        (json.dumps(entries), call_id),
                    )
                    conn.commit()
                    return True
                except Exception as exc:
                    logger.error(
                        "append_phase_tool failed",
                        extra={"call_id": call_id, "phase": phase, "error": str(exc)},
                    )
                    return False
                finally:
                    conn.close()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync)

    async def update_phase_tool(
        self,
        call_id: str,
        phase: str,
        tool_name: str,
        started_at: Optional[str],
        updates: Dict[str, Any],
    ) -> bool:
        """
        Merge ``updates`` into an existing entry in ``<phase>_tool_calls`` matched by
        (``name`` == ``tool_name``, ``started_at`` == ``started_at``). If ``started_at``
        is None, matches by name and updates the most recent entry. If no entry matches,
        appends a new one (keeps the API forgiving for callers that skipped the pending
        placeholder).
        """
        column = self._PHASE_COLUMN.get(phase)
        if not self._enabled or column is None:
            return False

        def _sync():
            with self._lock:
                conn = self._get_connection()
                try:
                    cur = conn.cursor()
                    cur.execute(f"SELECT {column} FROM call_records WHERE call_id = ?", (call_id,))
                    row = cur.fetchone()
                    if row is None:
                        return False
                    existing_raw = row[0] if not isinstance(row, sqlite3.Row) else row[column]
                    try:
                        entries = json.loads(existing_raw) if existing_raw else []
                    except (TypeError, json.JSONDecodeError):
                        entries = []
                    if not isinstance(entries, list):
                        entries = []

                    target_idx = None
                    for i, entry in enumerate(entries):
                        if not isinstance(entry, dict):
                            continue
                        if entry.get("name") != tool_name:
                            continue
                        if started_at is None or entry.get("started_at") == started_at:
                            target_idx = i  # keep iterating to land on most recent match
                    if target_idx is None:
                        merged = {"name": tool_name}
                        if started_at is not None:
                            merged["started_at"] = started_at
                        merged.update(updates)
                        entries.append(merged)
                    else:
                        entries[target_idx] = {**entries[target_idx], **updates}

                    cur.execute(
                        f"UPDATE call_records SET {column} = ? WHERE call_id = ?",
                        (json.dumps(entries), call_id),
                    )
                    conn.commit()
                    return True
                except Exception as exc:
                    logger.error(
                        "update_phase_tool failed",
                        extra={"call_id": call_id, "phase": phase, "tool": tool_name, "error": str(exc)},
                    )
                    return False
                finally:
                    conn.close()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync)

    async def get(self, record_id: str) -> Optional[CallRecord]:
        """
        Get a call record by ID.
        
        Args:
            record_id: UUID of the record
            
        Returns:
            CallRecord if found, None otherwise
        """
        if not self._enabled:
            return None
        
        def _get_sync():
            with self._lock:
                conn = self._get_connection()
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT * FROM call_records WHERE id = ?", (record_id,))
                    row = cursor.fetchone()
                    if row:
                        return CallRecord.from_dict(dict(row))
                    return None
                finally:
                    conn.close()
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _get_sync)
    
    async def get_by_call_id(self, call_id: str) -> Optional[CallRecord]:
        """
        Get a call record by Asterisk call ID.
        
        Args:
            call_id: Asterisk channel ID
            
        Returns:
            CallRecord if found, None otherwise
        """
        if not self._enabled:
            return None
        
        def _get_sync():
            with self._lock:
                conn = self._get_connection()
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT * FROM call_records WHERE call_id = ?", (call_id,))
                    row = cursor.fetchone()
                    if row:
                        return CallRecord.from_dict(dict(row))
                    return None
                finally:
                    conn.close()
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _get_sync)
    
    async def list(
        self,
        limit: int = 50,
        offset: int = 0,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        caller_number: Optional[str] = None,
        caller_name: Optional[str] = None,
        provider_name: Optional[str] = None,
        pipeline_name: Optional[str] = None,
        context_name: Optional[str] = None,
        outcome: Optional[str] = None,
        has_tool_calls: Optional[bool] = None,
        min_duration: Optional[float] = None,
        max_duration: Optional[float] = None,
        transcript_search: Optional[str] = None,
        order_by: str = "start_time",
        order_dir: str = "DESC",
        include_details: bool = True,
    ) -> List[CallRecord]:
        """
        List call records with filtering and pagination.
        
        Args:
            limit: Maximum records to return
            offset: Records to skip
            start_date: Filter by start date (inclusive)
            end_date: Filter by end date (inclusive)
            caller_number: Filter by caller number (partial match)
            caller_name: Filter by caller name (partial match)
            provider_name: Filter by provider
            pipeline_name: Filter by pipeline
            context_name: Filter by context
            outcome: Filter by outcome
            has_tool_calls: Filter calls with/without tool calls
            min_duration: Minimum duration in seconds
            max_duration: Maximum duration in seconds
            order_by: Column to order by
            order_dir: ASC or DESC
            include_details: If False, excludes large payload fields (transcript/tool JSON)
            
        Returns:
            List of CallRecord objects
        """
        if not self._enabled:
            return []
        
        def _list_sync():
            with self._lock:
                conn = self._get_connection()
                try:
                    # Build query with filters
                    conditions = []
                    params = []
                    
                    if start_date:
                        conditions.append("start_time >= ?")
                        params.append(start_date.isoformat())
                    if end_date:
                        conditions.append("start_time <= ?")
                        params.append(end_date.isoformat())
                    if caller_number:
                        conditions.append("caller_number LIKE ?")
                        params.append(f"%{caller_number}%")
                    if caller_name:
                        conditions.append("caller_name LIKE ?")
                        params.append(f"%{caller_name}%")
                    if provider_name:
                        conditions.append("provider_name = ?")
                        params.append(provider_name)
                    if pipeline_name:
                        conditions.append("pipeline_name = ?")
                        params.append(pipeline_name)
                    if context_name:
                        conditions.append("context_name = ?")
                        params.append(context_name)
                    if outcome:
                        conditions.append("outcome = ?")
                        params.append(outcome)
                    if has_tool_calls is not None:
                        if has_tool_calls:
                            conditions.append("tool_calls IS NOT NULL AND tool_calls != '[]'")
                        else:
                            conditions.append("(tool_calls IS NULL OR tool_calls = '[]')")
                    if min_duration is not None:
                        conditions.append("duration_seconds >= ?")
                        params.append(min_duration)
                    if max_duration is not None:
                        conditions.append("duration_seconds <= ?")
                        params.append(max_duration)
                    if transcript_search:
                        escaped = self._escape_like(transcript_search)
                        conditions.append("LOWER(conversation_history) LIKE LOWER(?) ESCAPE '\\'")
                        params.append(f"%{escaped}%")

                    # Validate order_by to prevent SQL injection
                    valid_columns = [
                        'start_time', 'end_time', 'duration_seconds', 
                        'caller_number', 'caller_name', 'provider_name', 'pipeline_name',
                        'context_name', 'outcome', 'created_at'
                    ]
                    safe_order_by = order_by if order_by in valid_columns else 'start_time'
                    safe_order_dir = order_dir.upper() if order_dir.upper() in ['ASC', 'DESC'] else 'DESC'
                    
                    where_clause = " AND ".join(conditions) if conditions else "1=1"

                    select_cols = "*"
                    if not include_details:
                        # Exclude transcript/tool payloads to keep list views fast and reduce exposure.
                        select_cols = ", ".join([
                            "id",
                            "call_id",
                            "caller_number",
                            "caller_name",
                            "start_time",
                            "end_time",
                            "duration_seconds",
                            "provider_name",
                            "pipeline_name",
                            "pipeline_components",
                            "context_name",
                            "outcome",
                            "transfer_destination",
                            "error_message",
                            "avg_turn_latency_ms",
                            "max_turn_latency_ms",
                            "total_turns",
                            "caller_audio_format",
                            "codec_alignment_ok",
                            "barge_in_count",
                            "created_at",
                        ])

                    query = f"""
                        SELECT {select_cols} FROM call_records 
                        WHERE {where_clause}
                        ORDER BY {safe_order_by} {safe_order_dir}
                        LIMIT ? OFFSET ?
                    """
                    params.extend([limit, offset])
                    
                    cursor = conn.cursor()
                    cursor.execute(query, params)
                    rows = cursor.fetchall()
                    return [CallRecord.from_dict(dict(row)) for row in rows]
                finally:
                    conn.close()
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _list_sync)
    
    async def count(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        caller_number: Optional[str] = None,
        caller_name: Optional[str] = None,
        provider_name: Optional[str] = None,
        pipeline_name: Optional[str] = None,
        context_name: Optional[str] = None,
        outcome: Optional[str] = None,
        has_tool_calls: Optional[bool] = None,
        min_duration: Optional[float] = None,
        max_duration: Optional[float] = None,
        transcript_search: Optional[str] = None,
    ) -> int:
        """Count records matching filters."""
        if not self._enabled:
            return 0
        
        def _count_sync():
            with self._lock:
                conn = self._get_connection()
                try:
                    conditions = []
                    params = []
                    
                    if start_date:
                        conditions.append("start_time >= ?")
                        params.append(start_date.isoformat())
                    if end_date:
                        conditions.append("start_time <= ?")
                        params.append(end_date.isoformat())
                    if caller_number:
                        conditions.append("caller_number LIKE ?")
                        params.append(f"%{caller_number}%")
                    if caller_name:
                        conditions.append("caller_name LIKE ?")
                        params.append(f"%{caller_name}%")
                    if provider_name:
                        conditions.append("provider_name = ?")
                        params.append(provider_name)
                    if pipeline_name:
                        conditions.append("pipeline_name = ?")
                        params.append(pipeline_name)
                    if context_name:
                        conditions.append("context_name = ?")
                        params.append(context_name)
                    if outcome:
                        conditions.append("outcome = ?")
                        params.append(outcome)
                    if has_tool_calls is not None:
                        if has_tool_calls:
                            conditions.append("tool_calls IS NOT NULL AND tool_calls != '[]'")
                        else:
                            conditions.append("(tool_calls IS NULL OR tool_calls = '[]')")
                    if min_duration is not None:
                        conditions.append("duration_seconds >= ?")
                        params.append(min_duration)
                    if max_duration is not None:
                        conditions.append("duration_seconds <= ?")
                        params.append(max_duration)
                    if transcript_search:
                        escaped = self._escape_like(transcript_search)
                        conditions.append("LOWER(conversation_history) LIKE LOWER(?) ESCAPE '\\'")
                        params.append(f"%{escaped}%")

                    where_clause = " AND ".join(conditions) if conditions else "1=1"
                    query = f"SELECT COUNT(*) FROM call_records WHERE {where_clause}"
                    
                    cursor = conn.cursor()
                    cursor.execute(query, params)
                    return cursor.fetchone()[0]
                finally:
                    conn.close()
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _count_sync)
    
    async def delete(self, record_id: str) -> bool:
        """Delete a call record by ID."""
        if not self._enabled:
            return False
        
        def _delete_sync():
            with self._lock:
                conn = self._get_connection()
                try:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM call_records WHERE id = ?", (record_id,))
                    conn.commit()
                    return cursor.rowcount > 0
                finally:
                    conn.close()
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _delete_sync)
    
    async def delete_before(self, before_date: datetime) -> int:
        """Delete all records before a date. Returns count deleted."""
        if not self._enabled:
            return 0
        
        def _delete_sync():
            with self._lock:
                conn = self._get_connection()
                try:
                    cursor = conn.cursor()
                    cursor.execute(
                        "DELETE FROM call_records WHERE start_time < ?",
                        (before_date.isoformat(),)
                    )
                    conn.commit()
                    return cursor.rowcount
                finally:
                    conn.close()
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _delete_sync)
    
    async def get_stats(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Get aggregate statistics for the dashboard.
        
        Returns:
            Dictionary with stats: total_calls, avg_duration, outcomes, providers, etc.
        """
        if not self._enabled:
            return {}
        
        def _stats_sync():
            with self._lock:
                conn = self._get_connection()
                try:
                    cursor = conn.cursor()
                    
                    # Build date filter
                    date_filter = "1=1"
                    params = []
                    if start_date:
                        date_filter += " AND start_time >= ?"
                        params.append(start_date.isoformat())
                    if end_date:
                        date_filter += " AND start_time <= ?"
                        params.append(end_date.isoformat())
                    
                    # Total calls and duration stats
                    cursor.execute(f"""
                        SELECT 
                            COUNT(*) as total_calls,
                            AVG(duration_seconds) as avg_duration,
                            MAX(duration_seconds) as max_duration,
                            MIN(duration_seconds) as min_duration,
                            SUM(duration_seconds) as total_duration,
                            AVG(avg_turn_latency_ms) as avg_latency,
                            SUM(total_turns) as total_turns,
                            SUM(barge_in_count) as total_barge_ins
                        FROM call_records WHERE {date_filter}
                    """, params)
                    row = cursor.fetchone()
                    stats = {
                        "total_calls": row[0] or 0,
                        "avg_duration_seconds": round(row[1] or 0, 2),
                        "max_duration_seconds": round(row[2] or 0, 2),
                        "min_duration_seconds": round(row[3] or 0, 2),
                        "total_duration_seconds": round(row[4] or 0, 2),
                        "avg_latency_ms": round(row[5] or 0, 2),
                        "total_turns": row[6] or 0,
                        "total_barge_ins": row[7] or 0,
                    }
                    
                    # Outcome breakdown
                    cursor.execute(f"""
                        SELECT outcome, COUNT(*) as count
                        FROM call_records WHERE {date_filter}
                        GROUP BY outcome
                    """, params)
                    stats["outcomes"] = {row[0]: row[1] for row in cursor.fetchall()}
                    
                    # Provider usage
                    cursor.execute(f"""
                        SELECT provider_name, COUNT(*) as count
                        FROM call_records WHERE {date_filter}
                        GROUP BY provider_name
                    """, params)
                    stats["providers"] = {row[0]: row[1] for row in cursor.fetchall()}
                    
                    # Pipeline usage
                    cursor.execute(f"""
                        SELECT pipeline_name, COUNT(*) as count
                        FROM call_records WHERE {date_filter} AND pipeline_name IS NOT NULL
                        GROUP BY pipeline_name
                    """, params)
                    stats["pipelines"] = {row[0]: row[1] for row in cursor.fetchall()}
                    
                    # Context usage
                    cursor.execute(f"""
                        SELECT context_name, COUNT(*) as count
                        FROM call_records WHERE {date_filter} AND context_name IS NOT NULL
                        GROUP BY context_name
                    """, params)
                    stats["contexts"] = {row[0]: row[1] for row in cursor.fetchall()}
                    
                    # Calls per day (last 30 days)
                    cursor.execute(f"""
                        SELECT DATE(start_time) as day, COUNT(*) as count
                        FROM call_records 
                        WHERE {date_filter}
                        GROUP BY DATE(start_time)
                        ORDER BY day DESC
                        LIMIT 30
                    """, params)
                    stats["calls_per_day"] = [
                        {"date": row[0], "count": row[1]} 
                        for row in cursor.fetchall()
                    ]
                    
                    # Top callers
                    cursor.execute(f"""
                        SELECT caller_number, COUNT(*) as count
                        FROM call_records 
                        WHERE {date_filter} AND caller_number IS NOT NULL
                        GROUP BY caller_number
                        ORDER BY count DESC
                        LIMIT 10
                    """, params)
                    stats["top_callers"] = [
                        {"number": row[0], "count": row[1]} 
                        for row in cursor.fetchall()
                    ]
                    
                    # Tool usage stats
                    cursor.execute(f"""
                        SELECT COUNT(*) FROM call_records 
                        WHERE {date_filter} AND tool_calls != '[]'
                    """, params)
                    stats["calls_with_tools"] = cursor.fetchone()[0]
                    
                    # Top tools aggregation (parse JSON tool_calls field)
                    cursor.execute(f"""
                        SELECT tool_calls FROM call_records 
                        WHERE {date_filter} AND tool_calls != '[]'
                    """, params)
                    tool_counts: Dict[str, int] = {}
                    for row in cursor.fetchall():
                        try:
                            tools = json.loads(row[0]) if row[0] else []
                            for tool in tools:
                                name = tool.get("name", "unknown")
                                tool_counts[name] = tool_counts.get(name, 0) + 1
                        except (json.JSONDecodeError, TypeError):
                            pass
                    stats["top_tools"] = dict(sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)[:10])
                    
                    return stats
                finally:
                    conn.close()
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _stats_sync)
    
    async def cleanup_old_records(self) -> int:
        """
        Delete records older than retention period.
        
        Returns:
            Number of records deleted
        """
        if not self._enabled or self._retention_days <= 0:
            return 0
        
        cutoff = datetime.now() - timedelta(days=self._retention_days)
        deleted = await self.delete_before(cutoff)
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} old call history records (retention: {self._retention_days} days)")
        return deleted
    
    async def get_distinct_values(self, column: str) -> List[str]:
        """Get distinct values for a column (for filter dropdowns)."""
        if not self._enabled:
            return []
        
        valid_columns = ['provider_name', 'pipeline_name', 'context_name', 'outcome']
        if column not in valid_columns:
            return []
        
        def _get_sync():
            with self._lock:
                conn = self._get_connection()
                try:
                    cursor = conn.cursor()
                    cursor.execute(f"""
                        SELECT DISTINCT {column} FROM call_records 
                        WHERE {column} IS NOT NULL
                        ORDER BY {column}
                    """)
                    return [row[0] for row in cursor.fetchall()]
                finally:
                    conn.close()
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _get_sync)


# Global instance (lazy initialization)
_call_history_store: Optional[CallHistoryStore] = None


def get_call_history_store() -> CallHistoryStore:
    """Get the global call history store instance."""
    global _call_history_store
    if _call_history_store is None:
        _call_history_store = CallHistoryStore()
    return _call_history_store
