from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import structlog
from prometheus_client import Counter, Histogram, Gauge

from src.config import MCPConfig, MCPServerConfig, MCPToolConfig
from src.mcp.naming import make_exposed_tool_name, is_provider_safe_tool_name
from src.mcp.stdio_client import MCPStdioClient
from src.mcp.streamable_http_client import MCPStreamableHTTPClient
from src.tools.mcp_tool import MCPTool, MCPToolBehavior

logger = structlog.get_logger(__name__)

_MCP_TOOL_CALLS_TOTAL = Counter(
    "ai_agent_mcp_tool_calls_total",
    "Total MCP tool calls",
    labelnames=("server", "tool", "status"),
)
_MCP_TOOL_LATENCY_SECONDS = Histogram(
    "ai_agent_mcp_tool_latency_seconds",
    "MCP tool call latency (seconds)",
    labelnames=("server", "tool"),
)
_MCP_SERVER_UP = Gauge(
    "ai_agent_mcp_server_up",
    "Whether an MCP server is up (1) or down (0)",
    labelnames=("server",),
)


@dataclass(frozen=True)
class _DiscoveredTool:
    name: str
    description: str
    input_schema: Optional[Dict[str, Any]]


class MCPClientManager:
    """Owns MCP server clients and registers MCP tools into the ToolRegistry."""

    def __init__(self, config: MCPConfig):
        self.config = config
        self._clients: Dict[str, MCPStdioClient | MCPStreamableHTTPClient] = {}
        self._discovered: Dict[str, Dict[str, _DiscoveredTool]] = {}
        self._tool_routes: Dict[str, Tuple[str, str]] = {}  # exposed_name -> (server_id, tool_name)
        self._server_up: Dict[str, bool] = {}
        self._server_errors: Dict[str, str] = {}
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        await self._start_servers_and_discover()

    async def stop(self) -> None:
        for client in list(self._clients.values()):
            try:
                await client.stop()
            except Exception:
                logger.debug("Failed stopping MCP client", server=client.server_id, exc_info=True)
        self._clients.clear()
        self._discovered.clear()
        self._tool_routes.clear()
        for server_id in list(self._server_up.keys()):
            self._server_up[server_id] = False
            _MCP_SERVER_UP.labels(server_id).set(0)
        self._started = False

    def is_enabled(self) -> bool:
        return bool(self.config and self.config.enabled)

    async def call_tool(self, *, server_id: str, tool_name: str, arguments: Dict[str, Any], timeout_ms: Optional[int]) -> Dict[str, Any]:
        client = self._clients.get(server_id)
        if not client:
            raise RuntimeError(f"Unknown MCP server: {server_id}")
        start = time.perf_counter()
        try:
            result = await client.call_tool(name=tool_name, arguments=arguments or {}, timeout_ms=timeout_ms)
            outcome = "error" if result.get("isError") or result.get("is_error") else "success"
            _MCP_TOOL_CALLS_TOTAL.labels(server_id, tool_name, outcome).inc()
            return result
        except Exception:
            _MCP_TOOL_CALLS_TOTAL.labels(server_id, tool_name, "error").inc()
            raise
        finally:
            try:
                _MCP_TOOL_LATENCY_SECONDS.labels(server_id, tool_name).observe(max(0.0, time.perf_counter() - start))
            except Exception:
                pass

    def register_tools(self, tool_registry: Any) -> List[str]:
        """Register MCP tools into the shared ToolRegistry.

        Returns the list of exposed tool names registered.
        """
        registered: List[str] = []
        for server_id, server_cfg in (self.config.servers or {}).items():
            if not getattr(server_cfg, "enabled", True):
                continue
            discovered = self._discovered.get(server_id, {})
            if not discovered:
                logger.warning("No tools discovered for MCP server", server=server_id)
                continue

            tool_cfgs = server_cfg.tools or []
            if tool_cfgs:
                for cfg in tool_cfgs:
                    t = discovered.get(cfg.name)
                    if not t:
                        logger.warning("Configured MCP tool not found in discovery", server=server_id, tool=cfg.name)
                        continue
                    exposed_name, tool_obj = self._build_tool(server_id, server_cfg, t, cfg)
                    if tool_registry.has(exposed_name):
                        logger.error("MCP tool name collision; skipping", server=server_id, tool=t.name, exposed=exposed_name)
                        continue
                    tool_registry.register_instance(tool_obj)
                    self._tool_routes[exposed_name] = (server_id, t.name)
                    registered.append(exposed_name)
            else:
                for t in discovered.values():
                    exposed_name, tool_obj = self._build_tool(server_id, server_cfg, t, None)
                    if tool_registry.has(exposed_name):
                        logger.error("MCP tool name collision; skipping", server=server_id, tool=t.name, exposed=exposed_name)
                        continue
                    tool_registry.register_instance(tool_obj)
                    self._tool_routes[exposed_name] = (server_id, t.name)
                    registered.append(exposed_name)

        logger.info("Registered MCP tools", count=len(registered), tools=registered)
        return registered

    def unregister_tools(self, tool_registry: Any) -> int:
        """Unregister MCP tools previously registered by this manager."""
        removed = 0
        for exposed_name in list(self._tool_routes.keys()):
            try:
                if hasattr(tool_registry, "unregister"):
                    if tool_registry.unregister(exposed_name):
                        removed += 1
            except Exception:
                logger.debug("Failed to unregister MCP tool", tool=exposed_name, exc_info=True)
        self._tool_routes.clear()
        return removed

    def get_status(self) -> Dict[str, Any]:
        """Return a safe-to-expose status snapshot (no secret env values)."""
        servers: Dict[str, Any] = {}
        for server_id, server_cfg in (self.config.servers or {}).items():
            discovered = self._discovered.get(server_id, {})
            registered = [name for name, (sid, _t) in self._tool_routes.items() if sid == server_id]
            servers[server_id] = {
                "enabled": bool(getattr(server_cfg, "enabled", True)),
                "transport": getattr(server_cfg, "transport", "stdio"),
                "command": list(getattr(server_cfg, "command", []) or []) if server_cfg.transport == "stdio" else [],
                "cwd": getattr(server_cfg, "cwd", None) if server_cfg.transport == "stdio" else None,
                "up": bool(self._server_up.get(server_id, False)),
                "last_error": self._server_errors.get(server_id),
                "discovered_tools": sorted(list(discovered.keys())),
                "registered_tools": sorted(registered),
                "configured_tools": [t.name for t in (getattr(server_cfg, "tools", None) or [])],
            }
        return {
            "enabled": bool(self.config and self.config.enabled),
            "servers": servers,
            "tool_routes": {k: {"server": v[0], "tool": v[1]} for k, v in self._tool_routes.items()},
        }

    async def test_server(self, server_id: str) -> Dict[str, Any]:
        if not self.is_enabled():
            return {"ok": False, "error": "MCP disabled"}
        server_cfg = (self.config.servers or {}).get(server_id)
        if not server_cfg:
            return {"ok": False, "error": f"Unknown server: {server_id}"}
        if not getattr(server_cfg, "enabled", True):
            return {"ok": False, "error": f"Server '{server_id}' is disabled"}
        client = self._clients.get(server_id)
        if not client:
            # Start it on-demand using the same start path.
            await self._start_servers_and_discover()
            client = self._clients.get(server_id)
        if not client:
            return {"ok": False, "error": "Failed to create client"}
        try:
            tools = await client.list_tools()
            names = []
            for t in tools:
                if isinstance(t, dict) and t.get("name"):
                    names.append(str(t["name"]))
            return {"ok": True, "tools": sorted(names)}
        except Exception as exc:
            error = str(exc) if server_cfg.transport == "stdio" else type(exc).__name__
            return {"ok": False, "error": error}

    async def _start_servers_and_discover(self) -> None:
        if not self.is_enabled():
            return

        async def start_one(server_id: str, server_cfg: MCPServerConfig) -> None:
            self._server_errors.pop(server_id, None)
            if not getattr(server_cfg, "enabled", True):
                self._server_up[server_id] = False
                _MCP_SERVER_UP.labels(server_id).set(0)
                return
            transport = (server_cfg.transport or "stdio").strip().lower().replace("-", "_")
            try:
                if transport == "stdio":
                    if not server_cfg.command:
                        raise ValueError("Missing command")
                    client = MCPStdioClient(
                        server_id=server_id,
                        command=server_cfg.command,
                        cwd=server_cfg.cwd,
                        env=server_cfg.env,
                        restart_enabled=server_cfg.restart.enabled,
                        max_restarts=server_cfg.restart.max_restarts,
                        backoff_ms=server_cfg.restart.backoff_ms,
                        default_timeout_ms=server_cfg.defaults.timeout_ms,
                    )
                elif transport == "streamable_http":
                    client = MCPStreamableHTTPClient(
                        server_id=server_id,
                        url=server_cfg.url or "",
                        headers=server_cfg.headers,
                        default_timeout_ms=server_cfg.defaults.timeout_ms,
                    )
                else:
                    raise ValueError("Unsupported transport")
                self._clients[server_id] = client
                await client.start()
                tools = await client.list_tools()
                _MCP_SERVER_UP.labels(server_id).set(1)
                self._server_up[server_id] = True
                discovered: Dict[str, _DiscoveredTool] = {}
                for raw in tools:
                    if not isinstance(raw, dict):
                        continue
                    name = str(raw.get("name") or "").strip()
                    if not name:
                        continue
                    discovered[name] = _DiscoveredTool(
                        name=name,
                        description=str(raw.get("description") or "").strip(),
                        input_schema=raw.get("inputSchema") if isinstance(raw.get("inputSchema"), dict) else None,
                    )
                self._discovered[server_id] = discovered
                logger.info("Discovered MCP tools", server=server_id, count=len(discovered), tools=list(discovered.keys()))
            except Exception as exc:
                _MCP_SERVER_UP.labels(server_id).set(0)
                self._server_up[server_id] = False
                self._server_errors[server_id] = str(exc) if transport == "stdio" else type(exc).__name__
                logger.warning("Failed to start/discover MCP server", server=server_id, transport=transport, error_type=type(exc).__name__)
                failed_client = self._clients.pop(server_id, None)
                if failed_client:
                    try:
                        await failed_client.stop()
                    except Exception:
                        logger.debug("Failed stopping MCP client after discovery error", server=server_id)

        await asyncio.gather(*(start_one(sid, scfg) for sid, scfg in (self.config.servers or {}).items()))

    def _build_tool(
        self,
        server_id: str,
        server_cfg: MCPServerConfig,
        discovered: _DiscoveredTool,
        tool_cfg: Optional[MCPToolConfig],
    ) -> Tuple[str, MCPTool]:
        exposed = None
        description = discovered.description
        behavior = MCPToolBehavior(
            speech_field=None,
            speech_template=None,
            timeout_ms=server_cfg.defaults.timeout_ms,
            slow_response_threshold_ms=server_cfg.defaults.slow_response_threshold_ms,
            slow_response_message=server_cfg.defaults.slow_response_message,
        )

        if tool_cfg:
            if tool_cfg.expose_as:
                exposed = tool_cfg.expose_as.strip()
            if tool_cfg.description:
                description = tool_cfg.description.strip()
            behavior = MCPToolBehavior(
                speech_field=tool_cfg.speech_field,
                speech_template=tool_cfg.speech_template,
                timeout_ms=tool_cfg.timeout_ms or server_cfg.defaults.timeout_ms,
                slow_response_threshold_ms=tool_cfg.slow_response_threshold_ms if tool_cfg.slow_response_threshold_ms is not None else server_cfg.defaults.slow_response_threshold_ms,
                slow_response_message=tool_cfg.slow_response_message or server_cfg.defaults.slow_response_message,
            )

        if not exposed:
            exposed = make_exposed_tool_name(server_id, discovered.name)

        if not is_provider_safe_tool_name(exposed):
            raise ValueError(f"Invalid exposed MCP tool name '{exposed}' (must match [A-Za-z0-9_]+)")

        # Ensure schema is object-shaped; OpenAI/Deepgram expect object params
        input_schema = discovered.input_schema
        if isinstance(input_schema, dict) and input_schema.get("type") is None:
            input_schema = {"type": "object", **input_schema}

        tool = MCPTool(
            exposed_name=exposed,
            server_id=server_id,
            mcp_tool_name=discovered.name,
            description=description,
            input_schema=input_schema,
            manager=self,
            behavior=behavior,
        )
        return exposed, tool
