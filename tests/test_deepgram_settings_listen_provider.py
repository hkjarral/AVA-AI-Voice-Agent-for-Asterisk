"""Tests for the Deepgram Voice Agent listen-provider Settings block.

Pins the v6.5.0 fixes for the audit findings:
  - #1 (HIGH): YAML/config `model` field must actually flow into the
    Settings JSON sent to Deepgram (was hardcoded to "nova-3" pre-v6.5.0).
  - #2 (HIGH): When `model` starts with "flux", the Settings JSON must
    include `version: "v2"` plus Flux-specific tuning fields per Deepgram's
    Configure Voice Agent docs:
    https://developers.deepgram.com/docs/configure-voice-agent

Tested via `build_listen_provider_block` — a module-level pure function
extracted from `_configure_agent` specifically so the Settings-builder
behavior can be unit-tested without spinning up a full DeepgramProvider.
"""

import pytest

from src.providers.deepgram import build_listen_provider_block


# --- Nova family: model name flows through unchanged, no Flux fields added ---

@pytest.mark.parametrize("model", [
    "nova-3",
    "nova-3-medical",
    "nova-2",
    "nova-2-phonecall",
    "nova-2-conversationalai",
    "nova",
    "nova-phonecall",
    "enhanced",
    "base",
    "whisper-cloud",
])
def test_nova_models_pass_through_with_no_flux_fields(model):
    block = build_listen_provider_block(model=model)
    assert block == {"type": "deepgram", "model": model}, (
        f"Nova/non-Flux model '{model}' must not get Flux-specific fields. "
        f"Got: {block}"
    )


def test_nova_ignores_flux_tuning_arguments():
    """If a caller passes Flux-specific tuning to a Nova model, those
    fields must NOT leak into the Settings JSON (Deepgram rejects unknown
    fields on Nova listen-provider blocks)."""
    block = build_listen_provider_block(
        model="nova-3",
        eot_threshold=0.85,
        eager_eot_threshold=0.5,
        keyterms=["hello", "goodbye"],
    )
    assert block == {"type": "deepgram", "model": "nova-3"}


# --- Flux family: version=v2 required + tuning fields wired through ---

@pytest.mark.parametrize("model", ["flux-general-en", "flux-general-multi"])
def test_flux_models_get_version_v2_required_field(model):
    block = build_listen_provider_block(model=model)
    assert block["type"] == "deepgram"
    assert block["model"] == model
    assert block["version"] == "v2", (
        "Flux models require `version: \"v2\"` in listen.provider per "
        "Deepgram's Configure Voice Agent docs."
    )


def test_flux_default_eot_threshold_is_07():
    """Default eot_threshold per AAVA's Pydantic schema (matches Deepgram's
    documented recommended default)."""
    block = build_listen_provider_block(model="flux-general-en")
    assert block.get("eot_threshold") == 0.7


def test_flux_eot_threshold_is_configurable_in_valid_range():
    """Deepgram's documented valid range is 0.5–0.9. The function does not
    enforce the range itself (config validation is a separate concern); it
    just passes the value through. This test pins that pass-through
    behavior at both endpoints of the documented range."""
    block_low = build_listen_provider_block(model="flux-general-en", eot_threshold=0.5)
    block_high = build_listen_provider_block(model="flux-general-en", eot_threshold=0.9)
    assert block_low["eot_threshold"] == 0.5
    assert block_high["eot_threshold"] == 0.9


def test_flux_eager_eot_threshold_disabled_by_default():
    """`eager_eot_threshold` defaults to None (disabled). When None, the
    field MUST NOT appear in the Settings JSON."""
    block = build_listen_provider_block(model="flux-general-en")
    assert "eager_eot_threshold" not in block, (
        "When eager_eot_threshold is None it must not appear in the "
        "Settings JSON (Deepgram interprets presence of the field as enabled)."
    )


def test_flux_eager_eot_threshold_propagates_when_set():
    block = build_listen_provider_block(
        model="flux-general-en",
        eager_eot_threshold=0.5,
    )
    assert block.get("eager_eot_threshold") == 0.5


def test_flux_eot_threshold_none_omits_field():
    """If a caller explicitly sets eot_threshold=None, the field is omitted
    from the Settings JSON (operator opt-out from the AAVA default)."""
    block = build_listen_provider_block(model="flux-general-en", eot_threshold=None)
    assert "eot_threshold" not in block


def test_flux_keyterms_propagate_when_set():
    block = build_listen_provider_block(
        model="flux-general-en",
        keyterms=["AAVA", "Asterisk", "voicemail"],
    )
    assert block["keyterms"] == ["AAVA", "Asterisk", "voicemail"]


def test_flux_keyterms_none_omits_field():
    block = build_listen_provider_block(model="flux-general-en", keyterms=None)
    assert "keyterms" not in block


def test_flux_keyterms_empty_list_omits_field():
    block = build_listen_provider_block(model="flux-general-en", keyterms=[])
    assert "keyterms" not in block


def test_flux_keyterms_blank_strings_filtered_out():
    block = build_listen_provider_block(
        model="flux-general-en",
        keyterms=["valid_term", "", "  ", "another_term"],
    )
    assert block["keyterms"] == ["valid_term", "another_term"]


def test_flux_keyterms_all_blank_omits_field():
    """If all entries in keyterms are blank/whitespace, the field should be
    omitted entirely (don't send `keyterms: []` to Deepgram)."""
    block = build_listen_provider_block(
        model="flux-general-en",
        keyterms=["", "  ", "\t"],
    )
    assert "keyterms" not in block


def test_flux_full_payload_matches_deepgram_doc_example():
    """The Settings example in Deepgram's Configure Voice Agent docs:
        {
          "type": "deepgram",
          "model": "flux-general-en",
          "version": "v2",
          "keyterms": ["hello", "goodbye"],
          "eot_threshold": 0.8,
          "eager_eot_threshold": 0.5
        }
    Pin this exact shape so a future refactor cannot silently drop a field.
    """
    block = build_listen_provider_block(
        model="flux-general-en",
        eot_threshold=0.8,
        eager_eot_threshold=0.5,
        keyterms=["hello", "goodbye"],
    )
    assert block == {
        "type": "deepgram",
        "model": "flux-general-en",
        "version": "v2",
        "eot_threshold": 0.8,
        "eager_eot_threshold": 0.5,
        "keyterms": ["hello", "goodbye"],
    }


# --- Edge cases / defensive parsing ---

def test_uppercase_flux_model_still_triggers_v2_path():
    """Model names should be matched case-insensitively to avoid surprises
    if a config file capitalizes."""
    block = build_listen_provider_block(model="FLUX-GENERAL-EN")
    assert block.get("version") == "v2"


def test_non_string_model_does_not_trigger_flux_path():
    """If something has gone wrong and `model` is not a string, the function
    should not crash; it should return a minimally-shaped block. (Pydantic
    catches this earlier in practice; this is belt-and-suspenders.)"""
    block = build_listen_provider_block(model=None)  # type: ignore[arg-type]
    assert block == {"type": "deepgram", "model": None}
    assert "version" not in block
