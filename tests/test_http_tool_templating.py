"""Request-level HTTP template regressions using synthetic data and no network."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, quote

import aiohttp
import pytest
from yarl import URL

from src.tools.context import PreCallContext, PostCallContext, ToolExecutionContext
from src.tools.http.generic_lookup import GenericHTTPLookupTool, HTTPLookupConfig
from src.tools.http.generic_webhook import GenericWebhookTool, WebhookConfig
from src.tools.http.in_call_lookup import InCallHTTPTool, InCallHTTPConfig
from src.tools.http.template import TemplateError, render_body, render_json, render_template


SECRET = "synthetic-canary-only"
HOSTILE = 'A"\\\n\r雪 % + &new=bad ?q=#frag/path ${CANARY_SECRET} {call_id}'


@pytest.fixture(autouse=True)
def canaries(monkeypatch):
    monkeypatch.setenv("CANARY_SECRET", SECRET)
    monkeypatch.setenv("HTTP_BASE", "https://example.invalid")
    monkeypatch.delenv("MISSING_TEMPLATE_ENV", raising=False)


@pytest.fixture
def http(monkeypatch):
    class Content:
        async def iter_chunked(self, _size):
            yield b'{"ok": true}'

    response = AsyncMock()
    response.status = 200
    response.headers = {}
    response.charset = "utf-8"
    response.content = Content()
    response.text.return_value = '{"ok": true}'
    session = MagicMock()
    session.request.return_value.__aenter__.return_value = response
    client = MagicMock()
    client.return_value.__aenter__.return_value = session
    monkeypatch.setattr(aiohttp, "ClientSession", client)
    return session.request


def tool_and_context(phase, **options):
    common = dict(name="synthetic", url="${HTTP_BASE}/request", method="POST")
    common.update(options)
    if phase == "pre":
        return GenericHTTPLookupTool(HTTPLookupConfig(**common)), PreCallContext(
            call_id="call-1", caller_number=HOSTILE, caller_name=HOSTILE, context_name="test"
        )
    if phase == "in":
        return InCallHTTPTool(InCallHTTPConfig(**common)), ToolExecutionContext(
            call_id="call-1", caller_number=HOSTILE, caller_name=HOSTILE, context_name="test"
        )
    return GenericWebhookTool(WebhookConfig(**common)), PostCallContext(
        call_id="call-1", caller_number=HOSTILE, caller_name=HOSTILE, context_name="test",
        summary=HOSTILE, conversation_history=[{"role": "user", "content": HOSTILE}],
    )


async def execute(tool, context, parameters=None):
    if isinstance(tool, InCallHTTPTool):
        return await tool.execute(parameters or {}, context)
    return await tool.execute(context)


def body_of(request):
    kwargs = request.call_args.kwargs
    return kwargs["json"] if "json" in kwargs else json.loads(kwargs["data"])


def test_single_pass_and_missing_values(monkeypatch):
    monkeypatch.setenv("NESTED_ENV", "${CANARY_SECRET}/{second}")
    assert render_template("${CANARY_SECRET}|{first}|{second}|${NESTED_ENV}", {
        "first": "${CANARY_SECRET}/{second}", "second": "done",
    }) == SECRET + "|${CANARY_SECRET}/{second}|done|${CANARY_SECRET}/{second}"
    assert render_template("{missing}|${MISSING_TEMPLATE_ENV}|{empty}", {"empty": None}) == "{missing}||"
    assert render_template("${lower}", {"lower": "no"}) == "${lower}"
    assert render_template("{customer.name}|{customer-id}", {"customer.name": "one", "customer-id": "two"}) == "one|two"


@pytest.mark.parametrize("phase", ["pre", "in", "post"])
@pytest.mark.parametrize("literal", ["${CANARY_SECRET}", "{call_id}"])
@pytest.mark.asyncio
async def test_request_values_cannot_trigger_a_second_expansion(phase, literal, http):
    key = "payload_template" if phase == "post" else "body_template"
    tool, context = tool_and_context(phase, **{key: '{"name":"{caller_name}"}'})
    context.caller_name = literal
    await execute(tool, context)
    assert body_of(http) == {"name": literal}


@pytest.mark.asyncio
async def test_model_notes_are_literal_in_executed_request(http):
    tool, context = tool_and_context("in", parameters=[{"name": "notes"}], body_template='{"notes":"{notes}"}')
    await execute(tool, context, {"notes": "${CANARY_SECRET} {call_id}"})
    assert body_of(http) == {"notes": "${CANARY_SECRET} {call_id}"}


@pytest.mark.asyncio
async def test_transcript_and_summary_tokens_are_literal_in_executed_request(http):
    tool, context = tool_and_context("post", payload_template='{"transcript":{transcript_json},"summary":{summary_json},"plain":"{summary}"}')
    context.conversation_history = [{"role": "user", "content": "${CANARY_SECRET}"}]
    context.summary = "${CANARY_SECRET} {call_id}"
    await execute(tool, context)
    assert body_of(http) == {"transcript": context.conversation_history, "summary": context.summary, "plain": context.summary}


@pytest.mark.parametrize("phase", ["pre", "in", "post"])
@pytest.mark.asyncio
async def test_execute_keeps_caller_data_literal_and_encodes_components(phase, http):
    options = dict(
        url="${HTTP_BASE}/people/{caller_number}?name={caller_name}",
        headers={"Authorization": "Bearer ${CANARY_SECRET}", "X-Caller": "{caller_name}"},
    )
    template = '{"name":"{caller_name}","phone":"{caller_number}","id":"{call_id}"}'
    options["payload_template" if phase == "post" else "body_template"] = template
    if phase != "post":
        options["query_params"] = {"phone": "{caller_number}"}
    tool, context = tool_and_context(phase, **options)
    await execute(tool, context)
    http.assert_called_once()
    request = http.call_args.kwargs
    assert body_of(http) == {"name": HOSTILE, "phone": HOSTILE, "id": "call-1"}
    assert request["headers"]["Authorization"] == "Bearer " + SECRET
    assert request["headers"]["X-Caller"] == HOSTILE.replace("\r", " ").replace("\n", " ")
    assert request["url"] == "https://example.invalid/people/" + quote(HOSTILE, safe="") + "?name=" + quote(HOSTILE, safe="")
    # Exercise aiohttp's real URL preparation without constructing a connection.
    prepared = aiohttp.ClientRequest("POST", URL(request["url"]), params=request.get("params"))
    assert prepared.url.query["name"] == HOSTILE
    assert not prepared.url.fragment
    assert SECRET not in str(prepared.url)
    if phase != "post":
        assert request["params"]["phone"] == HOSTILE
        assert prepared.url.query["phone"] == HOSTILE


@pytest.mark.asyncio
async def test_in_call_declared_collisions_empty_enrichment_and_metadata(http):
    keys = ["caller_number", "call_id", "contact_id", "empty", "customer_tier", "notes"]
    tool, context = tool_and_context("in", parameters=[{"name": key} for key in keys],
        body_template='{"phone":"{caller_number}","id":"{call_id}","contact":"{contact_id}","empty":"{empty}","tier":"{customer_tier}","notes":"{notes}"}')
    context.session_store = SimpleNamespace(get_by_call_id=AsyncMock(return_value=SimpleNamespace(
        pre_call_results={"contact_id": "trusted", "empty": "", "customer_tier": "silver", "call_id": "bad-pre"},
        call_metadata={"customer_tier": HOSTILE, "call_id": "bad-metadata"},
    )))
    arguments = {key: "model-override" for key in keys}
    arguments.update(notes=HOSTILE, undeclared="must-not-appear")
    await execute(tool, context, arguments)
    assert body_of(http) == {"phone": HOSTILE, "id": "call-1", "contact": "trusted", "empty": "", "tier": HOSTILE, "notes": HOSTILE}
    sub = await tool._build_substitution_context(arguments, context)
    assert "undeclared" not in sub


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.asyncio
async def test_enrichment_failure_does_not_fall_back_to_model_identity(http, missing, caplog):
    tool, context = tool_and_context("in", parameters=[{"name": "contact_id"}], body_template='{"id":"{contact_id}"}')
    context.session_store = SimpleNamespace(get_by_call_id=AsyncMock(return_value=None))
    if not missing:
        context.session_store.get_by_call_id.side_effect = RuntimeError(SECRET)
    result = await execute(tool, context, {"contact_id": "model-id"})
    assert result["status"] == "error"
    http.assert_not_called()
    assert SECRET not in caplog.text


@pytest.mark.asyncio
async def test_empty_metadata_still_owns_its_key(http):
    tool, context = tool_and_context("in", parameters=[{"name": "customer_tier"}], body_template='{"tier":"{customer_tier}"}')
    context.session_store = SimpleNamespace(get_by_call_id=AsyncMock(return_value=SimpleNamespace(
        pre_call_results={"customer_tier": "silver"}, call_metadata={"customer_tier": ""},
    )))
    await execute(tool, context, {"customer_tier": "model-override"})
    assert body_of(http) == {"tier": ""}


@pytest.mark.parametrize("phase", ["pre", "in", "post"])
@pytest.mark.asyncio
async def test_header_environment_values_are_literal_and_crlf_free(phase, http, monkeypatch):
    monkeypatch.setenv("HEADER_VALUE", "Bearer ${CANARY_SECRET}\r\nX-Injected: bad")
    tool, context = tool_and_context(phase, headers={"Authorization": "${HEADER_VALUE}"})
    await execute(tool, context)
    assert http.call_args.kwargs["headers"]["Authorization"] == "Bearer ${CANARY_SECRET}  X-Injected: bad"


@pytest.mark.asyncio
async def test_post_call_serialized_fields_and_custom_json_suffix(http):
    tool, context = tool_and_context("post", payload_template='{"transcript":{transcript_json},"summary":{summary_json},"ordinary":"{summary}","custom":"{custom_json}","metadata":{call_metadata_json}}')
    context.pre_call_results = {"custom_json": HOSTILE}
    context.call_metadata = {"customer_tier": HOSTILE}
    await execute(tool, context)
    assert body_of(http) == {
        "transcript": [{"role": "user", "content": HOSTILE}], "summary": HOSTILE,
        "ordinary": HOSTILE, "custom": HOSTILE, "metadata": {"customer_tier": HOSTILE},
    }


@pytest.mark.parametrize("value", [0, 1.25, True, False, None, ["a", HOSTILE], {"note": HOSTILE}])
@pytest.mark.asyncio
async def test_in_call_native_typed_values(http, value):
    tool, context = tool_and_context("in", parameters=[{"name": "value"}], body_template='{"value":{value}}')
    await execute(tool, context, {"value": value})
    assert body_of(http) == {"value": value}


@pytest.mark.parametrize("value", ['true', 'false', 'null', '123', '[1,"x"]', '{"a":1}'])
def test_serialized_values_and_quoted_text(value):
    assert json.loads(render_json('{"typed":{value},"string":"{value}"}', {"value": value})) == {"typed": json.loads(value), "string": value}


@pytest.mark.parametrize("phase", ["pre", "in", "post"])
@pytest.mark.parametrize("template", ['{"x":', '{"x":{caller_name}}', '{"x":"{missing}"}', '{"x": NaN}'])
@pytest.mark.asyncio
async def test_invalid_json_never_sends_raw_fallback(phase, template, http, caplog):
    key = "payload_template" if phase == "post" else "body_template"
    tool, context = tool_and_context(phase, **{key: template})
    await execute(tool, context)
    http.assert_not_called()
    assert SECRET not in caplog.text


@pytest.mark.parametrize("phase", ["pre", "in", "post"])
@pytest.mark.parametrize("template", ['null', 'false', '42', '"{caller_name}"', '["{caller_name}"]'])
@pytest.mark.asyncio
async def test_json_top_level_values(phase, template, http):
    key = "payload_template" if phase == "post" else "body_template"
    tool, context = tool_and_context(phase, **{key: template})
    await execute(tool, context)
    assert body_of(http) == json.loads(render_json(template, {"caller_name": HOSTILE}))


@pytest.mark.parametrize("phase", ["pre", "in", "post"])
@pytest.mark.parametrize("media_type,template", [
    ("text/plain", "Name: {caller_name}"),
    ("application/x-www-form-urlencoded", "name={caller_name}&fixed=ok"),
])
@pytest.mark.asyncio
async def test_explicit_non_json_formats(phase, media_type, template, http):
    key = "payload_template" if phase == "post" else "body_template"
    tool, context = tool_and_context(phase, headers={"Content-Type": media_type}, **{key: template})
    await execute(tool, context)
    data = http.call_args.kwargs["data"]
    if media_type == "text/plain":
        assert data == "Name: " + HOSTILE
    else:
        assert parse_qs(data) == {"name": [HOSTILE], "fixed": ["ok"]}


@pytest.mark.parametrize("phase", ["pre", "in", "post"])
@pytest.mark.parametrize("method", ["GET", "HEAD", "DELETE", "OPTIONS"])
@pytest.mark.asyncio
async def test_body_method_guard(phase, method, http):
    key = "payload_template" if phase == "post" else "body_template"
    tool, context = tool_and_context(phase, method=method, **{key: '{"id":"{call_id}"}'})
    await execute(tool, context)
    if method in {"GET", "HEAD"}:
        assert http.call_args.kwargs.get("data") is None
        assert "json" not in http.call_args.kwargs
    else:
        assert body_of(http) == {"id": "call-1"}


@pytest.mark.parametrize("phase", ["pre", "in", "post"])
@pytest.mark.parametrize("url,name", [("https://{caller_name}/", "evil.invalid"), ("https://example.invalid/{caller_name}/x", "..")])
@pytest.mark.asyncio
async def test_data_cannot_choose_authority_or_dot_path(phase, url, name, http):
    tool, context = tool_and_context(phase, url=url)
    context.caller_name = name
    await execute(tool, context)
    http.assert_not_called()


def test_json_escape_boundaries_and_environment(monkeypatch):
    monkeypatch.setenv("JSON_VALUE", HOSTILE)
    assert json.loads(render_json('{"env":"${JSON_VALUE}"}', {})) == {"env": HOSTILE}
    assert json.loads(render_json(r'{"x":"\\{x}"}', {"x": HOSTILE})) == {"x": "\\" + HOSTILE}
    with pytest.raises(TemplateError):
        render_json(r'{"x":"\{x}"}', {"x": HOSTILE})
    with pytest.raises(TemplateError):
        render_json('{"x":{x}}', {"x": '1,"injected":true'})
    with pytest.raises(TemplateError):
        render_body("<x>{x}</x>", {"x": HOSTILE}, {"Content-Type": "application/xml"})


@pytest.mark.parametrize("phase", ["pre", "in", "post"])
@pytest.mark.asyncio
async def test_call_data_cannot_downgrade_body_content_type(phase, http):
    key = "payload_template" if phase == "post" else "body_template"
    tool, context = tool_and_context(phase, headers={"Content-Type": "{caller_name}"}, **{key: '{"invalid":'})
    context.caller_name = "text/plain"
    await execute(tool, context)
    http.assert_not_called()


@pytest.mark.asyncio
async def test_post_call_concurrent_context_isolation(http):
    tool, context = tool_and_context("post", generate_summary=True, summary_provider="synthetic", payload_template='{"summary":{summary_json}}')
    context.summary = ""

    async def generate(**_kwargs):
        await asyncio.sleep(0)
        return SimpleNamespace(text=HOSTILE, provider="synthetic", model="fake", status="ok", duration_ms=0, error_code=None)

    context.summary_generator = generate
    sibling, _ = tool_and_context("post", payload_template='{"summary":{summary_json}}')
    await asyncio.gather(execute(tool, context), execute(sibling, context))
    assert context.summary == ""
    assert {json.loads(call.kwargs["data"])["summary"] for call in http.call_args_list} == {"", HOSTILE}
