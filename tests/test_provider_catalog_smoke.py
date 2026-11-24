"""Smoke reports expose protocol identifiers without provider/private messages."""
import logging
import asyncio
import json
import runpy
import sys
from types import SimpleNamespace
import wave
from pathlib import Path

import pytest
import structlog


@pytest.fixture(scope="module")
def smoke():
    disabled = logging.root.manager.disable
    configuration = structlog.get_config()
    try:
        module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/provider_catalog_smoke.py"))
        yield module["run_one"].__globals__
    finally:
        logging.disable(disabled)
        structlog.configure(**configuration)


@pytest.fixture
def safe_error_details(smoke):
    return smoke["safe_error_details"]


def test_embedded_http_error_keeps_only_safe_identifiers(safe_error_details):
    error = RuntimeError('OpenAI STT request failed (status 400): {"error":{"message":"private prompt and secret","code":"unsupported_parameter","param":"temperature"}}')
    assert safe_error_details(error) == {
        "error_type": "RuntimeError", "http_status": 400,
        "provider_error_code": "unsupported_parameter", "provider_error_param": "temperature",
    }


@pytest.mark.parametrize("value", ["secret value", "https://private.example/key", "sk-xxx\\nsecret", "x" * 81, "sk-private-shaped-token", "private_customer_name", "private.project.secret"])
def test_non_identifier_values_are_not_emitted(safe_error_details, value):
    error = RuntimeError('{"code":"' + value + '","param":"' + value + '"}')
    assert safe_error_details(error) == {"error_type": "RuntimeError"}


def test_typed_http_status_is_supported_without_printing_message(safe_error_details):
    error = RuntimeError("private operator information")
    error.status = 403
    assert safe_error_details(error) == {"error_type": "RuntimeError", "http_status": 403}


@pytest.mark.parametrize("preset,role", [
    ("openai_realtime_tts", "openai_tts"),
    ("openai_sol_llm", "openai_llm"),
    ("openai_astra_llm", "openai_llm"),
    ("openai_stt", "openai_stt"),
])
def test_openai_presets_use_role_credentials_before_full_provider(smoke, monkeypatch, tmp_path, preset, role):
    secret = tmp_path / "role-key"
    secret.write_text("role-file-key")
    secret.chmod(0o600)
    config = {"providers": {role: {"api_key_file": str(secret)}, "openai": {"api_key": "wrong-full-key"}, "openai_realtime": {"api_key": "wrong-realtime-key"}}}
    _, key, report = smoke["prerequisites"](preset, config)
    assert key == "role-file-key"
    assert report["credential_component"] == role
    assert report["credential_source"] == "api_key_file"
    assert "role-file-key" not in json.dumps(report)


def test_exact_component_uses_its_env_key_and_never_another_role(smoke, monkeypatch):
    monkeypatch.setenv("PROBE_KEY", "exact-probe-key")
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-global-key")
    config = {"providers": {"probe_tts": {"type": "openai", "api_key_env": "PROBE_KEY"}, "openai_tts": {"api_key": "wrong-role-key"}, "openai_realtime": {"api_key": "wrong-full-key"}}}
    _, key, report = smoke["prerequisites"]("openai_realtime_tts", config, "probe_tts")
    assert key == "exact-probe-key"
    assert report["credential_component"] == "probe_tts"
    assert report["credential_source"] == "api_key_env"
    monkeypatch.delenv("PROBE_KEY")
    _, key, report = smoke["prerequisites"]("openai_realtime_tts", config, "probe_tts")
    assert not key
    assert report["status"] == "SKIP"
    assert report["reason"] == "credentials_unavailable"


def test_realtime_tts_failure_is_from_exact_component_key_not_full_provider(smoke, monkeypatch):
    observed = []
    monkeypatch.setenv("PROBE_KEY", "exact-probe-key")
    class Adapter:
        def __init__(self, name, unused, config): observed.append(config.api_key)
        async def start(self): pass
        async def open_call(self, *args): pass
        async def synthesize(self, *args):
            raise RuntimeError('status 404 {"code":"model_not_found","message":"private-provider-message"}')
            yield b""
        async def close_call(self, *args): observed.append("closed")
        async def stop(self): observed.append("stopped")
    monkeypatch.setitem(sys.modules, "src.pipelines.openai", SimpleNamespace(OpenAISTTAdapter=Adapter, OpenAITTSAdapter=Adapter, OpenAILLMAdapter=Adapter))
    config = {"providers": {"probe_tts": {"type": "openai", "api_key_env": "PROBE_KEY"}, "openai": {"api_key": "wrong-full-key"}}}
    result = asyncio.run(smoke["run_one"]("openai_realtime_tts", config, 2, {}, "probe_tts"))
    assert observed == ["exact-probe-key", "closed", "stopped"]
    assert result["status"] == "FAIL"
    assert result["credential_component"] == "probe_tts"
    assert result["provider_error_code"] == "model_not_found"
    assert result["http_status"] == 404
    assert "private-provider-message" not in json.dumps(result)
    assert "exact-probe-key" not in json.dumps(result)


def test_missing_or_wrong_component_does_not_borrow_credentials(smoke, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-global-key")
    config = {"providers": {"other_tts": {"type": "elevenlabs", "api_key": "private-other-key"}}}
    for component, reason in [("missing_tts", "configured_component_unavailable"), ("other_tts", "component_provider_mismatch")]:
        _, key, report = smoke["prerequisites"]("openai_realtime_tts", config, component)
        assert key == ""
        assert report["reason"] == reason


def test_configured_role_with_missing_secret_does_not_borrow_full_key(smoke, monkeypatch):
    monkeypatch.delenv("MISSING_ROLE_KEY", raising=False)
    config = {"providers": {"openai_tts": {"api_key_env": "MISSING_ROLE_KEY"}, "openai": {"api_key": "wrong-full-key"}}}
    _, key, report = smoke["prerequisites"]("openai_realtime_tts", config)
    assert key == ""
    assert report["credential_component"] == "openai_tts"
    assert report["status"] == "SKIP"


def test_an_explicitly_named_preset_component_keeps_its_own_credentials(smoke):
    config = {"providers": {"openai_sol_llm": {"api_key": "specific-key"}, "openai_llm": {"api_key": "shared-role-key"}}}
    _, key, report = smoke["prerequisites"]("openai_sol_llm", config)
    assert key == "specific-key"
    assert report["credential_component"] == "openai_sol_llm"


def test_exact_component_keeps_its_voice_reference_private(smoke):
    config = {"providers": {"private_tts": {"type": "fishaudio", "api_key": "private-key", "reference_id": "private-reference"}}}
    cfg, key, report = smoke["prerequisites"]("fishaudio_tts", config, "private_tts")
    assert cfg["reference_id"] == "private-reference"
    assert key == "private-key"
    assert report["voice_available"] is True
    assert "private-reference" not in json.dumps(report)
    assert "private-key" not in json.dumps(report)


def test_stt_without_speech_skips_before_constructing_adapter(smoke):
    result = asyncio.run(smoke["run_one"]("openai_stt", {"providers": {"openai_stt": {"api_key": "test-key"}}}, 2, {}))
    assert result["status"] == "SKIP"
    assert result["reason"] == "speech_input_unavailable"


@pytest.mark.parametrize("transcript,expected", [("", "FAIL"), ("   ", "FAIL"), ("synthetic test", "PASS")])
def test_stt_requires_a_nonempty_transcript_and_cleans_up(smoke, monkeypatch, transcript, expected):
    events = []
    class Adapter:
        def __init__(self, name, unused, config):
            assert config.api_key == "test-key"
            assert config.stt_base_url == "https://gateway.example/transcribe"
        async def start(self): events.append("start")
        async def open_call(self, *args): events.append("open")
        async def transcribe(self, call_id, pcm, rate, options):
            assert pcm == b"synthetic-pcm"
            assert rate == 16000
            return transcript
        async def close_call(self, *args): events.append("close")
        async def stop(self): events.append("stop")
    monkeypatch.setitem(sys.modules, "src.pipelines.openai", SimpleNamespace(OpenAISTTAdapter=Adapter, OpenAITTSAdapter=Adapter, OpenAILLMAdapter=Adapter))
    config = {"providers": {"openai_stt": {"api_key": "test-key", "stt_base_url": "https://gateway.example/transcribe"}}}
    result = asyncio.run(smoke["run_one"]("openai_stt", config, 2, {"pcm16_16k": b"synthetic-pcm"}))
    assert result["status"] == expected
    assert events == ["start", "open", "close", "stop"]
    if transcript.strip():
        assert transcript.strip() not in json.dumps(result)


def test_groq_arabic_probe_sends_arabic_speech(smoke, monkeypatch):
    spoken = []
    class Adapter:
        def __init__(self, *args): pass
        async def start(self): pass
        async def open_call(self, *args): pass
        async def synthesize(self, call_id, text, options):
            spoken.append(text)
            yield b"audio"
        async def close_call(self, *args): pass
        async def stop(self): pass
    monkeypatch.setitem(sys.modules, "src.pipelines.groq", SimpleNamespace(GroqTTSAdapter=Adapter))
    result = asyncio.run(smoke["run_one"]("groq_arabic_tts", {"providers": {"groq_tts": {"api_key": "test-key"}}}, 2, {}))
    assert result["status"] == "PASS"
    assert spoken == [smoke["ARABIC_TEXT"]]
    assert any("\u0600" <= char <= "\u06ff" for char in spoken[0])


def test_speech_fixture_validates_real_pcm_input(smoke, tmp_path):
    path = tmp_path / "speech.wav"
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16000)
        target.writeframes(b"\x01\x00" * 1600)
    assert smoke["speech_fixture"](path) == b"\x01\x00" * 1600
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16000)
        target.writeframes(b"\x00\x00" * 1600)
    with pytest.raises(ValueError): smoke["speech_fixture"](path)


def test_dry_run_reports_prerequisites_without_requests_or_private_values(smoke, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "private-key")
    monkeypatch.setattr(sys, "argv", ["smoke", "--providers", "openai_sol_llm,openai_stt,elevenlabs_tts"])
    assert asyncio.run(smoke["main"]()) == 0
    output = capsys.readouterr().out
    assert "private-key" not in output
    report = json.loads(output)
    assert report["requests_sent"] == 0
    assert report["configuration_changed"] is False
    llm, stt, eleven = report["prerequisites"]
    assert llm["credential_available"] is True
    assert llm["status"] == "READY"
    assert stt["reason"] == "speech_input_unavailable"
    assert eleven["voice_available"] is False


def test_cli_component_override_reports_exact_prerequisites_without_requests(smoke, monkeypatch, capsys, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(json.dumps({"providers": {"custom_tts": {"type": "openai", "api_key_env": "CUSTOM_PROBE_KEY"}}}))
    monkeypatch.setenv("CUSTOM_PROBE_KEY", "private-component-key")
    monkeypatch.setattr(sys, "argv", ["smoke", "--config", str(config), "--providers", "openai_realtime_tts", "--component", "openai_realtime_tts=custom_tts"])
    assert asyncio.run(smoke["main"]()) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["requests_sent"] == 0
    assert report["prerequisites"][0]["credential_component"] == "custom_tts"
    assert report["prerequisites"][0]["credential_source"] == "api_key_env"
    assert report["prerequisites"][0]["status"] == "READY"
    assert "private-component-key" not in output
