"""Kokoro voice/language must persist, verify and roll back as one switch."""
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
pytest.importorskip("fastapi")

import api.local_ai as local_ai


def test_voice_selection_infers_language_for_env_yaml_and_websocket():
    request = local_ai.SwitchModelRequest(model_type="tts", backend="kokoro", voice="jf_alpha")
    env, yaml = local_ai._build_local_ai_env_and_yaml_updates(request)
    assert env["KOKORO_VOICE"] == "jf_alpha"
    assert env["KOKORO_LANG"] == "j"
    assert yaml["kokoro_voice"] == "jf_alpha"
    assert yaml["kokoro_lang"] == "j"
    payload = local_ai._build_local_ai_ws_switch_payload(request)
    assert payload["kokoro_voice"] == "jf_alpha"
    assert payload["kokoro_lang"] == "j"


def test_language_only_switch_and_explicit_language_are_preserved():
    request = local_ai.SwitchModelRequest(model_type="tts", backend="kokoro", kokoro_lang=" P ")
    env, yaml = local_ai._build_local_ai_env_and_yaml_updates(request)
    assert env["KOKORO_LANG"] == yaml["kokoro_lang"] == "p"
    assert "KOKORO_VOICE" not in env
    request = local_ai.SwitchModelRequest(model_type="tts", backend="kokoro", voice="jf_alpha", kokoro_lang="e")
    assert local_ai._build_local_ai_ws_switch_payload(request)["kokoro_lang"] == "e"


def test_invalid_language_is_rejected_before_mutation():
    request = local_ai.SwitchModelRequest(model_type="tts", backend="kokoro", voice="jf_alpha", kokoro_lang="invalid")
    with pytest.raises(local_ai.HTTPException) as error:
        local_ai._build_local_ai_env_and_yaml_updates(request)
    assert error.value.status_code == 400


def test_same_model_switch_must_verify_actual_language():
    request = local_ai._normalize_switch_request(local_ai.SwitchModelRequest(model_type="tts", backend="kokoro", voice="jf_alpha", model_path="/app/models/tts/kokoro"))
    status = {"voice": "jf_alpha", "lang": "a", "model_path": "/app/models/tts/kokoro"}
    assert local_ai._kokoro_status_matches(request, status) is False
    assert local_ai._kokoro_status_matches(request, dict(status, lang="j")) is True


def test_failed_switch_restores_previous_language_and_absence(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("KOKORO_VOICE=af_heart\nKOKORO_LANG=a\nOTHER=untouched\n")
    updates = {"KOKORO_VOICE": "jf_alpha", "KOKORO_LANG": "j"}
    snapshot = local_ai._rollback_env_snapshot(local_ai._read_env_values(str(env_file), list(updates)), updates)
    local_ai._update_env_file(str(env_file), updates)
    local_ai._restore_env_file(str(env_file), snapshot)
    assert local_ai._read_env_values(str(env_file), ["KOKORO_VOICE", "KOKORO_LANG", "OTHER"]) == {"KOKORO_VOICE": "af_heart", "KOKORO_LANG": "a", "OTHER": "untouched"}
    env_file.write_text("KOKORO_VOICE=af_heart\n")
    snapshot = local_ai._rollback_env_snapshot(local_ai._read_env_values(str(env_file), list(updates)), updates)
    local_ai._update_env_file(str(env_file), updates)
    local_ai._restore_env_file(str(env_file), snapshot)
    assert "KOKORO_LANG" not in env_file.read_text()


@pytest.mark.parametrize("voice", ["custom-voice-id", "jf_custom", "af_unknown"])
def test_custom_voice_does_not_overwrite_existing_language(voice):
    request = local_ai.SwitchModelRequest(model_type="tts", backend="kokoro", voice=voice)
    env, yaml = local_ai._build_local_ai_env_and_yaml_updates(request)
    assert "KOKORO_LANG" not in env
    assert "kokoro_lang" not in yaml
