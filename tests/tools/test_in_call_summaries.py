import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.post_call_summary import SummaryGenerationResult
from src.tools.context import ToolExecutionContext
from src.tools.http.in_call_lookup import InCallHTTPConfig, InCallHTTPTool
from src.tools.summary import generate_summary, summary_prompt, snapshot_history


def make_tool(**kw):
    return InCallHTTPTool(InCallHTTPConfig(name="brief", url="https://example.com", generate_summary=True,
        summary_provider="mock_llm", **kw))


def make_context(generator=None, call_id="synthetic"):
    session = SimpleNamespace(conversation_history=[{"role": "user", "content": "Earlier request"}],
        pre_call_results={"company": "Example"}, call_metadata={"customer_name": "Corrected", "summary": "poison"},
        cleanup_in_progress=False, cleanup_completed=False)
    return ToolExecutionContext(call_id=call_id, user_input="Most recent caller words",
        session_store=SimpleNamespace(get_by_call_id=AsyncMock(return_value=session)),
        tool_registry=SimpleNamespace(summary_generator=generator)), session


@pytest.mark.asyncio
async def test_summary_snapshot_safe_json_and_combined_budget():
    generator = AsyncMock(return_value=SummaryGenerationResult(text='A "quote" and \n newline', provider="mock_llm", status="ok"))
    context, session = make_context(generator)
    tool = make_tool(parameters=[{"name": "summary"}, {"name": "summary_status"}])
    observed = []
    async def request(params, ctx, values):
        observed.append(values)
        assert json.loads(values["summary_json"]) == values["summary"]
        return {"status": "success"}
    tool._execute_request = request
    result = await tool.execute({"summary": "injected", "summary_status": "fake"}, context)
    args = generator.await_args.kwargs
    assert args["conversation_history"][-2]["content"] == "Most recent caller words"
    assert "Corrected" in args["conversation_history"][-1]["content"]
    assert observed[0]["summary"] != "injected" and observed[0]["summary_status"] == "ok"
    assert result["summary_status"] == "ok" and result["internal_notification"]
    assert tool.definition.timeout_ms == 20000
    assert len(session.conversation_history) == 1 and not hasattr(session, "summary")


@pytest.mark.asyncio
@pytest.mark.parametrize("policy,sent", [("continue",True),("abort",False)])
async def test_summary_failure_is_explicit_and_operator_controls_delivery(policy, sent):
    context, _ = make_context()
    tool = make_tool(summary_failure_policy=policy)
    tool._execute_request = AsyncMock(return_value={"status":"success"})
    result = await tool.execute({}, context)
    assert tool._execute_request.await_count == int(sent)
    assert result["summary_error_code"] == "generator_unavailable"
    if sent:
        values=tool._execute_request.await_args.args[2]
        assert values["summary"] == "" and values["summary_status"] == "error"
    else:
        assert result["delivery_status"] == "skipped"


@pytest.mark.asyncio
async def test_hangup_during_summary_prevents_delivery_and_snapshot_does_not_change():
    context, session = make_context()
    async def generate(**kwargs):
        session.conversation_history[0]["content"] = "mutated"
        assert kwargs["conversation_history"][0]["content"] == "Earlier request"
        session.cleanup_in_progress = True
        return SummaryGenerationResult(text="Recap", provider="mock_llm", status="ok")
    context.tool_registry.summary_generator=generate
    tool=make_tool();tool._execute_request=AsyncMock()
    result=await tool.execute({},context)
    assert result["status"] == "cancelled" and not tool._execute_request.called


@pytest.mark.asyncio
async def test_concurrent_executions_have_distinct_jobs_and_results():
    ids=[]
    async def generate(**kwargs):
        ids.append(kwargs["call_id"])
        await asyncio.sleep(0)
        return SummaryGenerationResult(text=kwargs["conversation_history"][0]["content"],provider="mock_llm",status="ok")
    a,_=make_context(generate,"A"); b,s=make_context(generate,"B");s.conversation_history[0]["content"]="Different caller"
    tool=make_tool(); values=[]
    async def request(params,ctx,sub): values.append((ctx.call_id,sub["summary"]));return {"status":"success"}
    tool._execute_request=request
    await asyncio.gather(tool.execute({},a),tool.execute({},b),tool.execute({},a))
    assert len(set(ids))==3
    assert ("B","Different caller") in values and values.count(("A","Earlier request"))==2


@pytest.mark.asyncio
async def test_cancelled_summary_closes_work_without_request():
    started=asyncio.Event();closed=asyncio.Event()
    async def generate(**kwargs):
        started.set()
        try: await asyncio.Event().wait()
        finally: closed.set()
    ctx,_=make_context(generate);tool=make_tool();tool._execute_request=AsyncMock()
    task=asyncio.create_task(tool.execute({},ctx));await started.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert closed.is_set() and not tool._execute_request.called


@pytest.mark.asyncio
async def test_timeout_and_empty_history_are_safe():
    async def slow(**kwargs): await asyncio.sleep(10)
    tool=make_tool(summary_timeout_ms=1)
    values,diag=await generate_summary(tool.config,slow,call_id="test",history=[],values={})
    assert values["summary_status"] == "timeout" and diag["summary_error_code"]=="timeout"
    generator=AsyncMock(return_value=SummaryGenerationResult(status="skipped",error_code="empty_transcript"))
    await generate_summary(tool.config,generator,call_id="test",history=[],values={"company":"Example"})
    assert generator.await_args.kwargs["conversation_history"]==[]


@pytest.mark.parametrize("bad", ["{other}","{max_words!r}","{max_words:>10000000}","{max_words.real}"])
def test_prompt_rejects_unsupported_formatting(bad):
    with pytest.raises(ValueError):summary_prompt(bad,100)

@pytest.mark.asyncio
async def test_summary_disabled_preserves_legacy_summary_parameter():
    context,_=make_context()
    context.session_store=None
    tool=InCallHTTPTool(InCallHTTPConfig(name='legacy',url='https://example.com',parameters=[{'name':'summary','type':'string','required':True}]))
    tool._execute_request=AsyncMock(return_value={'status':'success'})
    result=await tool.execute({'summary':'Legacy model-provided note'},context)
    assert result=={'status':'success', 'http_tool': True, 'phase': 'in_call'}
    assert tool._execute_request.await_args.args[2]['summary']=='Legacy model-provided note'
    assert [p.name for p in tool.definition.parameters]==['summary']
    enabled=make_tool(parameters=[{'name':'summary','type':'string','required':True}])
    assert enabled.definition.parameters==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('before_request,expected', [(True,'skipped'),(False,'uncertain')])
async def test_combined_timeout_reports_whether_delivery_started(before_request,expected):
    context,_=make_context();context.session_store=None
    notify=InCallHTTPTool(InCallHTTPConfig(name='timeout',url='https://example.com',timeout_ms=1))
    async def blocked(*args):await asyncio.Event().wait()
    if before_request:notify._build_substitution_context=blocked
    else:notify._execute_request=blocked
    result=await notify.execute({},context)
    assert result['status']=='timeout' and result['delivery_status']==expected


@pytest.mark.asyncio
@pytest.mark.parametrize("recorded", [True, False])
async def test_caller_turn_identity_survives_following_assistant_response(recorded):
    context, session = make_context()
    context.user_input = "Please help"
    context.user_input_recorded = recorded
    # The same words may be a recorded current turn or a genuinely new utterance.
    session.conversation_history = [{"role": "user", "content": "Please help"}, {"role": "assistant", "content": "I can help"}]
    history = await snapshot_history(context)
    assert history.count({"role": "user", "content": "Please help"}) == (1 if recorded else 2)
    assert len(session.conversation_history) == 2
    history[0]["content"] = "snapshot mutation"
    assert session.conversation_history[0]["content"] == "Please help"


@pytest.mark.asyncio
async def test_summary_generator_gets_recorded_pipeline_caller_once():
    generator = AsyncMock(return_value=SummaryGenerationResult(text="Factual recap", provider="mock_llm", status="ok"))
    context, session = make_context(generator)
    session.conversation_history.extend([{"role": "user", "content": context.user_input}, {"role": "assistant", "content": "Checking"}])
    context.user_input_recorded = True
    notify = make_tool()
    notify._execute_request = AsyncMock(return_value={"status": "success"})
    await notify.execute({}, context)
    snapshot = generator.await_args.kwargs["conversation_history"]
    assert sum(row.get("content") == context.user_input for row in snapshot) == 1
    assert session.conversation_history[-1]["role"] == "assistant"


@pytest.mark.asyncio
@pytest.mark.parametrize("http_status,body,headers,delivery,processing", [
    (200, b"{}", {}, "accepted", "success"),
    (400, b"{}", {}, "rejected", "failed"),
    (429, b"{}", {}, "rejected", "failed"),
    (408, b"{}", {}, "uncertain", "failed"),
    (500, b"{}", {}, "uncertain", "failed"),
    (200, b"invalid-json", {}, "accepted", "error"),
    (200, b"{}", {"Content-Length": "999999"}, "accepted", "error"),
])
async def test_http_delivery_is_independent_of_response_processing_and_persisted(monkeypatch, http_status, body, headers, delivery, processing):
    from src.tools.execution_history import build_in_call_tool_record
    async def chunks(_size):
        yield body
    response = SimpleNamespace(status=http_status, headers=headers, charset="utf-8", content=SimpleNamespace(iter_chunked=chunks))
    request_cm = MagicMock(__aenter__=AsyncMock(return_value=response), __aexit__=AsyncMock(return_value=None))
    session = SimpleNamespace(request=MagicMock(return_value=request_cm))
    session_cm = MagicMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=None))
    monkeypatch.setattr("aiohttp.ClientSession", lambda **_kwargs: session_cm)
    generator = AsyncMock(return_value=SummaryGenerationResult(text="Factual recap", provider="mock_llm", status="ok"))
    context, _ = make_context(generator)
    result = await make_tool().execute({}, context)
    assert result["status"] == processing
    assert result["delivery_status"] == delivery and result["http_status"] == http_status
    record = build_in_call_tool_record(call_id=context.call_id, tool_call_id="synthetic-delivery", tool_name="brief", parameters={}, result=result)
    assert record["delivery_status"] == delivery and record["summary_status"] == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("network", [True, False])
async def test_http_network_failure_is_uncertain_but_invalid_template_is_skipped(monkeypatch, network):
    import aiohttp
    request_cm = MagicMock(__aenter__=AsyncMock(side_effect=aiohttp.ClientConnectionError("synthetic")), __aexit__=AsyncMock(return_value=None))
    session = SimpleNamespace(request=MagicMock(return_value=request_cm))
    session_cm = MagicMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=None))
    client = MagicMock(return_value=session_cm)
    monkeypatch.setattr("aiohttp.ClientSession", client)
    generator = AsyncMock(return_value=SummaryGenerationResult(text="Factual recap", provider="mock_llm", status="ok"))
    context, _ = make_context(generator)
    config = {} if network else {"headers": {"Content-Type": "application/json"}, "body_template": '{"broken":'}
    result = await make_tool(**config).execute({}, context)
    assert result["delivery_status"] == ("uncertain" if network else "skipped")
    assert result["summary_status"] == ("ok" if network else "skipped") and client.called == network
    assert generator.called == network


@pytest.mark.asyncio
async def test_combined_deadline_preserves_known_http_status_without_cross_call_state(monkeypatch):
    from src.tools.execution_history import build_in_call_tool_record
    async def stalled_chunks(_size):
        await asyncio.Event().wait()
        yield b"{}"
    response = SimpleNamespace(status=200, headers={}, charset="utf-8", content=SimpleNamespace(iter_chunked=stalled_chunks))
    async def awaiting_headers():
        await asyncio.Event().wait()
    accepted = MagicMock(__aenter__=AsyncMock(return_value=response), __aexit__=AsyncMock(return_value=None))
    pending = MagicMock(__aenter__=AsyncMock(side_effect=awaiting_headers), __aexit__=AsyncMock(return_value=None))
    def request(**kwargs):
        return accepted if kwargs["url"].endswith("accepted") else pending
    session = SimpleNamespace(request=request)
    session_cm = MagicMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=None))
    monkeypatch.setattr("aiohttp.ClientSession", lambda **_kwargs: session_cm)
    generator = AsyncMock(return_value=SummaryGenerationResult(text="Recap", provider="mock_llm", status="ok"))
    first, _ = make_context(generator, "accepted")
    second, _ = make_context(generator, "pending")
    notify = make_tool()
    notify.config.url = "https://example.com/{call_id}"
    notify._definition.timeout_ms = 100
    a, b = await asyncio.gather(notify.execute({}, first), notify.execute({}, second))
    assert a["status"] == b["status"] == "timeout"
    assert a["delivery_status"] == "accepted" and a["http_status"] == 200
    assert b["delivery_status"] == "uncertain" and "http_status" not in b
    record = build_in_call_tool_record(call_id="accepted", tool_call_id="deadline", tool_name="brief", parameters={}, result=a)
    assert record["delivery_status"] == "accepted" and record["summary_status"] == "ok"
