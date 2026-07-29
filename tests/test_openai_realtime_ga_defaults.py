"""
v6.5.4 regression tests — every code surface that defaults OpenAI Realtime
to api_version=ga + model=gpt-realtime.

Background: OpenAI sunset the Beta Realtime API on 2026-05-12 and removed
the `gpt-4o-realtime-preview-*` model snapshots on 2026-05-07. v6.5.3 was
a config-only hotfix (config/ai-agent.yaml). v6.5.4 brought the rest of
the codebase in line — Pydantic defaults, Admin UI form templates,
wizard backend, golden config, example config.

These tests pin those defaults so we don't regress to broken values.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest


# --- Pydantic defaults ----------------------------------------------------


def test_openai_provider_config_defaults_to_ga():
    """OpenAIProviderConfig (modular pipeline) must default to ga + gpt-realtime."""
    from src.config import OpenAIProviderConfig

    cfg = OpenAIProviderConfig()
    assert cfg.api_version == "ga", (
        "OpenAIProviderConfig.api_version default must be 'ga'. "
        "Beta was sunset 2026-05-12; setting beta produces a one-shot warning "
        "and OpenAI rejects the WebSocket with beta_api_shape_disabled."
    )
    assert cfg.realtime_model == "gpt-realtime", (
        "OpenAIProviderConfig.realtime_model default must be 'gpt-realtime'. "
        "Preview snapshots (gpt-4o-realtime-preview-*) were removed 2026-05-07."
    )


def test_openai_realtime_provider_config_defaults_to_ga():
    """OpenAIRealtimeProviderConfig (full-agent) must default to ga + gpt-realtime."""
    from src.config import OpenAIRealtimeProviderConfig

    cfg = OpenAIRealtimeProviderConfig(enabled=True)
    assert cfg.api_version == "ga"
    assert cfg.model == "gpt-realtime"


# --- Beta-deprecation warning (one-shot guard) ----------------------------


def test_warn_if_beta_deprecated_fires_exactly_once(monkeypatch):
    """The beta-deprecation warning must fire exactly once per provider
    lifetime — even though it's called from both start_session() and the
    reconnect path. Critical for log-readability: operators must see the
    one explanatory line, not a flood per reconnect attempt.

    The provider uses structlog, which doesn't reliably flow through
    pytest's caplog fixture. Patch logger.warning directly to count calls."""
    from src.config import OpenAIRealtimeProviderConfig
    from src.providers import openai_realtime as openai_realtime_module
    from src.providers.openai_realtime import OpenAIRealtimeProvider

    cfg = OpenAIRealtimeProviderConfig(
        enabled=True,
        api_key="test-key",
        api_version="beta",      # explicit override — triggers the warning
        model="gpt-4o-realtime-preview",  # legacy literal, irrelevant to the warning
    )

    async def _noop_on_event(*args, **kwargs):
        return None

    provider = OpenAIRealtimeProvider(cfg, _noop_on_event)
    assert provider._beta_warned is False

    # Count every logger.warning call. We don't care about the exact message
    # shape — that's verified by the existence of the keyword in the helper
    # source. We DO care that exactly one call occurs across three invocations.
    warning_calls: list = []
    original_warning = openai_realtime_module.logger.warning

    def _capture(*args, **kwargs):
        warning_calls.append((args, kwargs))
        return original_warning(*args, **kwargs)

    monkeypatch.setattr(openai_realtime_module.logger, "warning", _capture)

    provider._warn_if_beta_deprecated("call-1")  # first time — fires
    provider._warn_if_beta_deprecated("call-2")  # simulated reconnect — silent
    provider._warn_if_beta_deprecated("call-3")  # another reconnect — silent

    # Filter to calls whose message text mentions beta_api_shape_disabled, in
    # case other warnings from constructor / setup were also captured.
    beta_warnings = [c for c in warning_calls if c[0] and "beta_api_shape_disabled" in str(c[0][0])]
    assert len(beta_warnings) == 1, (
        f"Expected exactly one beta-deprecation warning, got {len(beta_warnings)}. "
        "The one-shot guard via self._beta_warned must serialise calls from "
        "both start_session() and the reconnect path."
    )
    assert provider._beta_warned is True


def test_warn_if_beta_deprecated_silent_when_ga(monkeypatch):
    """When api_version=ga (the default), the warning must NOT fire even
    on the first call. This guards against accidentally re-introducing the
    warning into the GA hot path."""
    from src.config import OpenAIRealtimeProviderConfig
    from src.providers import openai_realtime as openai_realtime_module
    from src.providers.openai_realtime import OpenAIRealtimeProvider

    cfg = OpenAIRealtimeProviderConfig(enabled=True, api_key="test-key")
    assert cfg.api_version == "ga"

    async def _noop(*args, **kwargs):
        return None

    provider = OpenAIRealtimeProvider(cfg, _noop)

    warning_calls: list = []
    monkeypatch.setattr(openai_realtime_module.logger, "warning", lambda *a, **kw: warning_calls.append((a, kw)))

    provider._warn_if_beta_deprecated("call-1")

    assert provider._beta_warned is False
    beta_warnings = [c for c in warning_calls if c[0] and "beta_api_shape_disabled" in str(c[0][0])]
    assert not beta_warnings


# --- Admin UI provider template grep (TSX defaults) -----------------------


_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_providers_page_template_defaults_to_ga():
    """Admin UI 'Add Provider' template for openai_realtime must seed
    api_version: 'ga' + model: 'gpt-realtime'. Every operator who clicks
    'Add OpenAI Realtime' in the UI creates a config from this template;
    if it ships beta + a removed preview model, fresh installs are broken
    on the very first call.

    File: admin_ui/frontend/src/pages/ProvidersPage.tsx
    """
    page = (_REPO_ROOT / "admin_ui" / "frontend" / "src" / "pages" / "ProvidersPage.tsx").read_text()
    # Find the openai_realtime template block (object literal seeded into the
    # template registry around line 234-260).
    template_marker = "openai_realtime: {"
    idx = page.find(template_marker)
    assert idx > 0, "openai_realtime template block not found in ProvidersPage.tsx"
    # Inspect the ~30 lines of the template body.
    body = page[idx : idx + 1200]

    assert "api_version: 'ga'" in body, (
        "ProvidersPage.tsx openai_realtime template must seed api_version: 'ga'. "
        "Found body:\n" + body[:600]
    )
    assert "model: 'gpt-realtime'" in body, (
        "ProvidersPage.tsx openai_realtime template must seed model: 'gpt-realtime'."
    )
    # And it must NOT carry the legacy preview model as a FIELD VALUE. We scope
    # the check to quoted-string field assignments to avoid false-positive
    # matches on explanatory comments inside the template body that legitimately
    # mention "removed gpt-4o-realtime-preview-* on 2026-05-07" as historical context.
    import re
    field_value_re = re.compile(r":\s*['\"]gpt-4o-realtime-preview")
    assert not field_value_re.search(body), (
        "ProvidersPage.tsx template assigns a removed preview model as a field value. "
        "Field-value-shaped matches (e.g. `model: 'gpt-4o-realtime-preview-*'`) "
        "are forbidden; historical references inside `//` comments are OK."
    )


def test_openai_realtime_provider_form_catalog_is_ga_only():
    """The OpenAIRealtimeProviderForm.tsx OPENAI_REALTIME_MODELS catalog
    must contain only current GA model identifiers — no preview snapshots."""
    form = (_REPO_ROOT / "admin_ui" / "frontend" / "src" / "components" / "config"
            / "providers" / "OpenAIRealtimeProviderForm.tsx").read_text()

    # The catalog is declared at module scope.
    assert "OPENAI_REALTIME_MODELS = [" in form, (
        "OpenAIRealtimeProviderForm.tsx is missing the OPENAI_REALTIME_MODELS catalog constant. "
        "Refactor to lift dropdown options to a module-scope constant (Grok form pattern)."
    )
    # Slice the constant body.
    start = form.index("OPENAI_REALTIME_MODELS = [")
    end = form.index("];", start)
    catalog = form[start:end]

    for ga_alias in ("gpt-realtime", "gpt-realtime-1.5", "gpt-realtime-2", "gpt-realtime-mini"):
        assert ga_alias in catalog, f"Catalog missing current GA alias: {ga_alias}"

    # No removed preview models in the GA catalog body.
    assert "gpt-4o-realtime-preview" not in catalog, (
        "OPENAI_REALTIME_MODELS catalog must not contain removed preview model snapshots. "
        "If an operator pins a legacy value via YAML, it renders via the "
        "'Custom (legacy)' optgroup outside this catalog."
    )
