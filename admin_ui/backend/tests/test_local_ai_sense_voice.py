"""Admin API handling of the SenseVoice settings (issue #407): switch mapping,
catalog-driven offline mode, status verification, and rollback snapshot."""
import re
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

pytest.importorskip("fastapi")

import api.local_ai as admin_local_ai_module  # noqa: E402


@pytest.fixture
def admin_local_ai():
    return admin_local_ai_module


def test_admin_languages_match_server(admin_local_ai):
    constants = (REPO_ROOT / "local_ai_server" / "constants.py").read_text(encoding="utf-8")
    match = re.search(r"^SENSE_VOICE_LANGUAGES = (\(.*?\))$", constants, re.M)
    assert match, "SENSE_VOICE_LANGUAGES not found in local_ai_server/constants.py"
    assert admin_local_ai.SENSE_VOICE_LANGUAGES == eval(match.group(1))  # noqa: S307 - literal tuple


def test_admin_switch_maps_sense_voice_settings(admin_local_ai):
    req = admin_local_ai.SwitchModelRequest(
        model_type="stt",
        backend="sherpa",
        model_path="/app/models/stt/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
        sherpa_sense_voice_language=" KO ",
        sherpa_sense_voice_use_itn=False,
    )
    req = admin_local_ai._normalize_switch_request(req)
    env, yaml_updates = admin_local_ai._build_local_ai_env_and_yaml_updates(req)
    assert env["SHERPA_SENSE_VOICE_LANGUAGE"] == "ko"
    assert env["SHERPA_SENSE_VOICE_USE_ITN"] == "false"
    assert yaml_updates["sherpa_sense_voice_language"] == "ko"
    assert yaml_updates["sherpa_sense_voice_use_itn"] is False
    payload = admin_local_ai._build_local_ai_ws_switch_payload(req)
    assert payload["sherpa_sense_voice_language"] == "ko"
    assert payload["sherpa_sense_voice_use_itn"] is False


def test_admin_settings_only_switch_reaches_payload(admin_local_ai):
    # Changing only ITN (no model path) must still be forwarded and persisted.
    req = admin_local_ai.SwitchModelRequest(model_type="stt", backend="sherpa", sherpa_sense_voice_use_itn=True)
    env, _ = admin_local_ai._build_local_ai_env_and_yaml_updates(admin_local_ai._normalize_switch_request(req))
    assert env["SHERPA_SENSE_VOICE_USE_ITN"] == "true"
    assert admin_local_ai._build_local_ai_ws_switch_payload(req)["sherpa_sense_voice_use_itn"] is True


def test_admin_catalog_offline_model_infers_offline_mode(admin_local_ai):
    req = admin_local_ai.SwitchModelRequest(
        model_type="stt",
        backend="sherpa",
        model_path="/app/models/stt/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
    )
    req = admin_local_ai._normalize_switch_request(req)
    assert req.sherpa_model_type == "offline"
    env, _ = admin_local_ai._build_local_ai_env_and_yaml_updates(req)
    assert env["SHERPA_MODEL_TYPE"] == "offline"


def test_admin_explicit_model_type_wins_over_catalog(admin_local_ai):
    req = admin_local_ai.SwitchModelRequest(
        model_type="stt",
        backend="sherpa",
        model_path="/app/models/stt/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
        sherpa_model_type="online",
    )
    assert admin_local_ai._normalize_switch_request(req).sherpa_model_type == "online"


def test_admin_unknown_model_does_not_infer_mode(admin_local_ai):
    req = admin_local_ai.SwitchModelRequest(model_type="stt", backend="sherpa", model_path="/app/models/stt/custom")
    assert admin_local_ai._normalize_switch_request(req).sherpa_model_type is None


def _stt_status(**overrides):
    status = {
        "path": "/m/sv",
        "sherpa_model_type": "offline",
        "sherpa_model_family": "sense_voice",
        "sherpa_sense_voice_language": "auto",
        "sherpa_sense_voice_use_itn": True,
    }
    status.update(overrides)
    return status


def test_admin_verification_checks_settings_with_same_path(admin_local_ai):
    req = admin_local_ai.SwitchModelRequest(
        model_type="stt", backend="sherpa", model_path="/m/sv", sherpa_sense_voice_language="en"
    )
    assert admin_local_ai._sherpa_status_matches(req, _stt_status()) is False
    assert admin_local_ai._sherpa_status_matches(req, _stt_status(sherpa_sense_voice_language="en")) is True


def test_admin_verification_checks_itn_and_mode(admin_local_ai):
    req = admin_local_ai.SwitchModelRequest(
        model_type="stt", backend="sherpa", sherpa_model_type="offline", sherpa_sense_voice_use_itn=False
    )
    assert admin_local_ai._sherpa_status_matches(req, _stt_status()) is False
    assert admin_local_ai._sherpa_status_matches(req, _stt_status(sherpa_sense_voice_use_itn=False)) is True
    assert (
        admin_local_ai._sherpa_status_matches(
            req, _stt_status(sherpa_sense_voice_use_itn=False, sherpa_model_type="online")
        )
        is False
    )


def test_admin_verification_ignores_settings_for_transducer(admin_local_ai):
    req = admin_local_ai.SwitchModelRequest(model_type="stt", backend="sherpa", sherpa_sense_voice_language="en")
    status = _stt_status(sherpa_model_family="transducer", sherpa_sense_voice_language=None, sherpa_sense_voice_use_itn=None)
    assert admin_local_ai._sherpa_status_matches(req, status) is True


def test_admin_rollback_snapshot_preserves_absence_for_new_keys(admin_local_ai):
    previous = {"SHERPA_MODEL_PATH": "/m/old"}
    updates = {"SHERPA_SENSE_VOICE_LANGUAGE": "ja", "SHERPA_SENSE_VOICE_USE_ITN": "false"}
    snapshot = admin_local_ai._rollback_env_snapshot(previous, updates)
    assert snapshot == {
        "SHERPA_MODEL_PATH": "/m/old",
        "SHERPA_SENSE_VOICE_LANGUAGE": None,
        "SHERPA_SENSE_VOICE_USE_ITN": None,
    }
    # Existing values are kept, and untouched keys are not added.
    assert admin_local_ai._rollback_env_snapshot({"SHERPA_SENSE_VOICE_LANGUAGE": "zh"}, {"SHERPA_SENSE_VOICE_LANGUAGE": "ja"}) == {
        "SHERPA_SENSE_VOICE_LANGUAGE": "zh"
    }
    assert admin_local_ai._rollback_env_snapshot({}, {"SHERPA_MODEL_PATH": "/m"}) == {"SHERPA_MODEL_PATH": None}


@pytest.mark.parametrize("previous_mode", [None, "online", ""])
def test_catalog_switch_rollback_restores_original_model_mode(tmp_path, admin_local_ai, previous_mode):
    env_file = tmp_path / ".env"
    original = "SHERPA_MODEL_PATH=/m/streaming\n# operator note\n"
    if previous_mode is not None:
        original += f"SHERPA_MODEL_TYPE={previous_mode}\n"
    env_file.write_text(original)
    previous = admin_local_ai._read_env_values(str(env_file), ["SHERPA_MODEL_PATH", "SHERPA_MODEL_TYPE"])
    req = admin_local_ai._normalize_switch_request(admin_local_ai.SwitchModelRequest(
        model_type="stt", backend="sherpa",
        model_path="/app/models/stt/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
    ))
    updates, _ = admin_local_ai._build_local_ai_env_and_yaml_updates(req)
    assert updates["SHERPA_MODEL_TYPE"] == "offline"
    snapshot = admin_local_ai._rollback_env_snapshot(previous, updates)
    admin_local_ai._update_env_file(str(env_file), updates)
    admin_local_ai._restore_env_file(str(env_file), snapshot)
    assert admin_local_ai._read_env_values(str(env_file), ["SHERPA_MODEL_PATH", "SHERPA_MODEL_TYPE"]) == previous
    assert "# operator note" in env_file.read_text()
