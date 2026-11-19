"""Synthetic managed Pushover lifecycle and transport checks; no real sends."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from pydantic import ValidationError
from src.integrations.pushover import PushoverToolConfig, PushoverSender, render_message
from src.tools.pushover import PushoverTool, NotificationState, notification_values
from src.tools.context import ToolExecutionContext, PostCallContext, PreCallContext
from src.tools.base import ToolPhase
from src.tools.runtime_config import ToolRuntimeGeneration, ToolConfigPolicyError
from src.post_call_summary import SummaryGenerationResult


def contexts():
    session = SimpleNamespace(
        conversation_history=[{"role": "user", "content": "Request a consultation"}],
        pre_call_results={"customer_name": "CRM Person", "company": "Example"},
        call_metadata={
            "customer_name": "Corrected",
            "customer_name_source": "caller_confirmed",
        },
        tool_calls=[],
        cleanup_in_progress=False,
        cleanup_completed=False,
        notification_state=NotificationState(),
    )
    live = ToolExecutionContext(
        call_id="synthetic",
        context_name="sales",
        caller_number="+15550102030",
        caller_name="Caller ID",
        session_store=SimpleNamespace(get_by_call_id=AsyncMock(return_value=session)),
        config={
            "tools": {
                "transfer": {
                    "destinations": {"support": {"description": "Support team"}}
                }
            }
        },
    )
    post = PostCallContext(
        call_id="synthetic",
        context_name="sales",
        caller_number="+15550102030",
        conversation_history=session.conversation_history,
        tool_calls=session.tool_calls,
        notification_state=session.notification_state,
    )
    return live, post, session


def configured(phase="in_call", **kwargs):
    return PushoverToolConfig(
        phase=phase, token_file="app-file", key_file="staff-file", **kwargs
    )


def tool(phase="in_call", cfg=None, sender=None):
    return PushoverTool(
        "notify_" + phase,
        (cfg or configured(phase)).model_dump(),
        sender=sender
        or SimpleNamespace(
            send=AsyncMock(return_value={"delivery_status": "accepted"})
        ),
    )


@pytest.mark.parametrize(
    "change",
    [
        {"priority": 2},
        {"token": "secret"},
        {"body": "{__class__}"},
        {"generate_summary": True},
        {"phase": "pre_call", "generate_summary": True, "summary_provider": "x_llm"},
    ],
)
def test_configuration_fails_closed(change):
    with pytest.raises(ValidationError):
        PushoverToolConfig.model_validate({"phase": "in_call", **change})


def test_named_tools_use_existing_phase_selection_and_global_optouts():
    cfg = {
        "tools": {
            "arrival": configured("pre_call").model_dump(),
            "finish": configured("post_call", is_global=True).model_dump(),
        },
        "in_call_tools": {
            "sales_notice": configured(
                description="Use before a Sales handoff."
            ).model_dump(),
            "booking_notice": configured(
                description="Use after a booking."
            ).model_dump(),
        },
    }
    generation = ToolRuntimeGeneration.build(generation_id=1, config=cfg)
    registry = generation.registry
    assert (
        registry.get("sales_notice").definition.description
        == "Use before a Sales handoff."
    )
    assert registry.get("sales_notice").definition.parameters == []
    assert registry.get_tools_for_context(ToolPhase.IN_CALL, [], []) == []
    assert [
        t.definition.name
        for t in registry.get_tools_for_context(
            ToolPhase.IN_CALL, ["booking_notice"], []
        )
    ] == ["booking_notice"]
    assert [
        t.definition.name
        for t in registry.get_tools_for_context(ToolPhase.POST_CALL, [], [])
    ] == ["finish"]
    assert registry.get_tools_for_context(ToolPhase.POST_CALL, [], ["finish"]) == []
    cfg["in_call_tools"]["sales_notice"]["description"] = "new instruction"
    assert registry.get("sales_notice").definition.description != "new instruction"
    with pytest.raises(ToolConfigPolicyError):
        ToolRuntimeGeneration.build(
            generation_id=2, config={"tools": {"invalid": configured().model_dump()}}
        )


@pytest.mark.asyncio
async def test_summary_dedup_and_isolation_use_call_state_not_shared_tool_instance():
    notify = tool()
    notify.config.generate_summary = True
    notify.config.summary_provider = "test_llm"
    generator = AsyncMock(
        return_value=SummaryGenerationResult(
            text="Factual recap", provider="test_llm", status="ok"
        )
    )
    live, _, session = contexts()
    live.tool_registry = SimpleNamespace(summary_generator=generator)
    first = await notify.execute({}, live)
    second = await notify.execute({}, live)
    assert first["delivery_status"] == "accepted" and second["deduplicated"]
    assert generator.await_count == 1 and notify.sender.send.await_count == 1
    assert "Factual recap" in notify.sender.send.await_args.kwargs["message"]["message"]
    session.conversation_history.extend(
        [
            {"role": "assistant", "content": "Okay"},
            {"role": "user", "content": "Request a consultation"},
        ]
    )
    await notify.execute({}, live)
    other, _, _ = contexts()
    other.call_id = "other"
    other.tool_registry = live.tool_registry
    await notify.execute({}, other)
    assert notify.sender.send.await_count == 3
    assert (await notify.execute({"recipient": "attacker"}, live))[
        "error_code"
    ] == "unexpected_parameters"


@pytest.mark.asyncio
@pytest.mark.parametrize("policy,sends", [("continue", 1), ("abort", 0)])
async def test_summary_failures_cleanup_and_disabled_tool_fail_closed(policy, sends):
    notify = tool(
        cfg=configured(
            generate_summary=True,
            summary_provider="test_llm",
            summary_failure_policy=policy,
        )
    )
    live, _, session = contexts()
    live.tool_registry = SimpleNamespace(
        summary_generator=AsyncMock(side_effect=RuntimeError("private failure"))
    )
    result = await notify.execute({}, live)
    assert (
        notify.sender.send.await_count == sends and result["summary_status"] == "error"
    )
    assert "private failure" not in str(result)
    if sends:
        assert notify.sender.send.await_args.kwargs["message"]["message"].endswith(
            "Summary: unavailable"
        )
    session.cleanup_in_progress = True
    assert (await notify.execute({}, live))["delivery_status"] == "skipped"
    notify.config.enabled = False
    assert (await notify.execute({}, live))["delivery_status"] == "skipped"


@pytest.mark.asyncio
async def test_uncertain_send_is_reserved_on_cancellation():
    entered = asyncio.Event()

    async def send(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    notify = tool(sender=SimpleNamespace(send=send))
    live, _, _ = contexts()
    task = asyncio.create_task(notify.execute({}, live))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    result = await notify.execute({}, live)
    assert result["deduplicated"] and result["delivery_status"] == "uncertain"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome,attempts,destination,suppressed",
    [
        ("transferred", 1, "support", True),
        ("caller_hangup", 1, "support", False),
        ("transferred", 2, "support", False),
        ("transferred", 1, "other", False),
    ],
)
async def test_optional_suppression_requires_actual_matching_transfer_and_accepted_tool(
    tmp_path, outcome, attempts, destination, suppressed
):
    key = tmp_path / "key"
    key.write_text("a" * 30)
    live, post, session = contexts()
    briefing = tool(
        cfg=PushoverToolConfig(
            phase="in_call",
            token_file=str(key),
            key_file=str(key),
            destination_key="support",
        )
    )
    await briefing.execute({}, live)
    post.call_outcome = outcome
    post.transfer_destination = destination
    post.tool_calls.extend({"name": "blind_transfer"} for _ in range(attempts))
    finish = tool(
        "post_call",
        cfg=PushoverToolConfig(
            phase="post_call",
            token_file=str(key),
            key_file=str(key),
            suppress_after_tool="notify_in_call",
        ),
    )
    result = await finish.execute(post)
    assert (result["delivery_status"] == "suppressed") == suppressed
    assert finish.sender.send.await_count == (0 if suppressed else 1)


def test_render_preserves_headers_and_substitutes_once():
    cfg = configured(
        title="{{AVA}} {name}", body="Call: {call_id}\n{summary}\nNext: {destination}"
    )
    message = render_message(
        cfg,
        "in_call",
        {
            "name": "{summary}",
            "summary": "界" * 3000,
            "call_id": "synthetic",
            "destination": "support",
        },
    )
    assert message["title"] == "{AVA} {summary}"
    assert len(message["message"]) <= 1024 and message["message"].endswith(
        "Next: support"
    )


def test_identity_precedence_uses_source_and_unknown_values_truthfully():
    context = PreCallContext(
        call_id="synthetic", caller_number="123", caller_name="Unknown"
    )
    values = notification_values(context, phase="pre_call")
    assert values["name"] == "unknown" and values["crm_status"] == "unknown"
    values = notification_values(
        context,
        phase="pre_call",
        enrichment={"customer_name": "CRM"},
        metadata={
            "customer_name": "Correction",
            "customer_name_source": "caller_confirmed",
        },
    )
    assert (
        values["name"] == "Correction"
        and values["identity_source"] == "caller-confirmed"
    )
    values = notification_values(
        context,
        phase="pre_call",
        enrichment={"customer_name": "CRM"},
        metadata={"customer_name": "Seeded"},
    )
    assert values["name"] == "CRM"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,payload,expected",
    [
        (200, {"status": 1}, "accepted"),
        (400, {"status": 0, "errors": ["secret-echo"]}, "rejected"),
        (429, {"status": 0}, "rejected"),
        (500, {}, "uncertain"),
        (302, {}, "uncertain"),
        (200, {"status": 0}, "rejected"),
        (200, "malformed", "uncertain"),
    ],
)
async def test_sender_truthful_status_redaction_and_fixed_endpoint(
    tmp_path, status, payload, expected
):
    token = tmp_path / "app"
    token.write_text("a" * 30)
    user = tmp_path / "user"
    user.write_text("b" * 30)
    seen = []

    async def handle(request):
        seen.append(request)
        return httpx.Response(
            status,
            content=(
                b"not-json" if payload == "malformed" else json.dumps(payload).encode()
            ),
        )

    sender = PushoverSender(
        lock_dir=tmp_path / "locks",
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handle), **kwargs
        ),
    )
    result = await sender.send(
        token_file=str(token), key_file=str(user), message={"message": "synthetic"}
    )
    assert result["delivery_status"] == expected
    assert str(seen[0].url) == "https://api.pushover.net/1/messages.json"
    assert (
        "secret-echo" not in str(result)
        and "a" * 30 not in str(result)
        and len(seen) == 1
    )


@pytest.mark.asyncio
async def test_sender_caps_connections_across_independent_process_clients_and_releases(
    tmp_path,
):
    token = tmp_path / "key"
    token.write_text("a" * 30)
    active = 0
    maximum = 0

    async def handle(request):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(0.025)
            return httpx.Response(200, json={"status": 1})
        finally:
            active -= 1

    factory = lambda **kw: httpx.AsyncClient(
        transport=httpx.MockTransport(handle), **kw
    )
    senders = [
        PushoverSender(lock_dir=tmp_path / "locks", client_factory=factory)
        for _ in range(2)
    ]
    results = await asyncio.gather(
        *(
            senders[i % 2].send(
                token_file=str(token),
                key_file=str(token),
                message={"message": "synthetic"},
            )
            for i in range(8)
        )
    )
    assert maximum == 2 and all(r["delivery_status"] == "accepted" for r in results)
    assert all(s._pending == 0 for s in senders)


@pytest.mark.asyncio
async def test_validation_does_not_send_message_and_oversized_reply_is_bounded(
    tmp_path,
):
    token = tmp_path / "key"
    token.write_text("a" * 30)
    seen = []

    async def handle(request):
        seen.append(request)
        return httpx.Response(200, content=b"x" * 20000)

    sender = PushoverSender(
        lock_dir=tmp_path / "locks",
        client_factory=lambda **kw: httpx.AsyncClient(
            transport=httpx.MockTransport(handle), **kw
        ),
    )
    result = await sender.send(
        token_file=str(token), key_file=str(token), validate=True
    )
    assert result["error_code"] == "oversized_response"
    assert (
        seen[0].url.path == "/1/users/validate.json"
        and b"message=" not in seen[0].content
    )


@pytest.mark.asyncio
async def test_arrival_waits_for_enrichment_and_diagnostics_do_not_become_prompt_vars():
    from src.engine import Engine
    from src.core.models import CallSession

    engine = Engine.__new__(Engine)
    session = CallSession(
        call_id="synthetic-arrival",
        caller_channel_id="synthetic-arrival",
        context_name="sales",
    )
    order = []

    async def enrich(context):
        await asyncio.sleep(0)
        order.append("enrichment")
        return {"customer_name": "Synthetic CRM", "company": "Example"}

    enrichment = SimpleNamespace(
        definition=SimpleNamespace(
            name="lookup",
            timeout_ms=1000,
            hold_audio_file=None,
            hold_audio_threshold_ms=500,
        ),
        execute=enrich,
    )

    async def send(**kwargs):
        assert order == ["enrichment"]
        assert "Synthetic CRM" in kwargs["message"]["message"]
        order.append("arrival")
        return {"delivery_status": "accepted"}

    arrival = tool(
        "pre_call",
        cfg=configured(
            "pre_call",
            body="{name} | {company}\nCaller: {caller_number}\nCall: {call_id}",
        ),
        sender=SimpleNamespace(send=send),
    )
    session.tool_runtime_registry = SimpleNamespace(
        get_tools_for_context=lambda **kwargs: [arrival, enrichment]
    )
    session.tool_runtime_config = {"tools": {}}
    engine.transport_orchestrator = SimpleNamespace(
        get_context_config=lambda *args: SimpleNamespace(
            pre_call_tools=[], disable_global_pre_call_tools=[]
        )
    )
    engine.config = SimpleNamespace()
    engine.ari_client = None
    engine._save_session = AsyncMock()
    result = await engine._execute_pre_call_tools(session.call_id, session)
    assert order == ["enrichment", "arrival"]
    assert result == {"customer_name": "Synthetic CRM", "company": "Example"}
    record = next(
        item
        for item in session.pre_call_tool_calls
        if item["name"] == "notify_pre_call"
    )
    assert record["delivery_status"] == "accepted"


@pytest.mark.asyncio
async def test_sender_waiter_capacity_and_timeout_do_not_send(tmp_path):
    sender = PushoverSender(lock_dir=tmp_path / "locks")
    sender._pending = 18
    result = await sender.send(token_file="unused", key_file="unused")
    assert result["delivery_status"] == "capacity" and sender._pending == 18


@pytest.mark.asyncio
async def test_sender_supports_backend_python_without_asyncio_timeout(
    tmp_path, monkeypatch
):
    monkeypatch.delattr(asyncio, "timeout", raising=False)
    token = tmp_path / "token"
    recipient = tmp_path / "recipient"
    token.write_text("a" * 30)
    recipient.write_text("b" * 30)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"status": 1})
    )
    sender = PushoverSender(
        lock_dir=tmp_path / "locks",
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=transport, **kwargs
        ),
    )
    result = await sender.send(
        token_file=str(token), key_file=str(recipient), message={"message": "Synthetic"}
    )
    assert result["delivery_status"] == "accepted"
    assert sender._pending == 0


def test_native_tool_cannot_shadow_builtin_during_generation():
    with pytest.raises(ToolConfigPolicyError):
        ToolRuntimeGeneration.build(
            generation_id=1,
            config={"in_call_tools": {"hangup_call": configured().model_dump()}},
        )


@pytest.mark.asyncio
async def test_multiple_recipients_share_summary_and_disabled_rows_never_send(tmp_path):
    app = tmp_path / "app"
    app.write_text("a" * 30)
    user = tmp_path / "user"
    user.write_text("b" * 30)
    group = tmp_path / "group"
    group.write_text("c" * 30)
    cfg = PushoverToolConfig(
        phase="in_call",
        token_file=str(app),
        recipients={
            "alice": {"label": "Alice", "key_file": str(user)},
            "team": {"label": "Team group", "key_file": str(group)},
            "off": {"label": "Disabled", "enabled": False, "key_file": "never-read"},
            "alias": {"label": "Same Alice", "key_file": str(user)},
        },
        generate_summary=True,
        summary_provider="test_llm",
    )
    generator = AsyncMock(
        return_value=SummaryGenerationResult(
            text="One shared recap", provider="test_llm", status="ok"
        )
    )
    sender = SimpleNamespace(
        send=AsyncMock(
            side_effect=[
                {"delivery_status": "accepted"},
                {"delivery_status": "rejected", "error_code": "api_rejected"},
            ]
        )
    )
    notify = tool(cfg=cfg, sender=sender)
    live, _, _ = contexts()
    live.tool_registry = SimpleNamespace(summary_generator=generator)
    result = await notify.execute({}, live)
    assert generator.await_count == 1 and sender.send.await_count == 2
    assert all(
        call.kwargs["message"]["message"].count("One shared recap") == 1
        for call in sender.send.await_args_list
    )
    assert result["delivery_status"] == "partial"
    rows = result["notification_deliveries"]
    assert (
        rows["off"]["delivery_status"] == "disabled" and rows["alias"]["deduplicated"]
    )
    assert (
        rows["alice"]["delivery_status"] == "accepted"
        and rows["team"]["delivery_status"] == "rejected"
    )
    assert str(user) not in str(result) and "b" * 30 not in str(result)
    await notify.execute({}, live)
    assert generator.await_count == 1 and sender.send.await_count == 2


@pytest.mark.asyncio
async def test_all_disabled_or_explicitly_removed_recipients_skip_summary_and_delivery():
    for recipients in ({"off": {"enabled": False}}, {}):
        notify = tool(
            cfg=PushoverToolConfig(
                phase="in_call",
                key_file="legacy-not-reactivated",
                recipients=recipients,
                generate_summary=True,
                summary_provider="test_llm",
            )
        )
        live, _, _ = contexts()
        generator = AsyncMock()
        live.tool_registry = SimpleNamespace(summary_generator=generator)
        result = await notify.execute({}, live)
        assert result["delivery_status"] == "skipped"
        assert not generator.called and not notify.sender.send.called


@pytest.mark.asyncio
async def test_concurrent_named_post_tools_do_not_hold_call_lock_during_network():
    both = asyncio.Event()
    arrivals = []

    async def send(**kwargs):
        arrivals.append(kwargs)
        if len(arrivals) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 1)
        return {"delivery_status": "accepted"}

    _, post, _ = contexts()
    one = PushoverTool(
        "one", configured("post_call").model_dump(), sender=SimpleNamespace(send=send)
    )
    two = PushoverTool(
        "two", configured("post_call").model_dump(), sender=SimpleNamespace(send=send)
    )
    results = await asyncio.gather(one.execute(post), two.execute(post))
    assert len(arrivals) == 2 and all(
        row["delivery_status"] == "accepted" for row in results
    )


@pytest.mark.asyncio
async def test_timeout_after_fanout_preserves_acceptance_and_uncertainty():
    async def send(**kwargs):
        if kwargs["key_file"] == "fast":
            return {"delivery_status": "accepted"}
        await asyncio.Event().wait()

    notify = tool(
        cfg=PushoverToolConfig(
            phase="in_call",
            timeout_ms=100,
            recipients={"fast": {"key_file": "fast"}, "slow": {"key_file": "slow"}},
        ),
        sender=SimpleNamespace(send=send),
    )
    live, _, _ = contexts()
    result = await notify.execute({}, live)
    assert result["status"] == "timeout" and result["delivery_status"] == "partial"
    assert result["notification_deliveries"]["fast"]["delivery_status"] == "accepted"
    assert result["notification_deliveries"]["slow"]["delivery_status"] == "uncertain"
    repeated = await notify.execute({}, live)
    assert repeated["deduplicated"] and repeated["delivery_status"] == "partial"


@pytest.mark.asyncio
async def test_suppression_is_per_recipient_not_per_tool(tmp_path):
    keys = []
    for index, char in enumerate("abc"):
        path = tmp_path / str(index)
        path.write_text(char * 30)
        keys.append(str(path))
    live, post, _ = contexts()
    first = tool(
        cfg=PushoverToolConfig(
            phase="in_call",
            token_file=keys[0],
            recipients={"alice": {"key_file": keys[1]}},
            destination_key="support",
        )
    )
    await first.execute({}, live)
    post.call_outcome = "transferred"
    post.transfer_destination = "support"
    post.tool_calls.append({"name": "blind_transfer"})
    finish = tool(
        "post_call",
        cfg=PushoverToolConfig(
            phase="post_call",
            token_file=keys[0],
            recipients={"alice": {"key_file": keys[1]}, "bob": {"key_file": keys[2]}},
            suppress_after_tool="notify_in_call",
        ),
    )
    result = await finish.execute(post)
    assert finish.sender.send.await_count == 1
    assert result["notification_deliveries"]["alice"]["delivery_status"] == "suppressed"
    assert result["notification_deliveries"]["bob"]["delivery_status"] == "accepted"


@pytest.mark.asyncio
async def test_pre_call_notifications_are_concurrent_after_enrichment():
    from src.engine import Engine
    from src.core.models import CallSession

    engine = Engine.__new__(Engine)
    session = CallSession(
        call_id="synthetic-concurrent-pre",
        caller_channel_id="synthetic-concurrent-pre",
        context_name="sales",
    )
    both = asyncio.Event()
    entered = []

    async def send(**kwargs):
        entered.append(kwargs)
        if len(entered) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 1)
        return {"delivery_status": "accepted"}

    tools = [
        PushoverTool(
            name, configured("pre_call").model_dump(), sender=SimpleNamespace(send=send)
        )
        for name in ("one", "two")
    ]
    session.tool_runtime_registry = SimpleNamespace(
        get_tools_for_context=lambda **kwargs: tools
    )
    session.tool_runtime_config = {"tools": {}}
    engine.transport_orchestrator = SimpleNamespace(
        get_context_config=lambda *args: SimpleNamespace(
            pre_call_tools=[], disable_global_pre_call_tools=[]
        )
    )
    engine.config = SimpleNamespace()
    engine.ari_client = None
    engine._save_session = AsyncMock()
    await engine._execute_pre_call_tools(session.call_id, session)
    assert len(entered) == 2
    assert len(session.pre_call_tool_calls) == 2
    assert all(
        row["delivery_status"] == "accepted" for row in session.pre_call_tool_calls
    )


@pytest.mark.asyncio
async def test_phase_dispatch_cancellation_keeps_per_recipient_diagnostics():
    entered = asyncio.Event()

    async def send(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    notify = tool("pre_call", sender=SimpleNamespace(send=send))
    context = PreCallContext(call_id="synthetic-cancel", caller_number="+15550102030")
    task = asyncio.create_task(notify.execute(context))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    result = context.execution_results[notify.definition.name]
    assert result["status"] == "cancelled"
    assert result["notification_deliveries"]["legacy"]["delivery_status"] == "uncertain"


@pytest.mark.asyncio
async def test_validation_bypasses_and_never_sets_delivery_cooldowns(tmp_path):
    token = tmp_path / "app"
    user = tmp_path / "user"
    token.write_text("a" * 30)
    user.write_text("b" * 30)
    seen = []
    responses = [
        (400, {"status": 0}),
        (400, {"status": 0}),
        (200, {"status": 1}),
        (429, {"status": 0}),
        (200, {"status": 1}),
    ]

    async def handle(request):
        seen.append(request.url.path)
        status, payload = responses.pop(0)
        return httpx.Response(status, json=payload)

    sender = PushoverSender(
        lock_dir=tmp_path / "locks",
        client_factory=lambda **kw: httpx.AsyncClient(
            transport=httpx.MockTransport(handle), **kw
        ),
    )
    args = {"token_file": str(token), "key_file": str(user)}
    assert (await sender.send(**args, validate=True))["delivery_status"] == "rejected"
    assert sender._blocked == {}
    assert (await sender.send(**args, message={"message": "synthetic"}))[
        "delivery_status"
    ] == "rejected"
    assert (await sender.send(**args, validate=True))["delivery_status"] == "accepted"
    assert (await sender.send(**args))["error_code"] == "rejection_cooldown"
    sender._blocked.clear()
    assert (await sender.send(**args))["error_code"] == "quota_exceeded"
    assert (await sender.send(**args, validate=True))["delivery_status"] == "accepted"
    assert (await sender.send(**args))["error_code"] == "rejection_cooldown"
    assert seen == [
        "/1/users/validate.json",
        "/1/messages.json",
        "/1/users/validate.json",
        "/1/messages.json",
        "/1/users/validate.json",
    ]


@pytest.mark.parametrize("in_call", [True, False])
def test_legacy_initializers_reject_invalid_native_entries_without_replacing_tools(
    in_call, caplog
):
    from src.tools.registry import ToolRegistry

    registry = ToolRegistry.isolated()
    registry.initialize_default_tools()
    original = registry.get("hangup_call")
    phase = "in_call" if in_call else "post_call"
    valid = configured(phase).model_dump()
    docs = {
        "hangup_call": valid,
        "mcp_fake": valid,
        "bad-name": valid,
        "invalid": {**valid, "priority": 2, "token": "private-synthetic-marker"},
        "wrong_phase": configured("pre_call" if in_call else "in_call").model_dump(),
        "good_notice": valid,
    }
    initialize = (
        registry.initialize_in_call_http_tools_from_config
        if in_call
        else registry.initialize_http_tools_from_config
    )
    initialize(docs)
    assert registry.get("hangup_call") is original
    assert registry.get("good_notice").phase == phase
    assert not any(
        registry.has(name)
        for name in ("mcp_fake", "bad-name", "invalid", "wrong_phase")
    )
    assert "private-synthetic-marker" not in caplog.text
    # A later per-agent initializer also cannot replace an already captured tool.
    captured = registry.get("good_notice")
    initialize({"good_notice": {**valid, "description": "replacement"}})
    assert registry.get("good_notice") is captured


@pytest.mark.parametrize("phase", ["pre_call", "in_call", "post_call"])
@pytest.mark.parametrize("body", [None, ""])
def test_public_default_fallback_is_generic_and_omits_disabled_summary(phase, body):
    raw = {"phase": phase, **({"body": body} if body is not None else {})}
    cfg = PushoverToolConfig.model_validate(raw)
    values = {
        "agent": "Example Agent",
        "caller_number": "+15550102030",
        "call_id": "synthetic-default",
        "outcome": "caller_hangup",
        "company": "Optional Company",
        "crm_status": "optional-match",
        "name": "Optional Name",
        "summary": "unused recap",
        "summary_status": "disabled",
    }
    result = render_message(cfg, phase, values)
    expected = "Caller: +15550102030\nCall: synthetic-default"
    if phase == "post_call":
        expected += "\nOutcome: caller_hangup"
    assert result["message"] == expected
    assert (
        result["title"]
        == {
            "pre_call": "Example Agent: incoming call",
            "in_call": "Example Agent: call update",
            "post_call": "Example Agent: call complete",
        }[phase]
    )
    assert not any(
        text in result["message"]
        for text in (
            "Company",
            "CRM",
            "Summary",
            "unknown",
            "unavailable",
            "unused recap",
        )
    )


@pytest.mark.parametrize("phase", ["in_call", "post_call"])
@pytest.mark.parametrize("summary,status", [("Factual recap", "ok"), ("", "timeout")])
def test_public_default_appends_only_requested_summary_and_reports_unavailable(
    phase, summary, status
):
    cfg = configured(phase, generate_summary=True, summary_provider="test_llm")
    result = render_message(
        cfg,
        phase,
        {
            "caller_number": "123",
            "call_id": "synthetic",
            "outcome": "failed_transfer",
            "summary": summary,
            "summary_status": status,
        },
    )
    assert result["message"].endswith("Summary: " + (summary or "unavailable"))
    assert "CRM" not in result["message"] and "Company" not in result["message"]
    if phase == "post_call":
        assert "Outcome: failed_transfer" in result["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ownership", ["current", "cleaning", "removed", "replaced"])
async def test_arrival_completion_cannot_resurrect_or_replace_call_session(ownership):
    from src.engine import Engine
    from src.core.models import CallSession
    from src.core.session_store import SessionStore

    entered, release = asyncio.Event(), asyncio.Event()

    async def send(**kwargs):
        entered.set()
        await release.wait()
        return {"delivery_status": "accepted"}

    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    engine.conversation_coordinator = SimpleNamespace(sync_from_session=AsyncMock())
    engine.config = SimpleNamespace()
    engine.ari_client = None
    engine.transport_orchestrator = SimpleNamespace(
        get_context_config=lambda *args: SimpleNamespace(
            pre_call_tools=[], disable_global_pre_call_tools=[]
        )
    )
    session = CallSession(
        call_id="synthetic-ownership",
        caller_channel_id="old-channel",
        context_name="pilot",
    )
    arrival = tool("pre_call", sender=SimpleNamespace(send=send))
    session.tool_runtime_registry = SimpleNamespace(
        get_tools_for_context=lambda **kwargs: [arrival]
    )
    session.tool_runtime_config = {"tools": {}}
    await engine.session_store.upsert_call(session)
    running = asyncio.create_task(
        engine._execute_pre_call_tools(session.call_id, session)
    )
    await asyncio.wait_for(entered.wait(), 1)
    replacement = None
    if ownership == "cleaning":
        session.cleanup_in_progress = True
    elif ownership in ("removed", "replaced"):
        await engine.session_store.remove_call(session.call_id)
        if ownership == "replaced":
            replacement = CallSession(
                call_id=session.call_id, caller_channel_id="new-channel"
            )
            await engine.session_store.upsert_call(replacement)
    release.set()
    await asyncio.wait_for(running, 1)
    current = await engine.session_store.get_by_call_id(session.call_id)
    if ownership == "removed":
        assert current is None
    elif ownership == "replaced":
        assert current is replacement and current.pre_call_tool_calls == []
    else:
        assert current is session
    assert engine.conversation_coordinator.sync_from_session.await_count == (
        1 if ownership == "current" else 0
    )
    if ownership in ("removed", "replaced"):
        assert await engine.session_store.get_by_channel_id("old-channel") is None
