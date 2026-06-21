"""Regression guard: EmailValidator must be importable + do format validation
without dnspython at module load.

The admin_ui container does NOT ship dnspython; the engine does. H3 (MED-E1)
imports EmailValidator into the admin backend for format validation. A
module-level ``import dns.resolver`` therefore crash-loops admin_ui with
ModuleNotFoundError and blocks the agents.db migration. dnspython must only be
needed inside ``validate_domain`` (the MX lookup), which runs in the engine."""
import importlib

import src.utils.email_validator as ev


def test_module_has_no_top_level_dns_binding():
    importlib.reload(ev)
    # A module-level `import dns.resolver` would expose `dns` here and require
    # dnspython at import time (which admin_ui lacks).
    assert not hasattr(ev, "dns"), "dns must be a lazy import inside validate_domain()"


def test_validate_email_format_check_works_without_dns():
    assert ev.EmailValidator.validate_email("ops@acme.test") is True
    assert ev.EmailValidator.validate_email("not-an-email") is False
    assert ev.EmailValidator.validate_email("") is False
