import json
from pathlib import Path

from scripts.index_call_archives import build_index, render_markdown


def _write_events(archive: Path, *events: dict) -> None:
    log_dir = archive / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "ai-engine.raw.log").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )


def test_build_index_deduplicates_calls_and_excludes_pii(tmp_path: Path) -> None:
    archive = tmp_path / "logs" / "archived" / "rca-20260709-203101"
    (archive / "runtime").mkdir(parents=True)
    (archive / "runtime" / "git-head.txt").write_text("abc123def456\n", encoding="utf-8")
    (archive / "call_id.txt").write_text("call-1\n", encoding="utf-8")
    (archive / "analysis.md").write_text("# Result\n", encoding="utf-8")
    _write_events(
        archive,
        {
            "event": "RCA_CALL_START",
            "timestamp": "2026-07-09T13:00:00Z",
            "call_id": "call-1",
            "provider_name": "google_live",
            "audio_transport": "audiosocket",
            "wire_encoding": "slin",
            "wire_sample_rate_hz": 8000,
            "caller_number": "must-not-leak",
        },
        {
            "event": "RCA_CALL_END",
            "timestamp": "2026-07-09T13:01:00Z",
            "call_id": "call-1",
            "provider_name": "google_live",
            "audio_transport": "audiosocket",
            "call_outcome": "agent_hangup",
            "duration_seconds": 60,
            "media_rx_confirmed": True,
            "transcript": "must-not-leak",
        },
    )

    rows = build_index([tmp_path / "logs"])

    assert len(rows) == 1
    assert rows[0]["call_id"] == "call-1"
    assert rows[0]["provider"] == "google_live"
    assert rows[0]["outcome"] == "agent_hangup"
    assert rows[0]["git_head"] == "abc123def456"
    assert rows[0]["media_rx_confirmed"] is True
    assert "caller_number" not in rows[0]
    assert "transcript" not in rows[0]


def test_render_markdown_links_the_preferred_analysis(tmp_path: Path) -> None:
    archive = tmp_path / "logs" / "archived" / "rca-20260709-203101"
    (archive / "runtime").mkdir(parents=True)
    (archive / "analysis.md").write_text("# Result\n", encoding="utf-8")
    _write_events(
        archive,
        {
            "event": "RCA_CALL_END",
            "timestamp": "2026-07-09T13:01:00Z",
            "call_id": "call-2",
            "pipeline_name": "local_hybrid",
            "audio_transport": "externalmedia",
            "call_outcome": "agent_hangup",
        },
    )

    output = render_markdown(build_index([tmp_path / "logs"]))

    assert "local_hybrid" in output
    assert "externalmedia" in output
    assert str(archive / "analysis.md") in output
