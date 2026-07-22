from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.config import AppConfig
from src.engine import _resolve_pipeline_streaming_overlap


def _app_config_with_overlap(value):
    return AppConfig(
        default_provider="local",
        providers={"local": {"enabled": True}},
        pipelines={
            "canary": {
                "stt": "local_stt",
                "llm": "local_llm",
                "tts": "local_tts",
                "options": {"tts": {"streaming_overlap": value}},
            }
        },
        asterisk={"host": "127.0.0.1", "username": "u", "password": "p"},
        llm={"initial_greeting": "", "prompt": "prompt", "model": "model"},
    )


@pytest.mark.parametrize("value", [True, False])
def test_app_config_accepts_boolean_pipeline_streaming_overlap(value):
    config = _app_config_with_overlap(value)
    assert config.pipelines["canary"].options["tts"]["streaming_overlap"] is value


@pytest.mark.parametrize("value", ["false", 0, 1, None])
def test_app_config_rejects_non_boolean_pipeline_streaming_overlap(value):
    with pytest.raises(
        ValidationError,
        match=r"pipelines\.canary\.options\.tts\.streaming_overlap",
    ):
        _app_config_with_overlap(value)


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("segment_energy_threshold", True),
        ("segment_energy_threshold", 32768),
        ("segment_silence_ms", "1200"),
        ("segment_silence_ms", 99),
    ],
)
def test_app_config_rejects_invalid_local_stt_segmenter_policy(option, value):
    config = _app_config_with_overlap(False)
    data = config.model_dump()
    data["pipelines"]["canary"]["options"]["stt"] = {option: value}
    with pytest.raises(
        ValidationError,
        match=rf"pipelines\.canary\.options\.stt\.{option}",
    ):
        AppConfig(**data)


@pytest.mark.parametrize(
    ("global_value", "tts_options", "expected"),
    [
        (True, {}, (True, "global")),
        (False, {}, (False, "global")),
        (True, {"streaming_overlap": False}, (False, "pipeline")),
        (False, {"streaming_overlap": True}, (True, "pipeline")),
        (True, {"streaming_overlap": "false"}, (True, "global")),
    ],
)
def test_resolve_pipeline_streaming_overlap(
    global_value,
    tts_options,
    expected,
):
    streaming_config = SimpleNamespace(pipeline_streaming_overlap=global_value)
    assert _resolve_pipeline_streaming_overlap(streaming_config, tts_options) == expected
