from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "local_test_report.py"
SPEC = importlib.util.spec_from_file_location("local_test_report", SCRIPT)
REPORT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(REPORT)


def test_parse_local_ai_logs_uses_exact_scoped_model_timing(monkeypatch):
    logs = "\n".join([
        "STT FINAL - Emitting transcript call_id=call-1 preview=That's all, goodbye.",
        "LLM RESULT (chat) - Completed in 332.88 ms tokens=5 call_id=call-1",
        "TTS RESULT - Kokoro generated uLaw 8kHz audio: 17000 bytes call_id=call-1",
        "WHISPER STT SUPPRESS - call_id=call-1 source=tool_result",
        "STT FINAL - Emitting transcript call_id=other preview=ignore me",
    ])
    monkeypatch.setattr(REPORT.subprocess, "check_output", lambda *_args, **_kwargs: logs.encode())

    result = REPORT.parse_local_ai_logs(call_id="call-1")

    assert result["llm_last_ms"] == 332.88
    assert result["tts_responses_count"] == 1
    assert result["stt_transcripts_count"] == 1
    assert result["stt_last_transcript"] == "That's all, goodbye."


def test_parse_local_ai_logs_does_not_mix_interleaved_calls(monkeypatch):
    logs = "\n".join([
        "STT FINAL - Emitting transcript call_id=call-1 preview=hello",
        "LLM RESULT (chat) - Completed in 999 ms tokens=8 call_id=call-10",
        "TTS RESULT - Kokoro generated uLaw 8kHz audio: 99999 bytes call_id=call-10",
        "LLM RESULT (chat) - Completed in 900 ms tokens=8 call_id=call-2",
        "TTS RESULT - Kokoro generated uLaw 8kHz audio: 99000 bytes call_id=call-2",
        "LLM RESULT (chat) - Completed in 120 ms tokens=3 call_id=call-1",
        "TTS RESULT - Kokoro generated uLaw 8kHz audio: 12000 bytes call_id=call-1",
    ])
    monkeypatch.setattr(REPORT.subprocess, "check_output", lambda *_args, **_kwargs: logs.encode())

    result = REPORT.parse_local_ai_logs(call_id="call-1")

    assert result["llm_all_ms"] == [120.0]
    assert result["tts_responses_count"] == 1
    assert result["tts_last_bytes"] == 12000


def test_detect_transport_prefers_call_runtime_evidence(monkeypatch, tmp_path):
    logs = "\n".join([
        "RCA_CALL_START call_id=call-1 audio_transport=audiosocket",
        "RCA_CALL_START call_id=call-10 audio_transport=externalmedia",
    ])
    monkeypatch.setattr(REPORT.subprocess, "check_output", lambda *_args, **_kwargs: logs.encode())

    assert REPORT.detect_transport(tmp_path, {}, call_id="call-1") == "AudioSocket"


def test_detect_transport_does_not_invent_externalmedia(tmp_path):
    assert REPORT.detect_transport(tmp_path, {}) == "unknown"


def test_detect_transport_does_not_use_current_config_for_historical_call(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        REPORT.subprocess,
        "check_output",
        lambda *_args, **_kwargs: b"RCA_CALL_START call_id=another-call audio_transport=audiosocket",
    )

    assert REPORT.detect_transport(
        tmp_path,
        {"AUDIO_TRANSPORT": "externalmedia"},
        call_id="historical-call",
    ) == "unknown"


def test_tool_calls_fall_back_to_call_history():
    calls = REPORT.tool_calls_from_history({
        "call_id": "call-1",
        "tool_calls": '[{"name":"hangup_call","result":"success"}]',
        "post_call_tool_calls": '[{"name":"disabled_hook","status":"skipped"}]',
    })

    assert calls == [{
        "name": "hangup_call",
        "status": "success",
        "result": "success",
        "error": "",
        "source": "call_history",
        "call_id": "call-1",
    }]


def test_post_call_history_overrides_misleading_completion_log():
    calls = REPORT.reconcile_post_call_tool_calls(
        [{
            "name": "disabled_hook",
            "status": "success",
            "result": "success",
            "source": "post_call",
            "call_id": "call-1",
        }],
        {
            "call_id": "call-1",
            "post_call_tool_calls": (
                '[{"name":"disabled_hook","status":"skipped",'
                '"error_message":"tool disabled"}]'
            ),
        },
    )

    assert calls == []
