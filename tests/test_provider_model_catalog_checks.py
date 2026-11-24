"""Catalog checks catch drift between real consumers without calling providers."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from urllib.error import URLError

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_provider_models_for_tests", ROOT / "scripts/check_provider_models.py")
checks = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(checks)


@pytest.fixture
def catalog():
    return {
        "schema_version": 1,
        "verified_at": "2026-11-23",
        "providers": {
            "example": {
                "sources": ["https://example.org/official-models"],
                "defaults": {"tts_model": "speech-current", "voice": "voice-a"},
                "models": {"tts": [{"id": "speech-current", "label": "Speech current"}]},
                "voices": {"tts": [{"id": "voice-a", "label": "Voice A"}, {"id": "voice-b", "label": "Voice B"}]},
                "voice_models": {"speech-current": ["voice-a"]},
            }
        },
    }


def test_catalog_compatible_default_is_accepted(catalog):
    assert checks.check_catalog(catalog) == []


@pytest.mark.parametrize("change, expected", [
    (lambda c: c.update(verified_at="yesterday"), "verified_at"),
    (lambda c: c["providers"]["example"].update(sources=["http://example.org"]), "HTTPS"),
    (lambda c: c["providers"]["example"]["defaults"].update(tts_model="removed-model"), "absent"),
    (lambda c: c["providers"]["example"]["defaults"].update(voice="voice-b"), "incompatible"),
    (lambda c: c["providers"]["example"]["voice_models"].update({"speech-current": ["unknown"]}), "unknown voices"),
    (lambda c: c["providers"]["example"]["models"]["tts"][0].update(status="deprecated"), "not active"),
    (lambda c: c["providers"]["example"]["models"]["tts"][0].update(shutdown_date="next year"), "shutdown_date"),
])
def test_catalog_rejects_semantic_drift(catalog, change, expected):
    change(catalog)
    assert any(expected in failure for failure in checks.check_catalog(catalog))


def test_catalog_rejects_duplicate_models(catalog):
    models = catalog["providers"]["example"]["models"]["tts"]
    models.append(deepcopy(models[0]))
    assert any("duplicate id" in failure for failure in checks.check_catalog(catalog))


def test_deprecation_must_explain_its_retirement_schedule(catalog):
    old_model = {"id": "old-model", "label": "Old model", "status": "deprecated"}
    catalog["providers"]["example"]["models"]["tts"].append(old_model)
    assert any("retirement date" in failure for failure in checks.check_catalog(catalog))
    old_model["retirement_date_unknown"] = True
    assert checks.check_catalog(catalog) == []


def test_runtime_lookup_and_literal_drift(tmp_path, monkeypatch, catalog):
    monkeypatch.setattr(checks, "RUNTIME_BINDINGS", {"ExampleConfig": "example"})
    config = tmp_path / "src/config.py"
    config.parent.mkdir()
    config.write_text('class ExampleConfig:\n    tts_model: str = Field(default=provider_default("example", "tts_model"))\n    voice: str = Field(default="voice-a")\n')
    assert checks.check_runtime(tmp_path, catalog) == []
    config.write_text(config.read_text().replace('default="voice-a"', 'default="voice-b"'))
    assert checks.check_runtime(tmp_path, catalog) == ["ExampleConfig.voice: runtime default 'voice-b' != catalog 'voice-a'"]


def test_missing_groq_tts_voice_field_fails_even_though_stt_owns_other_defaults(tmp_path):
    data = json.loads((ROOT / checks.CATALOG_PATH).read_text())
    (tmp_path / "src").mkdir()
    source = (ROOT / "src/config.py").read_text()
    start = source.index("class GroqTTSProviderConfig")
    end = source.index("class ElevenLabsProviderConfig", start)
    block = source[start:end]
    block = "\n".join(line for line in block.splitlines() if not line.strip().startswith("voice: str")) + "\n\n"
    (tmp_path / "src/config.py").write_text(source[:start] + block + source[end:])
    assert "GroqTTSProviderConfig: catalog default 'voice' has no runtime field" in checks.check_runtime(tmp_path, data)


def test_stt_default_cannot_use_model_from_tts_role(catalog):
    provider = catalog["providers"]["example"]
    provider["models"]["stt"] = [{"id": "recognizer", "label": "STT"}]
    provider["defaults"]["stt_model"] = "speech-current"
    assert any("role catalog" in failure for failure in checks.check_catalog(catalog))


def test_default_static_model_requires_compatibility_mapping(catalog):
    catalog["providers"]["example"]["voice_models"].clear()
    assert any("requires a voice_models" in failure for failure in checks.check_catalog(catalog))


def test_sample_drift_and_inherited_defaults(tmp_path, monkeypatch, catalog):
    monkeypatch.setattr(checks, "SAMPLE_BINDINGS", {"example_tts": ("example", {"tts_model", "voice"})})
    path = tmp_path / "config/ai-agent.yaml"
    path.parent.mkdir()
    path.write_text("providers:\n  example_tts:\n    tts_model: speech-current\n")
    assert checks.check_sample(tmp_path, catalog) == []
    path.write_text("providers:\n  example_tts:\n    tts_model: removed-model\n")
    assert any("example_tts.tts_model" in failure for failure in checks.check_sample(tmp_path, catalog))


def test_generated_files_cannot_silently_drift(tmp_path):
    generator = checks._load_module(ROOT / "scripts/generate_provider_catalog.py", "test_catalog_generator")
    source = tmp_path / "src/utils/provider_catalog.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"providers": {"fishaudio": {"models": {"tts": [{"id": "fish-model"}]}}}}))
    local_catalog = tmp_path / "admin_ui/backend/api/models_catalog.py"
    local_catalog.parent.mkdir(parents=True)
    local_catalog.write_text((ROOT / "admin_ui/backend/api/models_catalog.py").read_text())
    generator_path = tmp_path / "scripts/generate_provider_catalog.py"
    generator_path.parent.mkdir()
    generator_path.write_text((ROOT / "scripts/generate_provider_catalog.py").read_text())
    assert checks.check_generated(tmp_path)
    for path, content in generator.generated_files(tmp_path).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    assert checks.check_generated(tmp_path) == []
    generated = tmp_path / "admin_ui/frontend/src/config/fishAudioModels.json"
    generated.write_text('["wrong-model"]\n')
    assert any("fishAudioModels.json" in failure for failure in checks.check_generated(tmp_path))


def test_complete_kokoro_catalog_has_correct_language_metadata():
    assert checks.check_local_catalog(ROOT) == []
    local = checks._load_module(ROOT / "admin_ui/backend/api/models_catalog.py", "test_complete_kokoro")
    kokoro = local.KOKORO_TTS_MODELS[0]
    assert len(kokoro["voice_files"]) == 54
    assert kokoro["voice_lang_codes"]["jf_alpha"] == "j"
    assert kokoro["voice_languages"]["pf_dora"] == "pt-BR"
    assert kokoro["voice_lang_codes"]["af_heart"] == "a"


def test_missing_kokoro_voice_is_detected(tmp_path):
    path = tmp_path / "admin_ui/backend/api/models_catalog.py"
    path.parent.mkdir(parents=True)
    source = (ROOT / "admin_ui/backend/api/models_catalog.py").read_text()
    source += '\ndel KOKORO_TTS_MODELS[0]["voice_files"]["jf_alpha"]\n'
    path.write_text(source)
    constants = tmp_path / "local_ai_server/constants.py"
    constants.parent.mkdir()
    constants.write_text((ROOT / "local_ai_server/constants.py").read_text())
    failures = checks.check_local_catalog(tmp_path)
    assert any("all 54" in failure for failure in failures)
    assert any("every downloadable voice" in failure for failure in failures)


def test_upstream_errors_are_explicit_and_never_used_by_offline_checks(monkeypatch):
    requested = []
    def unavailable(url, timeout):
        requested.append(url)
        raise URLError("offline")
    monkeypatch.setattr(checks, "urlopen", unavailable)
    assert checks.check_local_catalog(ROOT) == []
    assert requested == []
    failures = checks.check_upstream(ROOT)
    assert len(requested) == 3
    assert len(failures) == 3
    assert all("public upstream comparison failed" in failure for failure in failures)
