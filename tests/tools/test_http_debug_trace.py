from src.tools.http.debug_trace import build_var_snapshot, redact_headers


def test_redact_headers_hides_credentials_but_keeps_operational_headers():
    redacted = redact_headers(
        {
            "Content-Type": "application/json",
            "Authorization": "Bearer top-secret",
            "X-API-Key": "api-secret",
            "X-AAVA-Secret": "shared-secret",
            "Cookie": "session=secret",
            "Idempotency-Key": "safe-request-id",
        }
    )

    assert redacted["Content-Type"] == "application/json"
    assert redacted["Idempotency-Key"] == "safe-request-id"
    assert redacted["Authorization"] == "<redacted>"
    assert redacted["X-API-Key"] == "<redacted>"
    assert redacted["X-AAVA-Secret"] == "<redacted>"
    assert redacted["Cookie"] == "<redacted>"


def test_build_var_snapshot_reports_env_presence_without_value():
    snapshot = build_var_snapshot(
        used_brace_vars=["call_id"],
        used_env_vars=["API_TOKEN", "MISSING_TOKEN"],
        values={"call_id": "call-1"},
        env={"API_TOKEN": "must-not-leak"},
    )

    assert snapshot == {
        "vars": {"call_id": "call-1"},
        "env": {"API_TOKEN": "<set>", "MISSING_TOKEN": None},
    }
