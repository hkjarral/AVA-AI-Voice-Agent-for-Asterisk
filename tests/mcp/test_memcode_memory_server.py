import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from src.mcp.stdio_client import MCP_PROTOCOL_VERSION, MCPStdioClient
from src.mcp.stdio_framing import decode_frame, encode_message
from src.mcp_servers import memcode_memory_server as server
from src.tools.mcp_tool import MCPTool, MCPToolBehavior


class _Response:
    """Minimal context-managed HTTP response used by bridge tests."""

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class TestMemcodeMemoryServer(unittest.TestCase):
    def test_tools_list_contains_memory_surface(self):
        """The bridge advertises only its four bounded memory tools."""

        response = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertEqual(
            names,
            {
                "search_memories",
                "retrieve_answer",
                "save_approved_memory",
                "get_memory_ingest_status",
            },
        )

    def test_save_requires_explicit_exact_approval(self):
        """A save cannot reach the network without exact caller approval."""

        with patch.dict(os.environ, {"MEMCODE_API_KEY": "test-key"}, clear=False):
            with patch("urllib.request.urlopen") as urlopen:
                with self.assertRaisesRegex(ValueError, "approved must be true"):
                    server.call_tool(
                        "save_approved_memory",
                        {"text": "Prefers email", "approved": False},
                    )
                urlopen.assert_not_called()

    def test_search_uses_credential_derived_identity(self):
        """Search relies on the bearer identity and never sends a user id."""

        with patch.dict(os.environ, {"MEMCODE_API_KEY": "test-key"}, clear=False):
            with patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _Response(
                    {
                        "status": "ok",
                        "data": {
                            "memory_results": [
                                {
                                    "domain": "profile",
                                    "content": "Prefers email follow-ups",
                                    "score": 0.91,
                                }
                            ]
                        },
                    }
                )
                result = server.call_tool(
                    "search_memories", {"query": "follow-up preference", "top_k": 3}
                )

        request = urlopen.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://memory.memcode.in/v2/memory/search")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertNotIn("user_id", body)
        self.assertEqual(
            result["structuredContent"]["sources"][0]["content"],
            "Prefers email follow-ups",
        )
        self.assertNotIn("test-key", json.dumps(result))

    def test_save_returns_ingest_receipt(self):
        """An approved save preserves the durable ingest receipt fields."""

        with patch.dict(os.environ, {"MEMCODE_API_KEY": "test-key"}, clear=False):
            with patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _Response(
                    {"status": "ok", "data": {"job_id": "job-123", "status": "queued"}}
                )
                result = server.call_tool(
                    "save_approved_memory",
                    {"text": "Prefers email follow-ups", "approved": True},
                )

        body = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(body["user_query"], "Prefers email follow-ups")
        self.assertNotIn("user_id", body)
        self.assertEqual(result["structuredContent"]["job_id"], "job-123")
        self.assertEqual(result["structuredContent"]["status"], "queued")

    def test_plaintext_api_url_is_rejected_before_authorization(self):
        """The bridge never sends a bearer credential to a plaintext endpoint."""

        with patch.dict(
            os.environ,
            {
                "MEMCODE_API_KEY": "test-key",
                "MEMCODE_API_URL": "http://memory.example.test",
            },
            clear=False,
        ):
            with patch("urllib.request.urlopen") as urlopen:
                with self.assertRaisesRegex(ValueError, "must be an https URL"):
                    server.call_tool(
                        "search_memories",
                        {"query": "follow-up preference"},
                    )
                urlopen.assert_not_called()

    def test_serialized_tool_results_preserve_machine_fields(self):
        """Wire responses retain job, status, and source data in structuredContent."""

        with patch.dict(os.environ, {"MEMCODE_API_KEY": "test-key"}, clear=False):
            with patch("urllib.request.urlopen") as urlopen:
                urlopen.side_effect = [
                    _Response(
                        {
                            "status": "ok",
                            "data": {"job_id": "job-123", "status": "queued"},
                        }
                    ),
                    _Response(
                        {
                            "status": "ok",
                            "data": {
                                "job_id": "job-123",
                                "status": "completed",
                                "progress": {"step": "complete"},
                            },
                        }
                    ),
                    _Response(
                        {
                            "status": "ok",
                            "data": {
                                "memory_results": [
                                    {
                                        "domain": "profile",
                                        "content": "Prefers email follow-ups",
                                        "score": 0.91,
                                    }
                                ]
                            },
                        }
                    ),
                ]
                responses = [
                    server.handle_message(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "tools/call",
                            "params": {
                                "name": "save_approved_memory",
                                "arguments": {
                                    "text": "Prefers email follow-ups",
                                    "approved": True,
                                },
                            },
                        }
                    ),
                    server.handle_message(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {
                                "name": "get_memory_ingest_status",
                                "arguments": {"job_id": "job-123"},
                            },
                        }
                    ),
                    server.handle_message(
                        {
                            "jsonrpc": "2.0",
                            "id": 3,
                            "method": "tools/call",
                            "params": {
                                "name": "search_memories",
                                "arguments": {"query": "follow-up preference"},
                            },
                        }
                    ),
                ]

        decoded = []
        for response in responses:
            wire = encode_message(response)
            message, consumed = decode_frame(bytearray(wire))
            self.assertEqual(consumed, len(wire))
            decoded.append(message)

        saved = decoded[0]["result"]["structuredContent"]
        completed = decoded[1]["result"]["structuredContent"]
        searched = decoded[2]["result"]["structuredContent"]
        self.assertEqual(saved["job_id"], "job-123")
        self.assertEqual(saved["status"], "queued")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["progress"], {"step": "complete"})
        self.assertEqual(searched["sources"][0]["content"], "Prefers email follow-ups")

    def test_protocol_errors_are_bounded_and_do_not_raise(self):
        """Expected configuration errors remain bounded MCP tool results."""

        without_key = dict(os.environ)
        without_key.pop("MEMCODE_API_KEY", None)
        with patch.dict(os.environ, without_key, clear=True):
            response = server.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "tools/call",
                    "params": {
                        "name": "search_memories",
                        "arguments": {"query": "preference"},
                    },
                }
            )
        self.assertEqual(response["id"], 9)
        self.assertIs(response["result"]["structuredContent"]["error"], True)
        self.assertIn(
            "MEMCODE_API_KEY is not configured",
            response["result"]["structuredContent"]["spoken"],
        )

    def test_initialize_and_speech_use_standard_structured_content(self):
        """The bridge negotiates the structured-result protocol and AVA reads it."""

        response = server.handle_message(
            {"jsonrpc": "2.0", "id": 7, "method": "initialize"}
        )
        self.assertEqual(response["result"]["protocolVersion"], MCP_PROTOCOL_VERSION)

        tool = MCPTool(
            exposed_name="mcp_memcode_search_memories",
            server_id="memcode",
            mcp_tool_name="search_memories",
            description="Search memory",
            input_schema={"type": "object"},
            manager=object(),
            behavior=MCPToolBehavior(speech_field="spoken"),
        )
        self.assertEqual(
            tool._build_speech_message(
                {
                    "content": [{"type": "text", "text": "Fallback"}],
                    "structuredContent": {"spoken": "Structured speech"},
                }
            ),
            "Structured speech",
        )


class TestMCPStdioClient(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_requests_structured_content_protocol(self):
        """The shared stdio client requests MCP 2025-06-18."""

        client = MCPStdioClient(
            server_id="test",
            command=["unused"],
            cwd=None,
            env={},
        )
        ensure_started = AsyncMock()
        request = AsyncMock(return_value={"protocolVersion": MCP_PROTOCOL_VERSION})
        notify = AsyncMock()

        with patch.object(client, "_ensure_started", ensure_started):
            with patch.object(client, "request", request):
                with patch.object(client, "notify", notify):
                    await client.initialize()

        self.assertEqual(
            request.await_args.args[1]["protocolVersion"],
            MCP_PROTOCOL_VERSION,
        )
        notify.assert_awaited_once_with("initialized", {})


if __name__ == "__main__":
    unittest.main()
