"""Tests for the engine /config/state restart-required reconciliation endpoint.

The engine reports whether the on-disk config differs from the running (loaded)
config so the Admin UI can stop relying on stale client-side localStorage to drive
the "restart required" banner.
"""
from types import SimpleNamespace

import pytest

import src.engine as engine_mod
from src.config import load_config
from src.engine import Engine

# A valid, self-contained on-disk config so the unchanged-config invariant
# exercises a real load_config() round-trip. Credentials come from env vars
# set by the autouse fixture below.
_CONFIG_PATH = "config/ai-agent.example.yaml"


@pytest.fixture(autouse=True)
def _required_env(monkeypatch):
    """ai-agent.example.yaml resolves Asterisk creds + API keys from env."""
    monkeypatch.setenv("ASTERISK_ARI_USERNAME", "test_user")
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "test_pass")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    monkeypatch.setenv("TELNYX_API_KEY", "tk-test-key")


def _make_engine_from_disk():
    """Build a minimal Engine whose running config is a real on-disk load.

    Mirrors the test_engine_live_status_publish.py construction pattern: bypass
    __init__ via __new__ and set only the attributes the methods under test use.
    The running hash is snapshotted from the SAME load_config() path the engine
    uses at startup, so an untouched disk must hash-match the running config.
    """
    config = load_config(_CONFIG_PATH)
    engine = Engine.__new__(Engine)
    engine.config = config
    engine._config_hash = engine._compute_config_hash()
    return engine


def test_unchanged_config_reports_no_restart(monkeypatch):
    """CRITICAL INVARIANT: with nothing changed on disk since the engine loaded,
    running and disk hashes must match and restart_required must be False."""
    engine = _make_engine_from_disk()
    # Engine re-reads disk via load_config(); point it at the same untouched file.
    monkeypatch.setattr(
        engine_mod, "load_config", lambda *a, **k: load_config(_CONFIG_PATH)
    )

    state = engine._compute_config_state()

    assert state["disk_config_valid"] is True
    assert state["running_config_hash"] == state["disk_config_hash"], (
        f"running={state['running_config_hash']} disk={state['disk_config_hash']}"
    )
    assert state["restart_required"] is False


def test_changed_disk_config_requires_restart(monkeypatch):
    """When the on-disk config differs from what the engine loaded, the engine
    reports restart_required and the hashes differ."""
    engine = _make_engine_from_disk()

    # Simulate an out-of-band edit to disk: a fresh load now yields a different config.
    mutated = load_config(_CONFIG_PATH)
    mutated.default_provider = mutated.default_provider + "_changed"
    monkeypatch.setattr(engine_mod, "load_config", lambda *a, **k: mutated)

    state = engine._compute_config_state()

    assert state["disk_config_valid"] is True
    assert state["running_config_hash"] != state["disk_config_hash"]
    assert state["restart_required"] is True


def test_invalid_disk_config_is_safe(monkeypatch):
    """If the on-disk config can't be parsed/validated, the engine must not claim
    a restart is needed and must not raise."""
    engine = _make_engine_from_disk()

    def _raise(*a, **k):
        raise ValueError("invalid YAML")

    monkeypatch.setattr(engine_mod, "load_config", _raise)

    state = engine._compute_config_state()

    assert state["disk_config_valid"] is False
    assert state["disk_config_hash"] is None
    assert state["restart_required"] is False


def test_compute_config_hash_accepts_arg():
    """_compute_config_hash(other) hashes the passed config; no-arg hashes
    self.config (back-compat for existing callers)."""
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(model_dump=lambda: {"a": 1})

    other = SimpleNamespace(model_dump=lambda: {"a": 2})

    self_hash = engine._compute_config_hash()
    other_hash = engine._compute_config_hash(other)

    assert self_hash == engine._compute_config_hash(engine.config)
    assert other_hash != self_hash
    assert other_hash == engine._compute_config_hash(other)
