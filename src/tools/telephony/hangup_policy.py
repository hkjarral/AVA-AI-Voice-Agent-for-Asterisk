from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

DEFAULT_HANGUP_MARKERS: Dict[str, List[str]] = {
    "end_call": [
        "no transcript",
        "no transcript needed",
        "don't send a transcript",
        "no thanks",
        "that's all",
        "nothing else",
        "end call",
        "hang up",
        "goodbye",
        "bye",
    ],
    "assistant_farewell": [
        "goodbye",
        "bye",
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


def _normalize_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


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
