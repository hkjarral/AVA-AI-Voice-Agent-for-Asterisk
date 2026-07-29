import errno
import os
import stat

import pytest

from src.config.normalization import ConfigValidationError, normalize_pipelines, validate_providers
from src.config.provider_instances import (
    ProviderInstanceError,
    read_secret_file,
    read_secret_file_for_provider,
    safe_secret_path,
    write_secret_file_bytes,
    provider_kind,
)


def test_provider_kind_uses_type_for_duplicate_full_agent_instances():
    cfg = {"type": "google_live", "enabled": True}

    assert provider_kind("acme_google_live", cfg) == "google_live"


def test_provider_kind_accepts_legacy_full_type_for_canonical_key():
    cfg = {"type": "full", "enabled": True}

    assert provider_kind("google_live", cfg) == "google_live"


def test_legacy_full_type_on_noncanonical_key_requires_specific_kind():
    config_data = {
        "default_provider": "acme_google_live",
        "providers": {
            "acme_google_live": {"type": "full", "enabled": True},
        },
    }

    with pytest.raises(ConfigValidationError) as exc_info:
        validate_providers(config_data)

    assert "unsupported full-agent type 'full'" in str(exc_info.value)


def test_full_agent_default_with_explicit_type_skips_implicit_pipeline():
    config_data = {
        "default_provider": "acme_google_live",
        "providers": {
            "acme_google_live": {"type": "google_live", "enabled": True},
        },
    }

    normalize_pipelines(config_data)

    assert config_data["pipelines"] == {}
    assert config_data["active_pipeline"] is None


def test_full_agent_default_with_legacy_full_type_skips_implicit_pipeline():
    config_data = {
        "default_provider": "google_live",
        "providers": {
            "google_live": {"type": "full", "enabled": True},
        },
    }

    normalize_pipelines(config_data)

    assert config_data["pipelines"] == {}
    assert config_data["active_pipeline"] is None


def test_context_provider_must_reference_exact_instance_key():
    config_data = {
        "default_provider": "acme_google_live",
        "providers": {
            "acme_google_live": {"type": "google_live", "enabled": True},
        },
        "contexts": {
            "sales": {"provider": "google_live"},
        },
    }

    with pytest.raises(ConfigValidationError):
        validate_providers(config_data)


@pytest.mark.parametrize("alias", ["openai", "google", "deepgram_agent"])
def test_legacy_short_provider_aliases_fail_validation(alias):
    config_data = {
        "default_provider": "acme_google_live",
        "providers": {
            "acme_google_live": {"type": "google_live", "enabled": True},
            "globex_openai_realtime": {"type": "openai_realtime", "enabled": True},
            "deepgram": {"enabled": True},
        },
        "contexts": {
            "legacy": {"provider": alias},
        },
    }

    with pytest.raises(ConfigValidationError) as exc_info:
        validate_providers(config_data)

    assert alias in str(exc_info.value)


def test_provider_key_cannot_collide_with_pipeline_name():
    config_data = {
        "default_provider": "acme_google_live",
        "providers": {
            "acme_google_live": {"type": "google_live", "enabled": True},
        },
        "pipelines": {
            "acme_google_live": {
                "stt": "local_stt",
                "llm": "openai_llm",
                "tts": "local_tts",
            }
        },
    }

    with pytest.raises(ConfigValidationError):
        validate_providers(config_data)


def test_read_secret_file_rejects_leaf_symlink(tmp_path):
    target = tmp_path / "target-secret"
    target.write_text("secret", encoding="utf-8")
    link = tmp_path / "linked-secret"
    link.symlink_to(target)

    with pytest.raises(OSError) as exc_info:
        read_secret_file(str(link))

    assert exc_info.value.errno in {errno.ELOOP, errno.EMLINK}


def test_provider_secret_reader_rejects_leaf_symlink(tmp_path):
    provider_dir = tmp_path / "acme"
    provider_dir.mkdir()
    target = tmp_path / "target-secret"
    target.write_text("secret", encoding="utf-8")
    (provider_dir / "api-key").symlink_to(target)

    with pytest.raises(OSError) as exc_info:
        read_secret_file_for_provider("acme", "api-key", root=str(tmp_path))

    assert exc_info.value.errno in {errno.ELOOP, errno.EMLINK}


def test_safe_secret_path_rejects_provider_directory_symlink_escape(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "acme").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ProviderInstanceError):
        safe_secret_path("acme", "api-key", root=str(tmp_path))


def test_write_secret_file_bytes_creates_owner_only_file_under_permissive_umask(
    tmp_path,
):
    previous_umask = os.umask(0)
    try:
        write_secret_file_bytes(str(tmp_path / "api-key"), b"secret")
    finally:
        os.umask(previous_umask)

    secret_path = tmp_path / "api-key"
    assert secret_path.read_bytes() == b"secret"
    assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600
