"""Internal bookkeeping must not become caller-facing speech instructions."""

import json
from unittest.mock import AsyncMock

import pytest

from src.tools.adapters.openai import OpenAIToolAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize("is_ga", [True, False])
@pytest.mark.parametrize(
    "result",
    [
        {
            "status": "success",
            "message": "Updated 'customer_name' for this call.",
            "changed": True,
        },
        {
            "status": "success",
            "message": "'customer_name' already has that value.",
            "changed": False,
        },
        {"status": "error", "message": "'secret' is not an allowed correctable field."},
        {"status": "error", "message": "Call state is unavailable."},
    ],
)
async def test_metadata_ack_is_data_and_conversation_can_continue(is_ga, result):
    socket = AsyncMock()
    adapter = OpenAIToolAdapter(None)
    await adapter.send_tool_result(
        {
            "call_id": "synthetic-metadata",
            "function_name": "update_call_metadata",
            **result,
        },
        {"websocket": socket, "is_ga": is_ga},
    )
    events = [json.loads(call.args[0]) for call in socket.send.await_args_list]
    assert [event["type"] for event in events] == [
        "conversation.item.create",
        "response.create",
    ]
    output = json.loads(events[0]["item"]["output"])
    assert output["status"] == result["status"]
    assert output["message"] == result["message"]
    response = events[1]["response"]
    instructions = response["instructions"]
    assert result["message"] not in instructions
    assert "Please say" not in instructions
    assert "silent internal bookkeeping" in instructions
    assert "Do not read or paraphrase" in instructions
    assert "remaining required metadata or source updates" in instructions
    assert ("modalities" in response) is not is_ga


@pytest.mark.asyncio
async def test_name_then_source_updates_keep_separate_outputs_and_continuations():
    socket = AsyncMock()
    adapter = OpenAIToolAdapter(None)
    for index, field in enumerate(("customer_name", "customer_name_source")):
        await adapter.send_tool_result(
            {
                "call_id": f"synthetic-{index}",
                "function_name": "update_call_metadata",
                "status": "success",
                "message": f"Updated '{field}' for this call.",
            },
            {"websocket": socket},
        )
    events = [json.loads(call.args[0]) for call in socket.send.await_args_list]
    assert [event["type"] for event in events] == [
        "conversation.item.create",
        "response.create",
    ] * 2
    assert [events[index]["item"]["call_id"] for index in (0, 2)] == [
        "synthetic-0",
        "synthetic-1",
    ]
    assert (
        events[1]["response"]["instructions"] == events[3]["response"]["instructions"]
    )
