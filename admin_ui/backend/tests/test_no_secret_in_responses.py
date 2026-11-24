"""Release gate for authenticated GET responses, including hidden routes and downloads."""
import asyncio
from collections import Counter
from contextlib import ExitStack, nullcontext
from datetime import datetime, timezone
import io
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import traceback
from types import SimpleNamespace
from urllib.parse import unquote, urlencode, urlsplit
from unittest.mock import patch
import zipfile


BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parents[1]
ASSET = "/assets/release-test1234.js"
PUBLIC = "release-scan-public-control"
PREFIX = b"CANARY-"

# Each route still has its response scanned. These are exact outcomes for the
# unconfigured/offline installation. Every other fixed GET must return 200.
OFFLINE_STATUS = {
    "/api/calendly/appointment-types": 422,
    "/api/config/export": 405,
    "/api/local-ai/backends": 503,
    "/api/mcp/status": 503,
    "/api/system/ari/endpoints": 502,
    "/api/system/containers": 500,
    "/api/system/docker/disk-usage": 500,
    "/api/system/updates/branches": 500,
    "/api/system/updates/plan": 500,
}


def test_no_secret_in_get_responses(tmp_path):
    # main and its routers cache settings and initialize stores on first import.
    # A fresh interpreter isolates them even when other tests have imported main.
    result = subprocess.run(
        [sys.executable, __file__, str(tmp_path)], cwd=tmp_path,
        env={
            "PYTHONPATH": os.pathsep.join(map(str, (BACKEND, REPO))),
            "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(tmp_path),
            "PROJECT_ROOT": str(tmp_path), "PATH": os.defpath,
            "ENABLE_API_DOCS": "true",
        },
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
    )
    receipt = tmp_path / "scan-result.json"
    assert receipt.is_file(), f"Scan worker exited with code {result.returncode}"
    report = json.loads(receipt.read_text())
    assert not report["errors"], "\n".join(report["errors"])
    assert result.returncode == 0
    print(report["summary"], report["offline"])


def _isolate(root):
    def require_fixture(path):
        if not isinstance(path, int) and not Path(os.fsdecode(path)).resolve().is_relative_to(root):
            raise PermissionError("release scan requires fixture storage")

    def guard(event, args):
        if event.startswith("socket.") and event not in {"socket.__new__", "socket.gethostname"}:
            raise PermissionError("external service unavailable in release scan")
        if event in {
            "subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.fork",
        }:
            raise PermissionError("external service unavailable in release scan")
        if event == "open":
            path, _, flags = args
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
                require_fixture(path)
            elif not isinstance(path, int):
                file = Path(os.fsdecode(path))
                if file.name == ".env" or file.parts[1:2] in (("app",), ("mnt",), ("host",)):
                    require_fixture(file)
        if event == "sqlite3.connect" and args[0] != ":memory:":
            database = os.fsdecode(args[0])
            require_fixture(unquote(urlsplit(database).path) if database.startswith("file:") else database)
        if event in {"os.remove", "os.mkdir", "os.rmdir", "os.chmod"}:
            require_fixture(args[0])
        if event == "os.rename":
            require_fixture(args[0])
            require_fixture(args[1])

    sys.addaudithook(guard)


# Counters and assertions report only these labels, never a fixture value or body.
SHAPES = {
    label: hashlib.sha256(("synthetic release fixture " + label).encode()).hexdigest()[:40]
    for label in (
        "webhook-path", "url-query", "header-apikey", "header-x-auth", "query-code",
        "query-sig", "body-bearer", "template-provider-key", "mcp-env", "mcp-arg",
        "url-userinfo", "service-account-json", "ws-extra-header",
    )
}


PUBLIC_PROMPT = "Collect basic information from the caller."
PUBLIC_MODEL_URL = "https://public.example.invalid/models?model=nova-3"
TOOL_ORIGIN = "https://hooks.example.invalid:8443"


def _structural_fixture():
    """Synthetic editor data. No transport, provider or receiver consumes these values."""
    import base64

    def b64(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    canaries = {label: hashlib.sha256(("editor-control/" + label).encode()).hexdigest()[:36]
                for label in ("json-pair", "assignment", "flag-next", "flag-inline", "Token", "ApiKey", "Bearer",
                              "sk_", "jwt", "database-userinfo", "header", "query", "environment", "extra-header", "account-json")}
    canaries["jwt"] = ".".join((b64({"alg": "HS256", "typ": "JWT"}), b64({"sub": "fixture"}), canaries["jwt"]))
    canaries["short-path"] = "q4R7"
    template = json.dumps({"api_key": canaries["json-pair"], "message": " ".join(
        ["token=" + canaries["assignment"], "sk_" + canaries["sk_"], canaries["jwt"]]
        + [scheme + " " + canaries[scheme] for scheme in ("Bearer", "Token", "ApiKey")])})
    desired = {
        "greeting": PUBLIC_PROMPT, "instructions": PUBLIC_PROMPT,
        "in_call_tools": {"fixture": {"kind": "in_call_http_lookup", "description": PUBLIC_PROMPT,
            "url": TOOL_ORIGIN + "/editor-path/" + canaries["short-path"], "body_template": template,
            "query_params": {"label": canaries["query"]},
            "headers": {"X-Version": canaries["header"], "Content-Type": "application/json"}}},
        "mcp": {"servers": {"fixture": {"env": {"LABEL": canaries["environment"]},
            "args": ["--api-key", canaries["flag-next"], "--token=" + canaries["flag-inline"],
                     "postgres://fixture:" + canaries["database-userinfo"] + "@db.example.invalid/report"]}}},
        "providers": {"fixture": {"instructions": PUBLIC_PROMPT, "ws_extra_headers": {"X-Version": canaries["extra-header"]},
                                  "service_account_json": json.dumps({"field": canaries["account-json"]})}},
    }
    return desired, canaries


def test_editor_structural_canaries_and_public_roundtrip():
    # Exercise the redaction boundary directly before any API integration. The
    # archived baseline can run this check without an application, socket or DB.
    import yaml
    from services.secret_keys import redact_mapping
    from services.yaml_secrets import has_markers, mask_config, mask_yaml_secrets, restore_config, restore_yaml_secrets

    desired, canaries = _structural_fixture()
    raw = yaml.safe_dump(desired)
    structured = mask_config(desired)
    masked_raw = mask_yaml_secrets(raw)
    failures = []
    for editor, output in (("structured", json.dumps(structured)), ("raw", masked_raw)):
        failures.extend(f"{editor}/{label}" for label, value in canaries.items() if value in output)
        if PUBLIC_PROMPT not in output:
            failures.append(editor + "/public-prose")
    if structured["in_call_tools"]["fixture"]["url"] != {"origin": TOOL_ORIGIN, "set": True}:
        failures.append("structured/tool-origin")
    raw_url = yaml.safe_load(masked_raw)["in_call_tools"]["fixture"]["url"]
    if not (isinstance(raw_url, str) and TOOL_ORIGIN in raw_url and has_markers(raw_url) and "/editor-path/" not in raw_url):
        failures.append("raw/tool-origin")
    for editor, document in (("structured", structured), ("raw", yaml.safe_load(masked_raw))):
        tool = document["in_call_tools"]["fixture"]
        if any(value != PUBLIC_PROMPT for value in (document["greeting"], document["instructions"], tool["description"],
                                                    document["providers"]["fixture"]["instructions"])):
            failures.append(editor + "/public-fields")
        if tool["headers"]["Content-Type"] != "application/json":
            failures.append(editor + "/public-header")
    if redact_mapping({"MODEL_CATALOG_URL": PUBLIC_MODEL_URL}) != {"MODEL_CATALOG_URL": PUBLIC_MODEL_URL}:
        failures.append("dotenv/public-url")
    if restore_config(structured, desired) != desired or yaml.safe_load(restore_yaml_secrets(masked_raw, raw)) != desired:
        failures.append("editor/restore")
    assert not failures, ", ".join(failures)


def _seed_tool():
    return {
        "kind": "in_call_http_lookup", "phase": "in_call", "enabled": False, "method": "POST",
        "url": f"https://hooks.example.invalid/notify/{SHAPES['webhook-path']}?notice={SHAPES['url-query']}",
        "headers": {"Content-Type": "application/json", "apikey": SHAPES["header-apikey"], "X-Auth": SHAPES["header-x-auth"]},
        "query_params": {"code": SHAPES["query-code"], "sig": SHAPES["query-sig"]},
        "body_template": json.dumps({"message": "Bearer " + SHAPES["body-bearer"], "notes": "sk-proj-" + SHAPES["template-provider-key"]}),
    }


def _fixture(root):
    import settings
    import yaml

    settings.PROJECT_ROOT = str(root)
    for name, relative in {
        "CONFIG_PATH": "config/ai-agent.yaml", "LOCAL_CONFIG_PATH": "config/ai-agent.local.yaml",
        "ENV_PATH": ".env", "ENV_EXAMPLE_PATH": ".env.example", "USERS_PATH": "config/users.json",
    }.items():
        setattr(settings, name, str(root / relative))
    for key, relative in {
        "AGENTS_DB_PATH": "agents.db", "CALL_HISTORY_DB_PATH": "calls.db",
        "CALENDLY_DB_PATH": "calendly.db", "VICIDIAL_DB_PATH": "vicidial.db",
        "AAVA_MEDIA_DIR": "media", "AST_MEDIA_DIR": "media", "AAVA_MODELS_DIR": "models",
    }.items():
        os.environ[key] = str(root / relative)
    for directory in ("config", "media", "models", "src", "static/assets"):
        (root / directory).mkdir(parents=True)
    (root / "static" / ASSET.lstrip("/")).write_text(PUBLIC)
    (root / "static/index.html").write_text(PUBLIC)
    (root / "media/scan.ulaw").write_bytes(b"\xff" * 160)
    example = (REPO / ".env.example").read_text()
    Path(settings.ENV_EXAMPLE_PATH).write_text(example)
    # Independent of the production redactor so a classifier regression is detectable.
    assignments = re.findall(r"^\s*(?:#\s*)?([A-Z][A-Z0-9_]*)\s*=", example, re.M)
    names = {name for name in assignments if re.search(
        r"(?:^|_)(?:KEY|PASSWORD|PASSWD|PASS|SECRET|TOKEN|CREDENTIALS?)(?:_|$)|_WEBHOOK_URL$", name
    )}
    assert {"JWT_SECRET", "OPENAI_API_KEY", "ASTERISK_ARI_PASSWORD", "VICIDIAL_API_PASS"} <= names
    # Credential *paths* identify files, not their contents.
    names -= {"CALENDLY_TOKEN_PATH", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CALENDAR_CREDENTIALS"}
    env = {name: f"CANARY-ENV-{name}" for name in sorted(names)}
    env["ASTERISK_HOST"] = "127.0.0.1"
    env["ASTERISK_ARI_USERNAME"] = "scan-operator"
    for name in ("CALENDLY_TOKEN_PATH", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CALENDAR_CREDENTIALS"):
        env[name] = str(root / "credential-files" / (name.lower() + ".json"))
    env["SCAN_PUBLIC_CONTROL"] = PUBLIC
    Path(settings.ENV_PATH).write_text("".join(f"{k}={v}\n" for k, v in env.items()))
    Path(settings.CONFIG_PATH).write_text(yaml.safe_dump({
        "default_provider": "openai_realtime",
        "asterisk": {"host": "127.0.0.1", "username": "scan-operator", "password": "${ASTERISK_ARI_PASSWORD}"},
        "llm": {},
        "providers": {"openai_realtime": {
            "api_key": "CANARY-INLINE",
            "service_account_json": json.dumps({"private_key": SHAPES["service-account-json"]}),
            "ws_extra_headers": {"Version": SHAPES["ws-extra-header"]},
        }},
        "in_call_tools": {"release_lookup": _seed_tool()},
        "tools": {"leave_voicemail": {"enabled": False}},
        "mcp": {"servers": {"release_adapter": {
            "command": ["synthetic"], "env": {"PROJECT_LABEL": SHAPES["mcp-env"]},
            "args": ["Bearer " + SHAPES["mcp-arg"], f"https://fixture:{SHAPES['url-userinfo']}@example.invalid/public"],
        }}},
        "scan_fields": {name: f"CANARY-YAML-{name}" for name in sorted(names)},
        "scan_public_control": PUBLIC,
    }))
    Path(settings.LOCAL_CONFIG_PATH).write_text(yaml.safe_dump({"scan_local": {"password": "CANARY-OVERRIDE"}}))
    return names


async def _request(app, path, token="", form=None, query=None, method=None, payload=None):
    disconnected = asyncio.Event()
    encoded = json.dumps(payload).encode() if payload is not None else form or b""
    sent_request = False
    status, headers, body = None, {}, bytearray()

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": encoded, "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        nonlocal status, headers
        if message["type"] == "http.response.start":
            status, headers = message["status"], dict(message["headers"])
        elif message["type"] == "http.response.body":
            body.extend(message.get("body", b""))
            # Observe the initial real snapshot before disconnecting a live subscriber.
            if headers.get(b"content-type", b"").startswith(b"text/event-stream") and b"\n\n" in body:
                disconnected.set()

    request_headers = [(b"host", b"release.test"), (b"accept-encoding", b"identity")]
    if token:
        request_headers.append((b"authorization", f"Bearer {token}".encode()))
    if form is not None:
        request_headers.append((b"content-type", b"application/x-www-form-urlencoded"))
    if payload is not None:
        request_headers.append((b"content-type", b"application/json"))
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": method or ("POST" if form is not None or payload is not None else "GET"),
        "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
        "query_string": urlencode(query or {}).encode(), "headers": request_headers,
        "client": ("127.0.0.1", 31000), "server": ("release.test", 80),
    }
    try:
        await asyncio.wait_for(app(scope, receive, send), timeout=12)
    except Exception:
        # ServerErrorMiddleware sends the actual 500 response, then re-raises.
        if status != 500:
            raise
    assert status is not None, path
    return status, bytes(body)


class CredentialInResponse(AssertionError):
    pass


def _assert_clean(body):
    chunks = [body]
    if zipfile.is_zipfile(io.BytesIO(body)):
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            for name in archive.namelist():
                chunks.extend((name.encode(), archive.read(name)))
    labels = [label for label, value in SHAPES.items() if any(value.encode() in chunk for chunk in chunks)]
    if any(PREFIX in chunk for chunk in chunks):
        labels.append("named-credential")
    if labels:
        raise CredentialInResponse(", ".join(labels))


def _case_failure(exc):
    return str(exc) if isinstance(exc, CredentialInResponse) else type(exc).__name__


async def _scan_docker_success(app, token, client, report):
    # Stub only external SDK/CLI results. Keep the real routes, parsing,
    # serialization and response scanning in both offline and configured cases.
    def docker_df(command, **kwargs):
        assert command == ["docker", "system", "df", "-v", "--format", "json"]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"Images": [{"Size": "4KB"}]}))

    samples = {
        "/api/system/containers": lambda data: data[0]["id"] == PUBLIC,
        "/api/system/docker/disk-usage": lambda data: data["images"]["size"] == "4.0KB",
        "/api/system/updates/branches": lambda data: data["branches"] == [PUBLIC],
        "/api/system/updates/plan": lambda data: data["plan"]["target"] == PUBLIC,
    }
    for path, verify in samples.items():
        status = None
        try:
            with ExitStack() as boundaries:
                boundaries.enter_context(patch("docker.from_env", return_value=client))
                if path.endswith("/disk-usage"):
                    boundaries.enter_context(patch("subprocess.run", side_effect=docker_df))
                if path.endswith("/branches") or path.endswith("/plan"):
                    output = f"abc\trefs/heads/{PUBLIC}" if path.endswith("/branches") else json.dumps({"target": PUBLIC})
                    boundaries.enter_context(patch("api.system._run_updater_ephemeral", return_value=(0, output)))
                status, body = await _request(app, path, token)
            _assert_clean(body)
            assert status == 200 and verify(json.loads(body))
        except Exception as exc:
            report["errors"].append(f"GET {path} (configured): HTTP {status}, {type(exc).__name__}")
    return len(samples)


async def _scan_configuration_actions(app, token, root, report):
    """Read seeded IDs, then exercise ordinary editor, preview and setup responses."""
    import httpx
    import yaml

    fixture = yaml.safe_load((root / "config/ai-agent.yaml").read_text())
    ari = {"host": "127.0.0.1", "username": "scan-operator"}
    setup = {"asterisk_host": ari["host"], "asterisk_username": ari["username"],
             "greeting": "Hello", "ai_role": "Assistant", "ai_name": "Fixture"}
    push = {"phase": "in_call", "title": PUBLIC, "body": "Message"}
    samples = []
    for name, document in fixture["in_call_tools"].items():
        samples.extend([
            ("GET", f"/api/tools/managed/{name}", None, 200),
            ("POST", "/api/tools/http-templates/validate", {"phase": "in_call", "config": document}, 200),
            ("POST", "/api/tools/test-http", {"tool_name": name, **document}, 200),
        ])
    for name in fixture["tools"]:
        samples.append(("GET", f"/api/tools/builtin/{name}", None, 200))
    for name, config in fixture["providers"].items():
        samples.extend([
            ("GET", f"/api/config/providers/{name}/credentials", None, 200),
            ("GET", f"/api/config/options/{name}", None, 200),
            ("POST", "/api/config/providers/test", {"name": name, "config": config}, 200),
        ])
    for server in fixture["mcp"]["servers"]:
        samples.append(("POST", f"/api/mcp/servers/{server}/test", {}, 200))
    samples.extend([
        ("POST", "/api/tools/a2a/validate",
         {"name": "release_delegate", "config": {"kind": "a2a_agent", "agent_url": "https://agent.example.invalid"}}, 200),
        ("POST", "/api/tools/transfer-destinations/validate", {"additions": [
            {"key": "fixture_desk", "target": "101", "description": PUBLIC, "dialplan_context": "from-internal"}
        ]}, 200),
        ("POST", "/api/tools/email-templates/preview", {"tool": "send_email_summary", "html_template": PUBLIC}, 200),
        ("POST", "/api/pushover/templates/validate", {"config": push, "phase": "in_call"}, 200),
        ("POST", "/api/pushover/preview", {"config": push}, 200),
        ("POST", "/api/ollama/test", {"base_url": "http://mock.example.invalid"}, 200),
        ("POST", "/api/system/test-ari", ari, 200),
        ("POST", "/api/wizard/validate-key", {"provider": "openai"}, 200),
        ("POST", "/api/wizard/validate-connection", ari, 200),
        ("POST", "/api/support/system-bundle", {"include_config": True, "include_ai_engine": False,
                                                "include_local_ai_server": False, "include_admin_ui": False}, 200),
        ("POST", "/api/tools/managed", {"name": "release_created", **_seed_tool()}, 201),
        ("POST", "/api/wizard/init-env", {}, 200),
        ("POST", "/api/wizard/save", setup, 200),
        # Explicitly retain the offline readiness error without acknowledging a live test call.
        ("POST", "/api/wizard/complete", {"greeting_heard": False, "two_way_audio": False,
                                          "clean_hangup": False, "history_checked": False}, 400),
    ])

    # Force every HTTP client in these handlers to use an in-memory adapter.
    # Public addresses and stored .env credentials never leave the interpreter.
    client = httpx.AsyncClient
    def offline_client(*args, **kwargs):
        def respond(request):
            return httpx.Response(200, json={"system": {"version": "fixture"}, "models": [], "success": True})
        return client(*args, **kwargs, transport=httpx.MockTransport(respond))

    for method, path, payload, expected in samples:
        status = None
        try:
            with ExitStack() as boundaries:
                boundaries.enter_context(patch.object(httpx, "AsyncClient", offline_client))
                if path == "/api/tools/test-http":
                    import socket
                    boundaries.enter_context(patch.dict(os.environ, {"AAVA_HTTP_TOOL_TEST_ALLOW_HOSTS": "hooks.example.invalid"}))
                    boundaries.enter_context(patch("socket.getaddrinfo", return_value=[
                        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.0.2.1", 443)),
                    ]))
                status, body = await _request(app, path, token, method=method, payload=payload)
            _assert_clean(body)
            assert status == expected
            if path == "/api/support/system-bundle":
                with zipfile.ZipFile(io.BytesIO(body)) as archive:
                    assert PUBLIC.encode() in archive.read("config/current_config.sanitized.json")
            if path == "/api/tools/email-templates/preview":
                assert PUBLIC.encode() in body
            if path in {"/api/wizard/validate-key", "/api/wizard/validate-connection"}:
                assert json.loads(body)["valid"] is True
            if path == "/api/tools/test-http":
                assert json.loads(body)["success"] is True
            if path == "/api/wizard/save":
                assert json.loads(body)["status"] == "success"
        except Exception as exc:
            report["errors"].append(f"{method} {path}: HTTP {status}, {_case_failure(exc)}")
    return len(samples)


async def _scan_editor_roundtrip(app, token, root):
    """Check editor reads, public saves, and recipient refusals in the isolated fixture."""
    import yaml

    test_editor_structural_canaries_and_public_roundtrip()
    desired, canaries = _structural_fixture()
    path = root / "config/ai-agent.yaml"
    original = path.read_bytes()
    try:
        base = yaml.safe_load(original)
        base["greeting"] = PUBLIC_PROMPT
        base["providers"]["openai_realtime"].update(desired["providers"]["fixture"])
        base["in_call_tools"]["release_lookup"].update(desired["in_call_tools"]["fixture"])
        base["mcp"]["servers"]["release_adapter"].update(desired["mcp"]["servers"]["fixture"])
        path.write_text(yaml.safe_dump(base))
        for route in ("/api/config", "/api/config/yaml", "/api/tools/managed/release_lookup"):
            code, response = await _request(app, route, token)
            assert code == 200, "editor read unavailable"
            missed = [label for label, value in canaries.items() if value.encode() in response]
            if missed:
                raise CredentialInResponse(", ".join(missed))
            assert PUBLIC_PROMPT.encode() in response, "public prose missing from editor read"
            assert TOOL_ORIGIN.encode() in response, "public origin missing from editor read"
        code, reply = await _request(app, "/api/config/env", token, method="POST", payload={"MODEL_CATALOG_URL": PUBLIC_MODEL_URL})
        assert code == 200, "public env save unavailable"
        _assert_clean(reply)
        code, reply = await _request(app, "/api/config/env", token)
        assert code == 200 and json.loads(reply)["MODEL_CATALOG_URL"] == PUBLIC_MODEL_URL, "public env value changed"
        # Use the normal save boundary against the isolated configuration.
        # Changed recipients must be rejected before any persistence or apply.
        for group in ("providers", "mcp"):
            code, document = await _request(app, "/api/config/yaml", token)
            assert code == 200
            draft = yaml.safe_load(json.loads(document)["content"])
            if group == "providers":
                draft[group]["openai_realtime"]["base_url"] = "wss://replacement.invalid/realtime"
                expected_field = "api_key"
            else:
                draft[group]["servers"]["release_adapter"]["command"] = ["fixture-alternate"]
                expected_field = "env"
            code, reply = await _request(app, "/api/config/yaml", token, method="POST", payload={"content": yaml.safe_dump(draft)})
            assert code == 400 and expected_field in json.loads(reply).get("detail", ""), group
            _assert_clean(reply)
            assert all(value.encode() not in reply for value in canaries.values())
        return 9
    finally:
        path.write_bytes(original)


def _agent_fixture(label):
    def document(column):
        def value(slot):
            return "CANARY-ROW-" + hashlib.sha256(f"{label}/{column}/{slot}".encode()).hexdigest()[:20]

        return {
            "tool_overrides": {"lookup": {
                "url": "https://tools.example.invalid/" + value("path"),
                "webhook_url": "https://tools.example.invalid/" + value("webhook"),
                "headers": {"X-Custom": value("header")},
                "query_params": {"slot": value("query")},
                "body_template": "token=" + value("template"),
            }},
            "mcp": {"servers": {"worker": {
                "env": {"LABEL": value("env")}, "args": ["--api-key", value("argument")],
            }}},
        }

    return {
        "display_name": label, "slug": label, "prompt": PUBLIC, "provider": "local",
        "extra_json": json.dumps(document("extra_json")), "mcp_json": json.dumps(document("mcp_json")),
    }


async def _scan_agent_rows(app, token, report):
    from agents_store import AgentsStore
    with AgentsStore() as store:
        store.create(**_agent_fixture("row_fixture"))
    route = "/api/agents/row_fixture"
    cases = [("list","GET","/api/agents",None,200), ("detail","GET",route,None,200),
             ("create","POST","/api/agents",_agent_fixture("row_created"),201),
             ("patch","PATCH",route,{"greeting":PUBLIC},200),
             ("activate","PATCH",route,{"is_active":True},200),
             ("default","POST",route + "/default",None,200)]
    for name,method,path,payload,expected in cases:
        try:
            status, body = await _request(app,path,token,method=method,payload=payload)
            _assert_clean(body)
            assert status == expected and PUBLIC.encode() in body
        except Exception as exc:
            report["errors"].append(f"Agent row {name}: {_case_failure(exc)}")
    return len(cases)


async def _scan_business_data(app, token, report, record_id):
    import csv
    from api.outbound import _get_outbound_store

    store = _get_outbound_store()
    campaign = await store.create_campaign({
        "name": PUBLIC,
        "voicemail_drop_media_uri": "https://user:CANARY-media@media.invalid/test?sig=CANARY-media-query",
        "consent_media_uri": "https://media.invalid/consent?ticket=CANARY-consent-query",
    })
    base = "/api/outbound/campaigns/" + campaign["id"]
    source = io.StringIO()
    writer = csv.writer(source)
    writer.writerow(["name", "phone_number", "custom_vars"])
    writer.writerow([PUBLIC, "+15555550101", json.dumps({
        "business": PUBLIC, "nested": {"db_pwd": "CANARY-lead-pass"},
        "item": "postgres://user:CANARY-lead-url@fixture.invalid/db",
    })])
    await store.import_leads_csv(campaign["id"], source.getvalue().encode())
    for path in (base, base + "/leads", "/api/calls/" + record_id,
                 "/api/calls/export/json", "/api/calls/export/csv"):
        try:
            status, body = await _request(app, path, token)
            _assert_clean(body)
            assert status == 200 and PUBLIC.encode() in body
        except Exception as exc:
            report["errors"].append(f"Business data {path}: {_case_failure(exc)}")


async def _scan(root, report):
    from fastapi.routing import iter_route_contexts
    from services import frontend_static

    names = _fixture(root)
    _isolate(root)
    from dotenv import load_dotenv
    load_dotenv(root / ".env")
    import auth

    password = secrets.token_urlsafe(32)
    auth.save_users({"scanner": {"username": "scanner", "hashed_password": auth.get_password_hash(password)}})
    mount = frontend_static.mount_frontend
    with patch.object(frontend_static, "mount_frontend", lambda app, _: mount(app, root / "static")):
        from main import app

    from api import config, outbound
    from src.core.call_history import CallRecord, get_call_history_store
    outbound._media_dir = lambda: str(root / "media")
    config.PROVIDER_SECRETS_ROOT = str(root / "credential-files" / "providers")
    now = datetime.now(timezone.utc)
    record = CallRecord(call_id="release-scan", start_time=now, end_time=now,
                        call_metadata={"label": PUBLIC, "order_key": "CANARY-order-key",
                                       "link": "ssh://user:CANARY-call@fixture.invalid/x"},
                        external_metadata={"nested": [{"session_token": "CANARY-vici"}]})
    assert await get_call_history_store().save(record)
    record = await get_call_history_store().get_by_call_id("release-scan")

    form = urlencode({"username": "scanner", "password": password}).encode()
    status, result = await _request(app, "/api/auth/login", form=form)
    assert status == 200, "synthetic user login failed"
    token = json.loads(result)["access_token"]

    await _scan_business_data(app, token, report, record.id)
    agent_requests = await _scan_agent_rows(app, token, report)
    paths = {route.path for route in iter_route_contexts(app.routes)
             if "GET" in (route.methods or ()) and "{" not in route.path}
    schema = app.openapi()["paths"]
    documented = {path for path, methods in schema.items() if "get" in methods and "{" not in path}
    assert documented <= paths
    assert OFFLINE_STATUS.keys() <= paths
    assert {"/docs", "/redoc", "/openapi.json", "/api/config/env", "/api/config/export", "/api/system/live-status/stream"} <= paths
    stats = Counter()
    query_samples = {"media_uri": "sound:ai-generated/scan", "q": "setup", "call_id": "release-scan"}
    # Exercise container drift using a synthetic running container with divergent secrets.
    container = SimpleNamespace(
        id=PUBLIC, name="ai_engine", status="running", image=SimpleNamespace(tags=["fixture:1"]),
        attrs={
            "Config": {"Env": [f"{k}=CANARY-RUNTIME-{k}" for k in names]},
            "Mounts": [{"Type": "bind", "Source": str(root), "Destination": str(root)}],
        },
    )
    docker_client = SimpleNamespace(containers=SimpleNamespace(list=lambda **_: [container], get=lambda _: container))
    for path in sorted(paths | {"/", "/index.html", ASSET}):
        status = None
        required = [p["name"] for p in schema.get(path, {}).get("get", {}).get("parameters", ())
                    if p["in"] == "query" and p.get("required")]
        try:
            credential = os.environ["AAVA_SPEECH_API_KEY"] if path == "/v1/models" else token
            query = {key: query_samples[key] for key in required}
            boundary = patch("docker.from_env", return_value=docker_client) if path == "/api/config/env/status" else nullcontext()
            with boundary:
                status, body = await _request(app, path, credential, query=query)
            _assert_clean(body)
            assert status == OFFLINE_STATUS.get(path, 200)
            if status == 422:
                assert not isinstance(json.loads(body).get("detail"), list), "required query input failed validation"
            if path in {"/api/config", "/api/config/"}:
                tool = json.loads(body)["in_call_tools"]["release_lookup"]
                assert tool["url"] == {"origin": "https://hooks.example.invalid", "set": True}
                assert tool["headers"]["Content-Type"] == "application/json"
            if path in {"/api/config", "/api/config/", "/api/config/yaml", "/api/config/env", "/", "/index.html", ASSET}:
                assert status == 200 and PUBLIC.encode() in body, "missing public fixture control"
            if path == "/api/config/env":
                env = json.loads(body)
                assert all(env[name] == {"set": True} for name in names)
                for name in ("CALENDLY_TOKEN_PATH", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CALENDAR_CREDENTIALS"):
                    assert env[name] == str(root / "credential-files" / (name.lower() + ".json"))
            if path == "/api/wizard/load-config":
                assert status == 200 and json.loads(body)["asterisk_password"] == {"set": True}
            if path == "/api/system/live-status/stream":
                assert status == 200 and b"data:" in body and b"event: snapshot" in body
            if path in {"/api/config/export-logs", "/api/support-bundle"}:
                assert status == 200 and zipfile.is_zipfile(io.BytesIO(body))
            if path == "/api/auth/me":
                assert status == 200 and json.loads(body)["username"] == "scanner"
            stats[status] += 1
            if status >= 400:
                report["offline"][path] = status
        except Exception as exc:
            # Responses, request headers and exception text can all contain credentials.
            line = traceback.extract_tb(exc.__traceback__)[-1].lineno
            report["errors"].append(f"GET {path}: HTTP {status}, {_case_failure(exc)} (line {line})")
    editor_requests = 0
    try:
        editor_requests = await _scan_editor_roundtrip(app, token, root)
    except Exception as exc:
        report["errors"].append(f"Structural editor round-trip: {_case_failure(exc)}")
    actions = await _scan_configuration_actions(app, token, root, report)
    configured = await _scan_docker_success(app, token, docker_client, report)
    report["summary"] = (
        f"{len(paths)} fixed GET routes + 3 frontend probes, "
        f"{len(names)} sample credential names, HTTP {dict(stats)}, {configured} configured Docker/update probes, {agent_requests} agent requests, {actions} configuration requests, {editor_requests} structural editor requests"
    )


if __name__ == "__main__":
    fixture_root = Path(sys.argv[1]).resolve()
    report = {"errors": [], "offline": {}, "summary": ""}
    try:
        asyncio.run(_scan(fixture_root, report))
    except Exception as exc:
        where = traceback.extract_tb(exc.__traceback__)[-1]
        report["errors"].append(f"Scan setup: {type(exc).__name__} ({Path(where.filename).name}:{where.lineno})")
    (fixture_root / "scan-result.json").write_text(json.dumps(report))
