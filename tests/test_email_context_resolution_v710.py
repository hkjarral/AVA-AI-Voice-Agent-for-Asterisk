"""H3 / MED-E2: regression coverage for resolve_context_value().

The *_by_context mechanism in email_dispatcher routes per-context email overrides.
It is load-bearing routing logic; these tests pin its three behaviors:
  1. per-context override beats the global value
  2. falls back to the global value when no context matches
  3. falls back to the supplied default when neither is present
"""
from src.tools.business.email_dispatcher import resolve_context_value


def test_per_context_override_beats_global():
    cfg = {
        "admin_email": "global@example.com",
        "admin_email_by_context": {"sales": "sales@example.com"},
    }
    v = resolve_context_value(tool_config=cfg, key="admin_email",
                              context_name="sales", default="default@example.com")
    assert v == "sales@example.com"


def test_falls_back_to_global_when_no_context_match():
    cfg = {
        "admin_email": "global@example.com",
        "admin_email_by_context": {"sales": "sales@example.com"},
    }
    # context present but not in the map -> global
    v = resolve_context_value(tool_config=cfg, key="admin_email",
                              context_name="support", default="default@example.com")
    assert v == "global@example.com"


def test_falls_back_to_global_when_context_none():
    cfg = {
        "admin_email": "global@example.com",
        "admin_email_by_context": {"sales": "sales@example.com"},
    }
    v = resolve_context_value(tool_config=cfg, key="admin_email",
                              context_name=None, default="default@example.com")
    assert v == "global@example.com"


def test_falls_back_to_default_when_neither_present():
    cfg = {}  # no global, no map
    v = resolve_context_value(tool_config=cfg, key="admin_email",
                              context_name="sales", default="default@example.com")
    assert v == "default@example.com"


def test_no_map_uses_global():
    cfg = {"admin_email": "global@example.com"}
    v = resolve_context_value(tool_config=cfg, key="admin_email",
                              context_name="sales", default="default@example.com")
    assert v == "global@example.com"
