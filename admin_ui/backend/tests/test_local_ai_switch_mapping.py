import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

pytest.importorskip("fastapi")

from api.local_ai import (  # noqa: E402
    SwitchModelRequest,
    _build_local_ai_env_and_yaml_updates,
    _build_local_ai_ws_switch_payload,
)


def test_ws_payload_faster_whisper_uses_stt_config_model() -> None:
    req = SwitchModelRequest(model_type="stt", backend="faster_whisper", model_path="base")
    assert _build_local_ai_ws_switch_payload(req) == {
        "type": "switch_model",
        "stt_backend": "faster_whisper",
        "stt_config": {"model": "base"},
    }


def test_env_and_yaml_updates_faster_whisper_persists_model_id() -> None:
    req = SwitchModelRequest(model_type="stt", backend="faster_whisper", model_path="base")
    env_updates, yaml_updates = _build_local_ai_env_and_yaml_updates(req)
    assert env_updates["LOCAL_STT_BACKEND"] == "faster_whisper"
    assert env_updates["FASTER_WHISPER_MODEL"] == "base"
    assert yaml_updates["stt_backend"] == "faster_whisper"
    assert yaml_updates["stt_model"] == "base"


def test_ws_payload_whisper_cpp_uses_stt_model_path() -> None:
    req = SwitchModelRequest(
        model_type="stt",
        backend="whisper_cpp",
        model_path="/app/models/stt/ggml-base.en.bin",
    )
    assert _build_local_ai_ws_switch_payload(req) == {
        "type": "switch_model",
        "stt_backend": "whisper_cpp",
        "stt_model_path": "/app/models/stt/ggml-base.en.bin",
    }


def test_ws_payload_tone_uses_tone_specific_fields() -> None:
    req = SwitchModelRequest(
        model_type="stt",
        backend="tone",
        model_path="/app/models/stt/t-one",
        tone_decoder_type="beam_search",
        tone_kenlm_path="/app/models/stt/t-one/kenlm.bin",
    )
    assert _build_local_ai_ws_switch_payload(req) == {
        "type": "switch_model",
        "stt_backend": "tone",
        "tone_model_path": "/app/models/stt/t-one",
        "tone_decoder_type": "beam_search",
        "tone_kenlm_path": "/app/models/stt/t-one/kenlm.bin",
    }


def test_env_and_yaml_updates_whisper_cpp_persists_model_path() -> None:
    req = SwitchModelRequest(
        model_type="stt",
        backend="whisper_cpp",
        model_path="/app/models/stt/ggml-base.en.bin",
    )
    env_updates, yaml_updates = _build_local_ai_env_and_yaml_updates(req)
    assert env_updates["LOCAL_STT_BACKEND"] == "whisper_cpp"
    assert env_updates["WHISPER_CPP_MODEL_PATH"] == "/app/models/stt/ggml-base.en.bin"
    assert yaml_updates["stt_backend"] == "whisper_cpp"
    assert yaml_updates["whisper_cpp_model_path"] == "/app/models/stt/ggml-base.en.bin"


def test_env_and_yaml_updates_tone_persists_model_and_decoder() -> None:
    req = SwitchModelRequest(
        model_type="stt",
        backend="tone",
        model_path="/app/models/stt/t-one",
        tone_decoder_type="greedy",
        tone_kenlm_path="/app/models/stt/t-one/kenlm.bin",
    )
    env_updates, yaml_updates = _build_local_ai_env_and_yaml_updates(req)
    assert env_updates["LOCAL_STT_BACKEND"] == "tone"
    assert env_updates["TONE_MODEL_PATH"] == "/app/models/stt/t-one"
    assert env_updates["TONE_DECODER_TYPE"] == "greedy"
    assert env_updates["TONE_KENLM_PATH"] == "/app/models/stt/t-one/kenlm.bin"
    assert yaml_updates["stt_backend"] == "tone"
    assert yaml_updates["tone_model_path"] == "/app/models/stt/t-one"
    assert yaml_updates["tone_decoder_type"] == "greedy"


def test_ws_payload_kroko_infers_embedded_when_model_path_points_to_models_kroko() -> None:
    req = SwitchModelRequest(
        model_type="stt",
        backend="kroko",
        model_path="/app/models/kroko/kroko-en-v1.0.onnx",
    )
    assert _build_local_ai_ws_switch_payload(req) == {
        "type": "switch_model",
        "stt_backend": "kroko",
        "kroko_embedded": True,
        "kroko_model_path": "/app/models/kroko/kroko-en-v1.0.onnx",
    }


def test_env_updates_kroko_sets_embedded_0_for_cloud_url_without_model_path() -> None:
    req = SwitchModelRequest(
        model_type="stt",
        backend="kroko",
        model_path=None,
        kroko_url="wss://app.kroko.ai/api/v1/transcripts/streaming",
    )
    env_updates, yaml_updates = _build_local_ai_env_and_yaml_updates(req)
    assert env_updates["LOCAL_STT_BACKEND"] == "kroko"
    assert env_updates["KROKO_URL"].startswith("wss://app.kroko.ai/")
    assert env_updates["KROKO_EMBEDDED"] == "0"
    assert yaml_updates["stt_backend"] == "kroko"


def test_ws_payload_melotts_uses_tts_config_voice() -> None:
    req = SwitchModelRequest(model_type="tts", backend="melotts", model_path="EN-US")
    assert _build_local_ai_ws_switch_payload(req) == {
        "type": "switch_model",
        "tts_backend": "melotts",
        "tts_config": {"voice": "EN-US"},
    }


def test_env_and_yaml_updates_melotts_persists_voice_id() -> None:
    req = SwitchModelRequest(model_type="tts", backend="melotts", model_path="EN-US")
    env_updates, yaml_updates = _build_local_ai_env_and_yaml_updates(req)
    assert env_updates["LOCAL_TTS_BACKEND"] == "melotts"
    assert env_updates["MELOTTS_VOICE"] == "EN-US"
    assert yaml_updates["tts_backend"] == "melotts"
    assert yaml_updates["tts_voice"] == "EN-US"


def test_ws_payload_silero_uses_silero_fields() -> None:
    req = SwitchModelRequest(
        model_type="tts",
        backend="silero",
        silero_speaker="xenia",
        silero_language="ru",
        silero_model_id="v3_1_ru",
    )
    payload = _build_local_ai_ws_switch_payload(req)
    assert payload["tts_backend"] == "silero"
    assert payload["silero_speaker"] == "xenia"
    assert payload["silero_language"] == "ru"
    assert payload["silero_model_id"] == "v3_1_ru"


def test_ws_payload_silero_with_model_path() -> None:
    req = SwitchModelRequest(
        model_type="tts",
        backend="silero",
        silero_speaker="eva_k",
        silero_language="de",
        model_path="/app/models/tts/silero",
    )
    payload = _build_local_ai_ws_switch_payload(req)
    assert payload["tts_backend"] == "silero"
    assert payload["silero_speaker"] == "eva_k"
    assert payload["silero_language"] == "de"
    assert payload["silero_model_path"] == "/app/models/tts/silero"


def test_env_and_yaml_updates_silero_persists_config() -> None:
    req = SwitchModelRequest(
        model_type="tts",
        backend="silero",
        silero_speaker="aidar",
        silero_language="ru",
        silero_model_id="v3_1_ru",
    )
    env_updates, yaml_updates = _build_local_ai_env_and_yaml_updates(req)
    assert env_updates["LOCAL_TTS_BACKEND"] == "silero"
    assert env_updates["SILERO_SPEAKER"] == "aidar"
    assert env_updates["SILERO_LANGUAGE"] == "ru"
    assert env_updates["SILERO_MODEL_ID"] == "v3_1_ru"
    assert yaml_updates["tts_backend"] == "silero"
    assert yaml_updates["silero_speaker"] == "aidar"
    assert yaml_updates["silero_language"] == "ru"


def test_env_and_yaml_updates_silero_with_model_path() -> None:
    req = SwitchModelRequest(
        model_type="tts",
        backend="silero",
        silero_speaker="xenia",
        model_path="/custom/silero/path",
    )
    env_updates, yaml_updates = _build_local_ai_env_and_yaml_updates(req)
    assert env_updates["LOCAL_TTS_BACKEND"] == "silero"
    assert env_updates["SILERO_SPEAKER"] == "xenia"
    assert env_updates["SILERO_MODEL_PATH"] == "/custom/silero/path"
    assert yaml_updates["silero_model_path"] == "/custom/silero/path"
