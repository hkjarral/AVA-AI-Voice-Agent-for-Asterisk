"""Generic regression fixtures, including the body shape reported in #766."""
import json
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.tools.context import ToolExecutionContext, PreCallContext, PostCallContext
from src.tools.http.in_call_lookup import InCallHTTPConfig, InCallHTTPTool
from src.tools.http.template import TemplateError, render_json
from src.tools.http.template_contract import assert_valid_template, example_body, validate_template
from src.post_call_summary import SummaryGenerationResult


REPORTER_BODY = '''{
  "schema_version": 1,
  "event_type": "call_completed",
  "caller_number": "{caller_number}",
  "caller_name": "{caller_name}",
  "summary": {summary_json},
  "transcript": {transcript_json},
  "customer_name": "{customer_company}",
  "customer_cod_anagr": "{customer_company_cod}",
  "customer_contatto": "{customer_company_contatto}"
}'''


def context(generator=None):
    session = SimpleNamespace(conversation_history=[{"role": "user", "content": 'Support for "Example" please.'}],
                              pre_call_results={"customer_company": "Example", "customer_company_cod": "EX01", "customer_company_contatto": "Sample"},
                              call_metadata={"transcript_json": "malicious override"}, cleanup_in_progress=False, cleanup_completed=False)
    ctx = ToolExecutionContext(call_id="synthetic-766", session_store=SimpleNamespace(get_by_call_id=AsyncMock(return_value=session)),
                               tool_registry=SimpleNamespace(summary_generator=generator))
    return ctx, session


@pytest.mark.parametrize("phase", ["pre_call", "in_call", "post_call"])
@pytest.mark.parametrize("summary", [False, True])
def test_examples_use_available_variables_and_runtime_json_serializers(phase, summary):
    if phase == "pre_call" and summary:
        return
    key = "payload_template" if phase == "post_call" else "body_template"
    result = validate_template({"url": "https://example.invalid/hook", "method": "POST", "generate_summary": summary,
                                "summary_provider": "example", key: example_body(phase, summary)}, phase)
    assert result["valid"], result["issues"]
    json.loads(result["preview"]["body"])
    assert ("summary_json" in result["example_body"]) == (phase != "pre_call" and summary)


def test_unavailable_fields_block_but_dynamic_enrichment_remains_a_warning():
    result = validate_template({"url": "https://example.invalid", "method": "POST", "body_template": REPORTER_BODY,
                                "generate_summary": True, "summary_provider": "example"}, "in_call")
    assert result["valid"]
    assert {issue["variable"] for issue in result["issues"]} == {"customer_company", "customer_company_cod", "customer_company_contatto"}
    result = validate_template({"url": "https://example.invalid", "method": "POST", "body_template": '{"outcome":"{call_outcome}"}'}, "in_call")
    assert not result["valid"]
    assert result["issues"][0]["error_code"] == "unavailable_template_variable"
    assert not validate_template({"url": "https://example.invalid", "method": "POST", "body_template": '{"summary":{summary_json}}'}, "pre_call")["valid"]


def test_preview_never_reads_environment_or_interprets_substituted_tokens(monkeypatch):
    monkeypatch.setenv("API_SECRET", "never-preview-me")
    monkeypatch.setenv("API_ORIGIN", "https://private.example.invalid")
    review = validate_template({"url": "${API_ORIGIN}/hook", "method": "POST", "headers": {"Authorization": "Bearer ${API_SECRET}"},
                                "body_template": '{"secret":"${API_SECRET}","caller":"{caller_number}"}'}, "pre_call")
    assert review["valid"]
    assert "never-preview-me" not in json.dumps(review)
    assert "private.example" not in json.dumps(review)
    assert json.loads(render_json('{"text":"{value}"}', {"value": "${API_SECRET}"}))["text"] == "${API_SECRET}"


def test_bodyless_methods_ignore_stale_body_and_disabled_drafts_can_be_saved():
    config = {"url": "https://example.invalid", "method": "GET", "body_template": '{"bad":{call_outcome}}'}
    assert validate_template(config, "in_call")["valid"]
    assert validate_template(config, "in_call")["preview"]["body"] is None
    assert_valid_template({**config, "method": "POST", "enabled": False}, "in_call")
    with pytest.raises(TemplateError):
        assert_valid_template({**config, "method": "POST"}, "in_call")


def test_errors_preserve_safe_variable_names_but_never_values():
    with pytest.raises(TemplateError) as error:
        render_json('{"key":{missing}}', {})
    assert error.value.diagnostics() == {"error_code": "missing_template_variable", "template_variable": "missing"}
    with pytest.raises(TemplateError) as error:
        render_json('{"key":{value}}', {"value": "private unquoted value"})
    assert error.value.code == "invalid_template_value"
    assert "private" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("summary", [False, True])
async def test_reporter_shape_receives_a_protected_transcript_and_summary_from_one_snapshot(summary):
    captured = {}
    ctx, session = context()
    async def generate(**kwargs):
        captured.update(kwargs)
        session.conversation_history.append({"role": "user", "content": "Later turn"})
        return SummaryGenerationResult(text="Example recap", status="ok", provider="example")
    ctx.tool_registry.summary_generator = generate
    ctx.user_input = "Latest caller utterance"
    tool = InCallHTTPTool(InCallHTTPConfig(name="briefing", url="https://example.invalid", generate_summary=summary,
                                          summary_provider="example", body_template=REPORTER_BODY if summary else '{"transcript":{transcript_json}}',
                                          parameters=[{"name": "transcript_json", "type": "string"}]))
    tool._execute_request = AsyncMock(return_value={"status": "success"})
    result = await tool.execute({"transcript_json": "malicious argument"}, ctx)
    assert result["status"] == "success"
    values = tool._execute_request.await_args.args[2]
    transcript = json.loads(values["transcript_json"])
    assert transcript[-1]["content"] == "Latest caller utterance"
    assert len(transcript) == 2
    assert "transcript_json" not in [p.name for p in tool.definition.parameters]
    if summary:
        assert captured["conversation_history"][:-1] == transcript
        assert "transcript_json" not in json.loads(captured["conversation_history"][-1]["content"].split(": ", 1)[1])
        assert values["summary"] == "Example recap"
        assert result["summary_status"] == "ok"


@pytest.mark.asyncio
async def test_empty_history_is_an_array_and_recorded_latest_turn_is_not_duplicated():
    ctx, session = context()
    session.conversation_history = []
    tool = InCallHTTPTool(InCallHTTPConfig(name="empty", url="https://example.invalid", body_template='{"transcript":{transcript_json}}'))
    tool._execute_request = AsyncMock(return_value={"status": "success"})
    await tool.execute({}, ctx)
    assert json.loads(tool._execute_request.await_args.args[2]["transcript_json"]) == []
    session.conversation_history = [{"role": "user", "content": "Same turn"}]
    ctx.user_input, ctx.user_input_recorded = "Same turn", True
    await tool.execute({}, ctx)
    assert len(json.loads(tool._execute_request.await_args.args[2]["transcript_json"])) == 1


@pytest.mark.asyncio
async def test_missing_variable_skips_generation_and_retains_call_correlated_diagnostics(caplog):
    generator = AsyncMock()
    ctx, _ = context(generator)
    tool = InCallHTTPTool(InCallHTTPConfig(name="bad", url="https://example.invalid", generate_summary=True,
                                          summary_provider="example", body_template='{"missing":{not_available}}'))
    result = await tool.execute({}, ctx)
    assert result["delivery_status"] == "skipped" and result["summary_status"] == "skipped"
    assert result["error_code"] == "missing_template_variable"
    assert result["template_variable"] == "not_available"
    assert not generator.called
    assert caplog.records[-1].call_id == ctx.call_id


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["pre_call", "post_call"])
async def test_pre_and_post_call_template_failures_have_the_same_safe_diagnostics(phase, caplog):
    from src.tools.http.generic_lookup import GenericHTTPLookupTool, HTTPLookupConfig
    from src.tools.http.generic_webhook import GenericWebhookTool, WebhookConfig
    generator = AsyncMock()
    if phase == "pre_call":
        tool = GenericHTTPLookupTool(HTTPLookupConfig(name="bad", url="https://example.invalid", method="POST", body_template='{"value":{absent}}'))
        ctx = PreCallContext(call_id="synthetic-pre", caller_number="synthetic")
    else:
        tool = GenericWebhookTool(WebhookConfig(name="bad", url="https://example.invalid", generate_summary=True, summary_provider="example", payload_template='{"value":{absent}}'))
        ctx = PostCallContext(call_id="synthetic-post", caller_number="synthetic", summary_generator=generator)
    await tool.execute(ctx)
    record = tool.get_last_result(ctx.call_id)
    assert record["error_code"] == "missing_template_variable"
    assert record["template_variable"] == "absent" and record["delivery_status"] == "skipped"
    assert record["phase"] == phase
    assert not generator.called
    assert ctx.call_id in caplog.records[-1].getMessage()


@pytest.mark.asyncio
async def test_transcripts_are_isolated_per_call_and_cleanup_blocks_delivery_without_a_summary():
    first, one = context()
    second, two = context()
    first.call_id, second.call_id = "first", "second"
    one.conversation_history = [{"role": "user", "content": "First caller"}]
    two.conversation_history = [{"role": "user", "content": "Second caller"}]
    captured = {}
    async def deliver(_params, ctx, values):
        await asyncio.sleep(0)
        captured[ctx.call_id] = json.loads(values["transcript_json"])
        return {"status": "success"}
    tool = InCallHTTPTool(InCallHTTPConfig(name="parallel", url="https://example.invalid", body_template='{"transcript":{transcript_json}}'))
    tool._execute_request = AsyncMock(side_effect=deliver)
    await asyncio.gather(tool.execute({}, first), tool.execute({}, second))
    assert captured["first"] == one.conversation_history
    assert captured["second"] == two.conversation_history
    one.cleanup_in_progress = True
    assert (await tool.execute({}, first))["delivery_status"] == "skipped"
    assert tool._execute_request.await_count == 2


def test_registry_activation_rejects_bad_templates_without_removing_an_existing_generation():
    from src.tools.registry import ToolRegistry
    existing = ToolRegistry.isolated()
    existing.initialize_in_call_http_tools_from_config({"working": {"url": "https://example.invalid", "kind": "in_call_http_lookup"}})
    candidate = ToolRegistry.isolated()
    with pytest.raises(TemplateError):
        candidate.initialize_http_tools_from_config({"broken": {"kind": "generic_webhook", "url": "https://example.invalid", "payload_template": '{"oops":'}})
    assert existing.get("working") is not None
    assert candidate.get("broken") is None


def test_http_highlight_offsets_match_utf16_editor_after_emoji():
    review = validate_template({"url": "https://example.invalid", "method": "POST", "body_template": '{"text":"😀 {caller_name}"}'}, "in_call")
    reference = next(item for item in review['references'] if item['name'] == 'caller_name')
    assert reference['start'] == 12


def test_repeated_environment_reference_preserves_synthetic_origin():
    review = validate_template({
        "url": "${API_BASE}/hook?origin=${API_BASE}", "method": "POST",
        "headers": {"X-Origin": "${API_BASE}"}, "query_params": {"origin": "${API_BASE}"},
        "body_template": '{"origin":"${API_BASE}"}',
    }, "in_call")
    assert review["valid"], review["issues"]
    assert review["preview"]["url"].startswith("https://example.invalid/hook")


@pytest.mark.parametrize("headers,content_type,valid", [
    ({}, None, False),
    ({"Content-Type": "application/json"}, None, False),
    ({"content-type": "application/problem+json; charset=utf-8"}, None, False),
    ({"Content-Type": "${BODY_TYPE}"}, None, False),
    ({"Authorization": "Bearer ${SECRET}"}, "text/plain", True),
    ({"Content-Type": "text/plain"}, None, True),
    ({"Content-Type": "application/x-www-form-urlencoded"}, None, True),
])
def test_pre_call_custom_json_variables_are_unavailable(headers, content_type, valid):
    review = validate_template({"url": "https://example.invalid", "method": "POST", "headers": headers,
                                "content_type": content_type, "body_template": '{"custom":"{crm_company}"}'}, "pre_call")
    assert review["valid"] is valid
    issue = next(item for item in review["issues"] if item.get("variable") == "crm_company")
    assert issue["severity"] == ("warning" if valid else "error")


def test_preview_does_not_serialize_template_exception_arguments(monkeypatch):
    from src.tools.http import template_contract
    def fail(*args, **kwargs):
        raise TemplateError("synthetic-private-value", code="invalid_template_value", variable="example")
    monkeypatch.setattr(template_contract, "render_request", fail)
    review = validate_template({"url": "https://example.invalid"}, "pre_call")
    assert not review["valid"]
    assert "synthetic-private-value" not in json.dumps(review)
    assert review["issues"][-1]["error_code"] == "invalid_template_value"
    assert review["issues"][-1]["template_variable"] == "example"
