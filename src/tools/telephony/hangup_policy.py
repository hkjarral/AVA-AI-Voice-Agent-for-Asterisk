from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List


AGENT_HANGUP_MARKER_STRATEGIES = frozenset({"inherit", "extend", "replace"})
MAX_AGENT_END_CALL_MARKERS = 100
MAX_AGENT_END_CALL_MARKER_LENGTH = 160


class HangupPolicyConfigError(ValueError):
    """Raised when an Agent-scoped hangup marker policy is malformed."""

DEFAULT_HANGUP_MARKERS: Dict[str, List[str]] = {
    "end_call": [
        "no transcript",
        "no transcript needed",
        "don't send a transcript",
        "no thanks",
        "no thank you",
        "thank you",
        "thanks",
        "that's all",
        "nothing else",
        "end call",
        "hang up",
        "goodbye",
        "bye",
        "have a good day",
        "have a great day",
        "take care",
        "talk to you later",
    ],
    "assistant_farewell": [
        "goodbye",
        "bye",
        "thanks for calling",
        "thank you for calling",
        "have a great day",
        "take care",
    ],
    "affirmative": [
        "yes",
        "yeah",
        "yep",
        "correct",
        "that's correct",
        "thats correct",
        "that's right",
        "thats right",
        "right",
        "exactly",
        "affirmative",
    ],
    "negative": [
        "no",
        "nope",
        "nah",
        "negative",
        "don't",
        "dont",
        "do not",
        "not",
        "not needed",
        "no need",
        "no thanks",
        "no thank you",
        "decline",
        "skip",
    ],
}

DEFAULT_HANGUP_POLICY: Dict[str, Any] = {
    "mode": "normal",
    "enforce_transcript_offer": True,
    "block_during_contact_capture": True,
    "markers": DEFAULT_HANGUP_MARKERS,
}

_END_CALL_FUZZY_PATTERNS: List[tuple[str, str]] = [
    (r"\bhand[\s-]*up\b", "hang up"),
    (r"\bhangup\b", "hang up"),
    (r"\bhand[\s-]*off\b", "hang up"),
    (r"\b(and|end)\s+the\s+call\b", "end call"),
    (r"\b(and|end)\s+call\b", "end call"),
    (r"\bhang\s+up\s+the\s+(?:cob|cab|cop|cause)\b", "hang up the call"),
    (r"\bhand\s+up\s+the\s+(?:call|cob|cab|cop|cause)\b", "hang up the call"),
]


def _normalize_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _normalize_end_call_text(value: str) -> str:
    text = _normalize_text(value)
    if not text:
        return text
    for pattern, replacement in _END_CALL_FUZZY_PATTERNS:
        text = re.sub(pattern, replacement, text)
    return _normalize_text(text)


def _coerce_marker_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[\n,]+", value)
        return [p.strip().lower() for p in parts if p.strip()]
    if isinstance(value, (list, tuple, set)):
        out: List[str] = []
        for item in value:
            s = str(item).strip().lower()
            if s:
                out.append(s)
        return out
    return []


def _dedupe(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def normalize_marker_list(value: Any, fallback: List[str]) -> List[str]:
    items = _coerce_marker_list(value)
    if not items:
        items = list(fallback)
    return _dedupe(items)


def normalize_hangup_policy(policy: Any) -> Dict[str, Any]:
    if not isinstance(policy, dict):
        policy = {}

    mode = str(policy.get("mode") or DEFAULT_HANGUP_POLICY["mode"]).strip().lower()
    if mode not in ("relaxed", "normal", "strict"):
        mode = DEFAULT_HANGUP_POLICY["mode"]

    markers_cfg = policy.get("markers") if isinstance(policy.get("markers"), dict) else {}

    markers = {
        "end_call": normalize_marker_list(markers_cfg.get("end_call"), DEFAULT_HANGUP_MARKERS["end_call"]),
        "assistant_farewell": normalize_marker_list(markers_cfg.get("assistant_farewell"), DEFAULT_HANGUP_MARKERS["assistant_farewell"]),
        "affirmative": normalize_marker_list(markers_cfg.get("affirmative"), DEFAULT_HANGUP_MARKERS["affirmative"]),
        "negative": normalize_marker_list(markers_cfg.get("negative"), DEFAULT_HANGUP_MARKERS["negative"]),
    }

    return {
        "mode": mode,
        "enforce_transcript_offer": bool(
            policy.get("enforce_transcript_offer", DEFAULT_HANGUP_POLICY["enforce_transcript_offer"])
        ),
        "block_during_contact_capture": bool(
            policy.get("block_during_contact_capture", DEFAULT_HANGUP_POLICY["block_during_contact_capture"])
        ),
        "markers": markers,
    }


def normalize_agent_hangup_policy(value: Any) -> Dict[str, Any]:
    """Validate and canonicalize an Agent's end-call marker override.

    An empty document means inherit. ``extend`` appends Agent markers to the
    global list, while ``replace`` makes the Agent list authoritative. Marker
    counts and lengths are bounded because this document crosses the Local AI
    WebSocket boundary once per call.
    """
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError) as exc:
            raise HangupPolicyConfigError(
                "hangup_policy_json must contain valid JSON"
            ) from exc
    if not isinstance(value, dict):
        raise HangupPolicyConfigError("hangup policy must be a JSON object")

    if any(not isinstance(key, str) for key in value):
        raise HangupPolicyConfigError("hangup policy field names must be strings")
    unknown = sorted(set(value) - {"strategy", "end_call"})
    if unknown:
        raise HangupPolicyConfigError(
            f"unsupported hangup policy field(s): {', '.join(unknown)}"
        )

    strategy = str(value.get("strategy") or "inherit").strip().lower()
    if strategy not in AGENT_HANGUP_MARKER_STRATEGIES:
        raise HangupPolicyConfigError(
            "hangup strategy must be inherit, extend, or replace"
        )
    raw_markers = value.get("end_call")
    if strategy == "inherit":
        if raw_markers not in (None, []):
            raise HangupPolicyConfigError(
                "end_call markers may only be set when strategy is extend or replace"
            )
        return {}
    if not isinstance(raw_markers, list):
        raise HangupPolicyConfigError("end_call must be an array")
    if len(raw_markers) > MAX_AGENT_END_CALL_MARKERS:
        raise HangupPolicyConfigError(
            f"end_call supports at most {MAX_AGENT_END_CALL_MARKERS} markers"
        )

    markers: List[str] = []
    seen = set()
    for raw_marker in raw_markers:
        if not isinstance(raw_marker, str):
            raise HangupPolicyConfigError("end_call entries must be strings")
        marker = " ".join(raw_marker.strip().lower().split())
        if not marker:
            raise HangupPolicyConfigError("end_call entries must not be empty")
        if len(marker) > MAX_AGENT_END_CALL_MARKER_LENGTH:
            raise HangupPolicyConfigError(
                f"end_call entries must be at most {MAX_AGENT_END_CALL_MARKER_LENGTH} characters"
            )
        if marker not in seen:
            markers.append(marker)
            seen.add(marker)
    if not markers:
        raise HangupPolicyConfigError(
            f"{strategy} requires at least one end_call marker"
        )
    return {"strategy": strategy, "end_call": markers}


def dump_agent_hangup_policy(value: Any) -> str | None:
    """Return stable JSON for Agent storage, or ``None`` for inheritance."""
    normalized = normalize_agent_hangup_policy(value)
    if not normalized:
        return None
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def resolve_effective_hangup_policy(
    tools_cfg: Any,
    agent_policy: Any = None,
) -> Dict[str, Any]:
    """Resolve global + Agent markers and return policy plus audit metadata."""
    policy = normalize_hangup_policy(
        tools_cfg.get("hangup_call", {}).get("policy")
        if isinstance(tools_cfg, dict)
        and isinstance(tools_cfg.get("hangup_call"), dict)
        else {}
    )
    override = normalize_agent_hangup_policy(agent_policy)
    strategy = override.get("strategy") or "inherit"
    source = "global"
    if strategy == "extend":
        policy["markers"]["end_call"] = _dedupe(
            list(policy["markers"]["end_call"]) + list(override["end_call"])
        )
        source = "agent_extend"
    elif strategy == "replace":
        policy["markers"]["end_call"] = list(override["end_call"])
        source = "agent_replace"

    markers = list(policy["markers"]["end_call"])
    digest = hashlib.sha256(
        json.dumps(markers, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "policy": policy,
        "source": source,
        "strategy": strategy,
        "marker_count": len(markers),
        "marker_digest": digest,
    }


def resolve_hangup_policy(tools_cfg: Any) -> Dict[str, Any]:
    if isinstance(tools_cfg, dict):
        hangup_cfg = tools_cfg.get("hangup_call")
        if isinstance(hangup_cfg, dict):
            return normalize_hangup_policy(hangup_cfg.get("policy"))
    return normalize_hangup_policy({})


def text_contains_marker(text: str, markers: Iterable[str]) -> bool:
    t = _normalize_text(text)
    if not t:
        return False
    for m in markers:
        if not m:
            continue
        m = str(m).strip().lower()
        if not m:
            continue
        # Multi-word markers use substring matching after normalization.
        if " " in m:
            if m in t:
                return True
            continue
        # Single-word markers should match whole words to avoid false positives (e.g., "no" in "notification").
        if re.search(rf"(?:^|\b){re.escape(m)}(?:\b|$)", t):
            return True
    return False


def text_contains_marker_word(text: str, markers: Iterable[str]) -> bool:
    t = _normalize_text(text)
    if not t:
        return False
    for m in markers:
        if re.search(rf"(?:^|\b){re.escape(m)}(?:\b|$)", t):
            return True
    return False


def text_contains_end_call_intent(text: str, markers: Iterable[str]) -> bool:
    """
    End-of-call intent matcher with light fuzzy normalization for STT artifacts.

    Examples:
    - "hand up the call" -> "hang up the call"
    - "and the call" -> "end call"
    """
    normalized = _normalize_end_call_text(text)
    if not normalized:
        return False
    if text_contains_marker(normalized, markers):
        return True
    if normalized != _normalize_text(text):
        return text_contains_marker(_normalize_text(text), markers)
    return False


def text_is_short_polite_closing(text: str) -> bool:
    """
    Detect short gratitude closings even when custom marker lists are too narrow.

    This is intentionally conservative to avoid mid-call false positives:
    - utterance must be short (<= 8 words after normalization)
    - must end in gratitude, optionally after a recognized closing prefix
    """
    normalized = _normalize_end_call_text(text)
    if not normalized:
        return False
    compact = _normalize_text(re.sub(r"[^a-z0-9\s]", " ", normalized))
    if not compact:
        return False
    if len(compact.split()) > 8:
        return False
    return bool(
        re.fullmatch(
            r"(?:(?:ok(?:ay)?|no(?:pe)?(?!\s+(?:thank\s+you|thanks)\b)|never\s+mind|nevermind|leave\s+it|that\s+s\s+(?:all|it)|thats\s+(?:all|it)|nothing\s+else)\s+)*"
            r"(?:thank\s+you|thanks)(?:\s+(?:so\s+much|very\s+much))?(?:\s+(?:bye|goodbye))?",
            compact,
        )
    )
