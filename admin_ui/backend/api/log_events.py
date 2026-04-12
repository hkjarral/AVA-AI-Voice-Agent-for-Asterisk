import ast
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


_TS_LEVEL_RE = re.compile(
    r"^\s*(?P<ts>\d{4}-\d\d-\d\d[T ][0-9:.]+(?:Z|[+-]\d{2}:\d{2})?)\s*\[\s*(?P<level>[a-zA-Z]+)\s*\]\s*(?P<rest>.*)$"
)

_LOGGER_RE = re.compile(r"^(?P<msg>.*?)\s*\[(?P<logger>[^\]]+)\]\s*(?P<kv>.*)$")

_KEY_RE = re.compile(r"\b(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)=(?P<val>\"[^\"]*\"|'[^']*'|[^\s]+)")


def _parse_ts(ts: str) -> Optional[datetime]:
    try:
        # Example: 2025-12-25T21:23:32.755042Z
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts).astimezone(timezone.utc)
    except Exception:
        return None


def _parse_kv(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in _KEY_RE.finditer(text or ""):
        k = m.group("key")
        v = m.group("val")
        if v and len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
            v = v[1:-1]
        out[k] = v
    return out


def _first_present(d: Dict[str, str], keys: Iterable[str]) -> Optional[str]:
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return None


@dataclass(frozen=True)
class LogEvent:
    ts: Optional[datetime]
    level: str
    msg: str
    component: Optional[str]
    call_id: Optional[str]
    provider: Optional[str]
    context: Optional[str]
    pipeline: Optional[str]
    category: str
    milestone: bool
    meta: Dict[str, str]
    raw: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts.isoformat() if self.ts else None,
            "level": self.level,
            "msg": self.msg,
            "component": self.component,
            "call_id": self.call_id,
            "provider": self.provider,
            "context": self.context,
            "pipeline": self.pipeline,
            "category": self.category,
            "milestone": self.milestone,
            "meta": dict(self.meta or {}),
            "raw": self.raw,
        }


def classify_event(msg: str, component: Optional[str]) -> Tuple[str, bool]:
    text = (msg or "").lower()
    comp = (component or "").lower()

    # ── Per-frame noise (fires every ~20 ms, ~50/sec) ──────────────────
    # Intercept before any other rule so these never pollute focused views.
    if "continuous input" in text and ("forwarding frame" in text or "frame sent" in text):
        return "audio", False
    if "encode resample" in text:
        return "audio", False
    if "encode config - reading provider config" in text:
        return "audio", False
    if "encoded for provider" in text:
        return "audio", False
    if "audiosocket rx" in text and "frame received" in text:
        return "transport", False

    # ── Strong component-based categorization ──────────────────────────
    if comp.startswith("src.tools.") or comp.startswith("src.mcp."):
        return "tools", False

    if comp.startswith("src.providers."):
        # Milestone-worthy provider events
        if "final user transcription" in text or "final ai transcription" in text:
            return "provider", True
        if "websocket connected" in text or "websocket closed" in text:
            return "provider", True
        if "websocket not open" in text:
            return "provider", False  # warning level handles visibility
        if "session started" in text or "setup complete" in text:
            return "provider", True
        if "greeting" in text and ("completed" in text or "sent" in text or "request" in text):
            return "provider", True
        if "farewell" in text or "cleanup_after_tts" in text or "armed cleanup" in text:
            return "provider", True
        if "flushed pending" in text:
            return "provider", True
        if "stopping" in text and "session" in text:
            return "provider", True
        return "provider", False

    if "vad" in comp or "vad_manager" in comp:
        return "vad", False

    # ── Milestones (info-level) + categories ───────────────────────────
    # Call lifecycle
    if "stasisstart event received" in text:
        return "call", True
    if text.startswith("stasis ended") or "stasis ended" in text:
        return "call", True
    if text.startswith("hanging up channel") or "hanging up channel" in text:
        return "call", True
    if text.startswith("channel destroyed") or "channel destroyed" in text:
        return "call", True
    if text.startswith("bridge destroyed") or "bridge destroyed" in text:
        return "call", True
    if "call cleanup completed" in text or text.startswith("cleaning up call"):
        return "call", True
    if "cleanup after tts" in text:
        return "call", True
    # Keep legacy misspelling for backward compatibility with old log messages.
    if "hangupready" in text or "hangupreay" in text:
        return "call", True
    if "rca_call_end" in text:
        return "call", True
    if "recorded call duration" in text:
        return "call", True

    # Audio / streaming milestones
    if "audio profile resolved and applied" in text:
        return "audio", True
    if "streaming playback" in text and ("started" in text or "stopped" in text):
        return "audio", True
    if "streaming tuning summary" in text:
        return "audio", True
    if "intelligent buffer calculated" in text:
        return "audio", True
    if "stream characterized" in text:
        return "audio", True
    if "continuous stream" in text and ("enabled" in text or "segment boundary" in text):
        return "audio", True
    if "output suppression" in text:
        return "audio", True

    # Provider milestones (from src.engine)
    if "openai session.updated ack received" in text or "session.updated ack received" in text:
        return "provider", True
    if "provider session started" in text:
        return "provider", True
    # Transport milestones
    if "rtp server started for externalmedia transport" in text or "externalmedia channel created" in text:
        return "transport", True
    if "transportcard" in text:
        return "transport", True
    if "audiosocket" in text and ("connected" in text or "disconnected" in text):
        return "transport", True

    # VAD / barge-in milestones
    if "barge-in" in text and ("action applied" in text or "triggered" in text):
        return "vad", True
    if "conversation" in text and "clearing gating" in text:
        return "vad", True

    # Tool milestones
    if "executing post-call tools" in text or "post-call tool" in text:
        return "tools", True
    if "farewell" in text and "intent" in text:
        return "call", True
    if "armed cleanup" in text:
        return "call", True

    # ── Categories (non-milestone) ────────────────────────────────────
    if "externalmedia" in text or "rtp " in text or "ari " in text or "audiosocket" in text:
        return "transport", False
    if "vad" in text or "talk detect" in text or "barge" in text:
        return "vad", False
    # Tools/MCP: avoid overly-broad "tool" matching (provider logs often contain "tool support")
    if "mcp" in text:
        return "tools", False
    if "initialized" in text and "tools" in text:
        return "tools", False
    if any(
        k in text
        for k in (
            "tool calling",
            "tool execution",
            "tool executed",
            "tool invoked",
            "registered tool",
            "initializing default tools",
            "discovered mcp tools",
            "registered mcp tools",
        )
    ):
        return "tools", False
    if "encode" in text or "resample" in text or "normalizer" in text or "gating" in text:
        return "audio", False
    if "provider" in text or comp.startswith("src.providers") or "realtime" in text:
        return "provider", False
    if "config" in text or "configuration" in text:
        return "config", False

    return "call", False


def _build_meta(msg: str, kv: Dict[str, str]) -> Dict[str, str]:
    text = (msg or "").lower()
    meta: Dict[str, str] = {}

    def pick(*keys: str) -> None:
        for k in keys:
            v = kv.get(k)
            if v:
                meta[k] = v

    if "audio profile resolved and applied" in text:
        pick("profile", "wire_format", "context", "provider")
        return meta

    if "openai session.updated ack received" in text or "session.updated ack received" in text:
        pick("input_format", "output_format", "sample_rate", "acknowledged")
        return meta

    if "externalmedia channel created" in text:
        pick("external_media_id", "channel_id")
        return meta

    if "transportcard" in text:
        pick(
            "wire_encoding",
            "wire_sample_rate_hz",
            "transport_encoding",
            "transport_sample_rate_hz",
            "target_encoding",
            "target_sample_rate_hz",
            "provider_encoding",
            "provider_sample_rate_hz",
            "chunk_size_ms",
            "idle_cutoff_ms",
            "transport_source",
        )
        return meta

    if "encode config - reading provider config" in text:
        pick(
            "wire_enc",
            "wire_rate",
            "provider_enc",
            "provider_rate",
            "expected_enc",
            "expected_rate",
            "pcm_rate",
        )
        return meta

    if "encode resample" in text:
        pick("expected_rate", "pcm_rate", "provider")
        return meta

    return meta


def _extract_balanced_braces(text: str, start: int) -> Optional[str]:
    depth = 0
    end = None
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None or end <= start:
        return None
    return text[start:end]


def _parse_event_data_dict(raw_line: str) -> Optional[Dict[str, Any]]:
    """
    Parse `event_data={...}` that is logged as a Python dict literal.

    This avoids changing ai-engine logging while still enabling call-centric fields
    (caller id, dialplan context, channel ids) in the Admin UI.
    """
    idx = raw_line.find("event_data=")
    if idx < 0:
        return None
    brace_start = raw_line.find("{", idx)
    if brace_start < 0:
        return None
    blob = _extract_balanced_braces(raw_line, brace_start)
    if not blob:
        return None
    try:
        parsed = ast.literal_eval(blob)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _meta_from_event_data(data: Dict[str, Any]) -> Dict[str, str]:
    meta: Dict[str, str] = {}
    channel = data.get("channel") or {}
    if isinstance(channel, dict):
        ch_id = channel.get("id")
        ch_name = channel.get("name")
        if ch_id:
            meta["ari_channel_id"] = str(ch_id)
        if ch_name:
            meta["ari_channel_name"] = str(ch_name)

        caller = channel.get("caller") or {}
        if isinstance(caller, dict):
            if caller.get("number"):
                meta["caller_number"] = str(caller.get("number"))
            if caller.get("name"):
                meta["caller_name"] = str(caller.get("name"))

        dialplan = channel.get("dialplan") or {}
        if isinstance(dialplan, dict):
            if dialplan.get("context"):
                meta["dialplan_context"] = str(dialplan.get("context"))
            if dialplan.get("exten"):
                meta["dialplan_exten"] = str(dialplan.get("exten"))
            if dialplan.get("priority") is not None:
                meta["dialplan_priority"] = str(dialplan.get("priority"))
            if dialplan.get("app_name"):
                meta["dialplan_app"] = str(dialplan.get("app_name"))

        if channel.get("protocol_id"):
            meta["ari_protocol_id"] = str(channel.get("protocol_id"))

    if data.get("application"):
        meta["stasis_app"] = str(data.get("application"))
    if data.get("type"):
        meta["ari_event_type"] = str(data.get("type"))
    return meta


def _infer_provider_from_component(component: Optional[str]) -> Optional[str]:
    """
    Best-effort provider inference when logs don't include `provider=...`.
    We rely only on existing ai-engine log `component` names.
    """
    if not component:
        return None
    comp = component.strip()
    if comp.startswith("src.providers."):
        # e.g. src.providers.openai_realtime -> openai_realtime
        rest = comp[len("src.providers.") :]
        # drop any class/function suffixes if present (rare)
        rest = rest.split(":", 1)[0]
        rest = rest.split(".", 1)[0]
        return rest or None
    return None


def parse_log_line(line: str) -> Optional[Tuple[LogEvent, Dict[str, str]]]:
    raw = strip_ansi(line.rstrip("\n"))
    if not raw.strip():
        return None

    # ── JSON format (structlog default: LOG_FORMAT=json) ──────────────────
    stripped = raw.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            call_id = obj.get("call_id") or obj.get("caller_channel_id") or obj.get("channel_id")
            msg = obj.get("event") or obj.get("message") or ""
            level = (obj.get("level") or "info").lower()
            ts = _parse_ts(obj.get("timestamp") or "")
            component = obj.get("component") or obj.get("logger")
            provider = obj.get("provider") or obj.get("provider_name")
            context = obj.get("context") or obj.get("context_name")
            pipeline = obj.get("pipeline") or obj.get("pipeline_name")
            if not provider:
                provider = _infer_provider_from_component(component)
            category, milestone = classify_event(msg, component)
            kv = {k: str(v) for k, v in obj.items() if isinstance(v, (str, int, float, bool))}
            meta = _build_meta(msg, kv)
            if "stasisstart event received" in (msg or "").lower():
                event_data = _parse_event_data_dict(raw)
                if event_data:
                    meta.update(_meta_from_event_data(event_data))
                    if not call_id:
                        call_id = meta.get("ari_channel_id") or call_id
            return (
                LogEvent(
                    ts=ts,
                    level=level,
                    msg=msg,
                    component=component,
                    call_id=call_id,
                    provider=provider,
                    context=context,
                    pipeline=pipeline,
                    category=category,
                    milestone=milestone,
                    meta=meta,
                    raw=raw,
                ),
                kv,
            )
        except json.JSONDecodeError:
            pass  # fall through to text regex parsing

    m = _TS_LEVEL_RE.match(raw)
    if not m:
        # Best-effort: return as "unknown" info
        msg = raw.strip()
        category, milestone = classify_event(msg, None)
        event = LogEvent(
            ts=None,
            level="info",
            msg=msg,
            component=None,
            call_id=None,
            provider=None,
            context=None,
            pipeline=None,
            category=category,
            milestone=milestone,
            meta={},
            raw=raw,
        )
        return event, {}

    ts_s = m.group("ts")
    level = (m.group("level") or "info").lower()
    rest = m.group("rest") or ""

    component = None
    msg = rest.strip()
    kv_str = ""

    m2 = _LOGGER_RE.match(rest)
    if m2:
        msg = (m2.group("msg") or "").strip()
        component = (m2.group("logger") or "").strip()
        kv_str = m2.group("kv") or ""

    kv = _parse_kv(kv_str)
    call_id = _first_present(kv, ("call_id", "caller_channel_id", "channel_id"))
    provider = _first_present(kv, ("provider", "provider_name"))
    context = _first_present(kv, ("context", "context_name"))
    pipeline = _first_present(kv, ("pipeline", "pipeline_name"))
    if not component:
        component = kv.get("component") or None
    if not provider:
        provider = _infer_provider_from_component(component)

    category, milestone = classify_event(msg, component)
    meta = _build_meta(msg, kv)

    # Parse ARI event_data payloads when present (StasisStart carries caller/dialplan)
    if "stasisstart event received" in (msg or "").lower():
        event_data = _parse_event_data_dict(raw)
        if event_data:
            meta.update(_meta_from_event_data(event_data))
            if not call_id:
                call_id = meta.get("ari_channel_id") or call_id

    return (
        LogEvent(
            ts=_parse_ts(ts_s),
            level=level,
            msg=msg,
            component=component,
            call_id=call_id,
            provider=provider,
            context=context,
            pipeline=pipeline,
            category=category,
            milestone=milestone,
            meta=meta,
            raw=raw,
        ),
        kv,
    )


def should_hide_payload(event: LogEvent) -> bool:
    # Hide large transcript/control payloads that swamp troubleshooting
    t = event.raw.lower()
    if "provider control event" in t and "provider_event" in t:
        return True
    if "transcript" in t and "provider_event" in t:
        return True
    if "has_prompt" in t and "has_config" in t:
        return True
    return False
