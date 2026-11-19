"""Calendar data must not become instructions to read technical errors aloud."""

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
            "status": "error",
            "error_code": "invalid_datetime",
            "message": "Bookings use whole-minute times.",
        },
        {
            "status": "error",
            "error_code": "mutation_uncertain",
            "message": "Calendar deletion outcome is uncertain.",
        },
        {
            "status": "success",
            "reservation_status": "created",
            "message": "Event created.",
        },
    ],
)
async def test_calendar_returns_data_with_business_guidance_instead_of_verbatim_speech(
    is_ga, result
):
    socket = AsyncMock()
    adapter = OpenAIToolAdapter(None)
    await adapter.send_tool_result(
        {
            "call_id": "synthetic-function",
            "function_name": "microsoft_calendar",
            **result,
        },
        {"websocket": socket, "is_ga": is_ga},
    )
    events = [json.loads(call.args[0]) for call in socket.send.await_args_list]
    assert json.loads(events[0]["item"]["output"]) == result
    instructions = events[1]["response"]["instructions"]
    assert "Please say the following" not in instructions
    assert result["message"] not in instructions
    assert "read-only" in instructions and "uncertain outcomes" in instructions
    assert ("modalities" in events[1]["response"]) is not is_ga


@pytest.mark.asyncio
async def test_other_tool_speech_and_hangup_behavior_remain_unchanged():
    socket = AsyncMock()
    adapter = OpenAIToolAdapter(None)
    await adapter.send_tool_result(
        {
            "call_id": "synthetic-transfer",
            "function_name": "blind_transfer",
            "status": "success",
            "message": "Connecting you now.",
        },
        {"websocket": socket},
    )
    assert (
        json.loads(socket.send.await_args_list[1].args[0])["response"]["instructions"]
        == "Please say the following to the user: Connecting you now."
    )
    socket.reset_mock()
    await adapter.send_tool_result(
        {
            "call_id": "synthetic-hangup",
            "function_name": "hangup_call",
            "status": "success",
            "will_hangup": True,
        },
        {"websocket": socket},
    )
    assert socket.send.await_count == 1
