import json
import os
import unittest
from unittest.mock import patch

from src.mcp_servers import memcode_memory_server as server


class _Response:
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
        with patch.dict(os.environ, {"MEMCODE_API_KEY": "test-key"}, clear=False):
            with patch("urllib.request.urlopen") as urlopen:
                with self.assertRaisesRegex(ValueError, "approved must be true"):
                    server.call_tool(
                        "save_approved_memory",
                        {"text": "Prefers email", "approved": False},
                    )
                urlopen.assert_not_called()

    def test_search_uses_credential_derived_identity(self):
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
            result["structured"]["sources"][0]["content"],
            "Prefers email follow-ups",
        )
        self.assertNotIn("test-key", json.dumps(result))

    def test_save_returns_ingest_receipt(self):
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
        self.assertEqual(result["structured"]["job_id"], "job-123")
        self.assertEqual(result["structured"]["status"], "queued")

    def test_protocol_errors_are_bounded_and_do_not_raise(self):
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
        self.assertIs(response["result"]["structured"]["error"], True)
        self.assertIn(
            "MEMCODE_API_KEY is not configured",
            response["result"]["structured"]["spoken"],
        )


if __name__ == "__main__":
    unittest.main()
