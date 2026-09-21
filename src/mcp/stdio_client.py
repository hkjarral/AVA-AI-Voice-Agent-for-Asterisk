from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, List, Optional

import structlog

from .errors import MCPError, MCPProtocolError, MCPServerExited
from .stdio_framing import encode_message, decode_frame

logger = structlog.get_logger(__name__)

MCP_PROTOCOL_VERSION = "2025-06-18"


class MCPStdioClient:
    """Minimal MCP stdio client (JSON-RPC 2.0 + Content-Length framing)."""

    def __init__(
        self,
        *,
        server_id: str,
        command: List[str],
        cwd: Optional[str],
        env: Dict[str, str],
        restart_enabled: bool = True,
        max_restarts: int = 5,
        backoff_ms: int = 1000,
        default_timeout_ms: int = 10000,
    ):
        self.server_id = server_id
        self.command = list(command or [])
        self.cwd = cwd
        self.env = dict(env or {})
        self.restart_enabled = bool(restart_enabled)
        self.max_restarts = int(max_restarts)
        self.backoff_ms = int(backoff_ms)
        self.default_timeout_ms = int(default_timeout_ms)

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._rx_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._exit_task: Optional[asyncio.Task] = None
        self._pending: Dict[int, asyncio.Future] = {}
        self._next_id = 1
        self._write_lock = asyncio.Lock()
        self._restarts = 0
        self._closing = False

    async def start(self) -> None:
        await self._ensure_started()

    async def stop(self) -> None:
        self._closing = True
        await self._shutdown_process()

    async def initialize(self) -> None:
        await self._ensure_started()
        await self.request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "Asterisk-AI-Voice-Agent", "version": "dev"},
            },
        )
        await self.notify("initialized", {})

    async def list_tools(self) -> List[Dict[str, Any]]:
        await self._ensure_started()
        result = await self.request("tools/list", {})
        tools = result.get("tools", [])
        return tools if isinstance(tools, list) else []

    async def call_tool(self, *, name: str, arguments: Dict[str, Any], timeout_ms: Optional[int] = None) -> Dict[str, Any]:
        await self._ensure_started()
        timeout = self.default_timeout_ms if timeout_ms is None else int(timeout_ms)
        return await self.request("tools/call", {"name": name, "arguments": arguments or {}}, timeout_ms=timeout)

    async def notify(self, method: str, params: Dict[str, Any]) -> None:
        await self._ensure_started()
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        await self._send(payload)

    async def request(self, method: str, params: Dict[str, Any], timeout_ms: Optional[int] = None) -> Dict[str, Any]:
        await self._ensure_started()
        if not self._proc or self._proc.returncode is not None:
            raise MCPServerExited(f"MCP server '{self.server_id}' is not running")

        request_id = self._next_id
        self._next_id += 1

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[request_id] = fut

        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        await self._send(payload)

        timeout = None
        if timeout_ms is not None and int(timeout_ms) > 0:
            timeout = float(timeout_ms) / 1000.0

        try:
            response = await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(request_id, None)

        if not isinstance(response, dict):
            raise MCPProtocolError(f"Invalid MCP response type: {type(response).__name__}")

        if "error" in response and response["error"]:
            raise MCPError(str(response["error"]))

        result = response.get("result")
        if not isinstance(result, dict):
            # Some servers may return non-dict results; wrap for consistency.
            return {"value": result}
        return result

    async def _ensure_started(self) -> None:
        if self._closing:
            raise MCPServerExited(f"MCP server '{self.server_id}' is shutting down")

        if self._proc and self._proc.returncode is None:
            return

        if self._proc and self._proc.returncode is not None:
            await self._shutdown_process()

        if self._restarts > 0 and not self.restart_enabled:
            raise MCPServerExited(f"MCP server '{self.server_id}' is down and restart is disabled")

        if self._restarts > 0:
            await asyncio.sleep(max(0.0, float(self.backoff_ms) / 1000.0))

        if self._restarts > self.max_restarts and self.restart_enabled:
            raise MCPServerExited(f"MCP server '{self.server_id}' exceeded restart limit ({self.max_restarts})")

        if not self.command:
            raise MCPError(f"MCP server '{self.server_id}' missing command")

        env = os.environ.copy()
        env.update(self.env)

        logger.info("Starting MCP stdio server", server=self.server_id, command=self.command)
        self._proc = await asyncio.create_subprocess_exec(
            *self.command,
            cwd=self.cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._restarts += 1

        self._rx_task = asyncio.create_task(self._rx_loop(), name=f"mcp-rx-{self.server_id}")
        self._stderr_task = asyncio.create_task(self._stderr_loop(), name=f"mcp-stderr-{self.server_id}")
        self._exit_task = asyncio.create_task(self._exit_watch(), name=f"mcp-exit-{self.server_id}")

        # MCP handshake
        await self.initialize()

    async def _shutdown_process(self) -> None:
        proc = self._proc
        self._proc = None

        for task in (self._rx_task, self._stderr_task, self._exit_task):
            if task and not task.done():
                task.cancel()
        self._rx_task = None
        self._stderr_task = None
        self._exit_task = None

        # Fail any pending requests
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(MCPServerExited(f"MCP server '{self.server_id}' stopped"))
        self._pending.clear()

        if not proc:
            return

        try:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    proc.kill()
        except Exception:
            logger.debug("Failed to terminate MCP server", server=self.server_id, exc_info=True)

    async def _send(self, payload: Dict[str, Any]) -> None:
        proc = self._proc
        if not proc or proc.returncode is not None or not proc.stdin:
            raise MCPServerExited(f"MCP server '{self.server_id}' is not running")

        data = encode_message(payload)
        async with self._write_lock:
            proc.stdin.write(data)
            await proc.stdin.drain()

    async def _rx_loop(self) -> None:
        proc = self._proc
        if not proc or not proc.stdout:
            return

        buf = bytearray()
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                return
            buf.extend(chunk)
            while True:
                msg, consumed = decode_frame(buf)
                if msg is None:
                    break
                del buf[:consumed]
                await self._handle_message(msg)

    async def _stderr_loop(self) -> None:
        proc = self._proc
        if not proc or not proc.stderr:
            return

        last_log_ts = 0.0
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            now = time.time()
            if now - last_log_ts > 0.25:
                last_log_ts = now
                logger.info("MCP server stderr", server=self.server_id, line=line.decode("utf-8", errors="replace").rstrip())

    async def _exit_watch(self) -> None:
        proc = self._proc
        if not proc:
            return
        rc = await proc.wait()
        if self._closing:
            return
        logger.warning("MCP server exited", server=self.server_id, returncode=rc)
        # Pending requests will error on next ensure_started; also fail them now.
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(MCPServerExited(f"MCP server '{self.server_id}' exited ({rc})"))

    async def _handle_message(self, msg: Dict[str, Any]) -> None:
        # JSON-RPC response
        if "id" in msg:
            req_id = msg.get("id")
            if isinstance(req_id, int) and req_id in self._pending:
                fut = self._pending[req_id]
                if not fut.done():
                    fut.set_result(msg)
            return
        # Notifications are ignored for now
