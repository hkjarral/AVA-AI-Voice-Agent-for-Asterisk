#!/usr/bin/env python3
"""Build a release-evidence index from archived structured AAVA call logs.

The index intentionally excludes caller numbers, transcripts, prompts, and tool
arguments. It records only the fields needed to select regression calls and to
prove which revision/provider/transport combination was exercised.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


CALL_EVENTS = {"RCA_CALL_START", "RCA_CALL_END"}


def _archive_root(path: Path) -> Path | None:
    for parent in (path, *path.parents):
        if parent.name.startswith("rca-"):
            return parent
    return None


def _source_score(archive: Path, call_id: str) -> tuple[int, int, str]:
    score = 0
    call_id_path = archive / "call_id.txt"
    try:
        if call_id_path.read_text(encoding="utf-8").strip() == call_id:
            score += 100
    except OSError:
        pass
    if (archive / "analysis.md").is_file():
        score += 10
    if "archived" in archive.parts:
        score += 5
    # A shorter path is normally the purpose-built archive rather than a nested
    # duplicate produced by an older collector.
    return score, -len(archive.parts), str(archive)


def _git_head(archive: Path) -> str | None:
    path = archive / "runtime" / "git-head.txt"
    try:
        value = path.read_text(encoding="utf-8").strip().splitlines()[0]
    except (OSError, IndexError):
        return None
    return value or None


def _iter_log_files(roots: Iterable[Path]) -> Iterable[Path]:
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if "ai-engine" not in path.name and path.name != "key-events.raw.log":
                continue
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                yield path


def _parse_event(raw: str) -> dict[str, Any] | None:
    if not any(event in raw for event in CALL_EVENTS):
        return None
    start = raw.find("{")
    if start < 0:
        return None
    try:
        payload = json.loads(raw[start:])
    except (TypeError, json.JSONDecodeError):
        return None
    if payload.get("event") not in CALL_EVENTS or not payload.get("call_id"):
        return None
    return payload


def build_index(roots: Iterable[Path]) -> list[dict[str, Any]]:
    calls: dict[str, dict[str, Any]] = {}
    source_scores: dict[str, tuple[int, int, str]] = {}

    for log_path in _iter_log_files(roots):
        archive = _archive_root(log_path)
        if archive is None:
            continue
        try:
            lines = log_path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with lines:
            for raw in lines:
                payload = _parse_event(raw)
                if payload is None:
                    continue
                call_id = str(payload["call_id"])
                row = calls.setdefault(
                    call_id,
                    {
                        "call_id": call_id,
                        "start_timestamp": None,
                        "end_timestamp": None,
                        "provider": None,
                        "pipeline": None,
                        "context": None,
                        "transport": None,
                        "wire_encoding": None,
                        "wire_sample_rate_hz": None,
                        "outcome": None,
                        "duration_seconds": None,
                        "media_rx_confirmed": None,
                        "git_head": None,
                        "source_archive": None,
                        "analysis": None,
                    },
                )

                score = _source_score(archive, call_id)
                current_score = source_scores.get(call_id)
                if current_score is None or score > current_score:
                    source_scores[call_id] = score
                    for field in (
                        "start_timestamp",
                        "end_timestamp",
                        "provider",
                        "pipeline",
                        "context",
                        "transport",
                        "wire_encoding",
                        "wire_sample_rate_hz",
                        "outcome",
                        "duration_seconds",
                        "media_rx_confirmed",
                    ):
                        row[field] = None
                    row["source_archive"] = str(archive)
                    row["git_head"] = _git_head(archive)
                    analysis = archive / "analysis.md"
                    row["analysis"] = str(analysis) if analysis.is_file() else None
                elif score < current_score:
                    continue

                if payload["event"] == "RCA_CALL_START":
                    row["start_timestamp"] = row["start_timestamp"] or payload.get("timestamp")
                    row["wire_encoding"] = row["wire_encoding"] or payload.get("wire_encoding")
                    row["wire_sample_rate_hz"] = row["wire_sample_rate_hz"] or payload.get("wire_sample_rate_hz")
                else:
                    row["end_timestamp"] = payload.get("timestamp") or row["end_timestamp"]
                    row["outcome"] = payload.get("call_outcome") or row["outcome"]
                    row["duration_seconds"] = payload.get("duration_seconds") or row["duration_seconds"]
                    if payload.get("media_rx_confirmed") is not None:
                        row["media_rx_confirmed"] = bool(payload["media_rx_confirmed"])

                row["provider"] = payload.get("provider_name") or payload.get("provider") or row["provider"]
                row["pipeline"] = payload.get("pipeline_name") or row["pipeline"]
                row["context"] = payload.get("context_name") or payload.get("context") or row["context"]
                row["transport"] = payload.get("audio_transport") or payload.get("transport") or row["transport"]

    return sorted(
        calls.values(),
        key=lambda row: (row.get("start_timestamp") or row.get("end_timestamp") or "", row["call_id"]),
    )


def render_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Call ID | Started | Provider / pipeline | Transport | Outcome | Media RX | Git SHA | Evidence |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        target = row.get("pipeline") or row.get("provider") or "unknown"
        evidence = row.get("analysis") or row.get("source_archive") or ""
        sha = str(row.get("git_head") or "")[:12]
        media = "yes" if row.get("media_rx_confirmed") is True else "no" if row.get("media_rx_confirmed") is False else "unknown"
        lines.append(
            "| {call_id} | {started} | {target} | {transport} | {outcome} | {media} | {sha} | `{evidence}` |".format(
                call_id=row["call_id"],
                started=row.get("start_timestamp") or "",
                target=target,
                transport=row.get("transport") or "",
                outcome=row.get("outcome") or "",
                media=media,
                sha=sha,
                evidence=evidence,
            )
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "roots",
        nargs="*",
        type=Path,
        default=[Path("logs/archived"), Path("logs/remote")],
        help="Archive roots to scan (default: logs/archived logs/remote)",
    )
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args()

    rows = build_index(args.roots)
    if args.format == "json":
        print(json.dumps(rows, indent=2, sort_keys=True))
    else:
        print(render_markdown(rows), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
