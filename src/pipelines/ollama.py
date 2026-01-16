"""
Ollama LLM adapter for self-hosted local language models.

This adapter implements the LLMComponent interface for Ollama's /api/chat endpoint,
enabling users to run their own LLMs on Mac Mini, gaming PCs, or any machine with
Ollama installed.

FEATURES:
- Connects to user's Ollama instance (requires network-accessible URL)
- Supports tool calling with compatible models (Llama 3.2, Mistral, etc.)
- Auto-detects tool support and falls back gracefully
- No API key required - fully self-hosted

COMPATIBLE MODELS FOR TOOL CALLING:
- llama3.2, llama3.1, llama3
- mistral, mistral-nemo
- qwen2.5, qwen2
- command-r, command-r-plus
- See docs/OLLAMA_SETUP.md for full list

SETUP:
1. Install Ollama: https://ollama.ai
2. Pull a model: ollama pull llama3.2
3. Expose on network: OLLAMA_HOST=0.0.0.0 ollama serve
4. Configure base_url in ai-agent.yaml: http://<your-ip>:11434
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Union

import aiohttp
from urllib.parse import urlparse

from ..logging_config import get_logger
from ..tools.registry import tool_registry
from .base import Component, LLMComponent, LLMResponse

logger = get_logger(__name__)

# Default Ollama endpoint (user must configure their own)
_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_MODEL = "llama3.2"

# Models known to support tool calling
_TOOL_CAPABLE_MODELS = {
    "llama3.2", "llama3.1", "llama3", "llama3.2:1b", "llama3.2:3b",
    "mistral", "mistral-nemo", "mistral:7b",
    "qwen2.5", "qwen2.5:7b", "qwen2.5:14b", "qwen2",
    "command-r", "command-r-plus",
    "nemotron", "granite3-dense",
}


class OllamaLLMAdapter(LLMComponent):
    """
    LLM adapter for self-hosted Ollama instances.
    
    Supports tool calling with compatible models. When using models that
    don't support tools, the adapter falls back to text-only mode.
    
    Users must expose Ollama on their network for Docker to reach it.
    Example: OLLAMA_HOST=0.0.0.0 ollama serve
    """

    component_key = "ollama_llm"

    def __init__(
        self,
        app_config: Any,
        pipeline_defaults: Optional[Dict[str, Any]] = None,
    ):
        self._app_config = app_config
        self._pipeline_defaults = pipeline_defaults or {}
        self._session: Optional[aiohttp.ClientSession] = None
        self._sessions: Dict[str, Dict[str, Any]] = {}  # per-call state
        self._tools_supported: Dict[str, bool] = {}  # model -> supports tools

    def _compose_options(self, runtime_opts: Dict[str, Any]) -> Dict[str, Any]:
        """Merge pipeline defaults with runtime options."""
        merged = dict(self._pipeline_defaults)
        merged.update(runtime_opts or {})
        
        # Set defaults
        merged.setdefault("base_url", _DEFAULT_BASE_URL)
        merged.setdefault("model", _DEFAULT_MODEL)
        merged.setdefault("temperature", 0.7)
        merged.setdefault("timeout_sec", 60)  # Local models may be slower
        merged.setdefault("stream", False)
        merged.setdefault("max_tokens", 200)
        merged.setdefault("tools_enabled", True)

        # Guardrail: a common misconfiguration is leaving OpenAI-style pipeline options in place
        # while selecting the Ollama adapter (which uses /api/* endpoints). That yields nginx 404s.
        try:
            base_url_raw = str(merged.get("base_url") or "").strip()
            parsed = urlparse(base_url_raw)
            host = (parsed.hostname or "").lower()
            path = (parsed.path or "").rstrip("/")
            if host == "api.openai.com" or path.endswith("/v1"):
                logger.warning(
                    "Ollama base_url looks like an OpenAI endpoint; this will 404 on /api/chat",
                    base_url=merged.get("base_url"),
                    hint="If using Ollama, set base_url to http://<ollama-host>:11434 and remove any pipeline llm overrides",
                )
        except Exception:
            pass
        
        return merged

    def _model_supports_tools(self, model: str) -> bool:
        """Check if model is known to support tool calling."""
        model_base = model.split(":")[0].lower()
        return model_base in _TOOL_CAPABLE_MODELS

    async def start(self) -> None:
        """Initialize the adapter."""
        base_url = self._pipeline_defaults.get("base_url", _DEFAULT_BASE_URL)
        model = self._pipeline_defaults.get("model", _DEFAULT_MODEL)
        logger.info(
            "Ollama LLM adapter initialized",
            base_url=base_url,
            model=model,
            tools_capable=self._model_supports_tools(model),
        )

    async def stop(self) -> None:
        """Clean up resources."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        self._sessions.clear()
        self._tools_supported.clear()
        logger.debug("Ollama LLM adapter stopped")

    async def open_call(self, call_id: str, options: Dict[str, Any]) -> None:
        """Initialize per-call state with conversation history."""
        merged = self._compose_options(options)
        self._sessions[call_id] = {
            "messages": [],  # Ollama uses messages array like OpenAI
            "options": merged,
            "tools_attempted": False,
            "tools_failed": False,
        }
        logger.debug(
            "Ollama LLM session opened",
            call_id=call_id,
            model=merged.get("model"),
            base_url=merged.get("base_url"),
        )

    async def close_call(self, call_id: str) -> None:
        """Clean up per-call state."""
        if call_id in self._sessions:
            del self._sessions[call_id]
            logger.debug("Ollama LLM session closed", call_id=call_id)

    async def _ensure_session(self) -> None:
        """Ensure aiohttp session exists."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

    def _build_tools_schema(self, tool_names: List[str]) -> List[Dict[str, Any]]:
        """Build Ollama-compatible tool schemas from tool registry."""
        tools = []
        for name in tool_names:
            tool = tool_registry.get(name)
            if tool:
                # Ollama uses same format as OpenAI for tools
                tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.definition.name,
                        "description": tool.definition.description,
                        "parameters": {
                            "type": "object",
                            "properties": {
                                p.name: {
                                    "type": p.type,
                                    "description": p.description,
                                }
                                for p in tool.definition.parameters
                            },
                            "required": [
                                p.name for p in tool.definition.parameters if p.required
                            ],
                        },
                    },
                })
        return tools

    async def generate(
        self,
        call_id: str,
        transcript: str,
        context: Dict[str, Any],
        options: Dict[str, Any],
    ) -> LLMResponse:
        """Generate a response using Ollama's /api/chat endpoint."""
        
        # Get or create session state
        if call_id not in self._sessions:
            await self.open_call(call_id, options)
        
        session_state = self._sessions[call_id]
        merged = self._compose_options(options)
        messages = session_state["messages"]
        model = merged["model"]
        
        # Build messages array
        # Add system message if not already present
        if not messages or messages[0].get("role") != "system":
            system_prompt = context.get("system_prompt", "")
            if system_prompt:
                messages.insert(0, {"role": "system", "content": system_prompt})
        
        # Handle prior_messages from context (includes tool results)
        prior_messages = context.get("prior_messages", [])
        if prior_messages:
            # Sync session messages with prior_messages (which includes tool results)
            # Convert tool messages to user messages for Ollama compatibility
            for pm in prior_messages:
                role = pm.get("role")
                content = pm.get("content")
                
                # Skip if already in messages or no content
                if role == "system":
                    continue  # System already handled above
                elif role == "tool":
                    # Convert tool result to a user-style message Ollama understands
                    tool_content = content or "Tool executed successfully."
                    # Add as assistant message first acknowledging the tool was called
                    messages.append({
                        "role": "assistant", 
                        "content": "I have the information from the tool."
                    })
                    # Then add the result as user message with VERY explicit instructions
                    messages.append({
                        "role": "user",
                        "content": f"[SYSTEM] The tool returned: {tool_content}\n\nNow say this information to the caller in a natural way. Do not add extra commentary."
                    })
                elif role == "assistant" and pm.get("tool_calls"):
                    # Skip assistant tool_call messages (we handle the result above)
                    continue
                elif content and role in ("user", "assistant"):
                    # Add regular messages if not duplicate
                    if not any(m.get("content") == content and m.get("role") == role for m in messages):
                        messages.append({"role": role, "content": content})
        
        # Add user message only if there's actual transcript
        if transcript and transcript.strip():
            messages.append({"role": "user", "content": transcript})
        
        # Prepare API request
        await self._ensure_session()
        assert self._session
        
        url = f"{merged['base_url'].rstrip('/')}/api/chat"
        payload = {
            "model": model,
            "messages": messages[-10:],  # Keep last 10 messages for context
            "stream": False,
            "options": {},
        }
        payload["options"]["temperature"] = merged.get("temperature", 0.7)
        payload["options"]["num_predict"] = merged.get("max_tokens", 200)

        # Optional Ollama generation/runtime controls (pass-through)
        # - Context window: num_ctx (tokens)
        # - Some users may supply OpenAI-ish names; accept a couple common aliases.
        num_ctx = merged.get("num_ctx") or merged.get("context_window") or merged.get("context_length")
        if num_ctx is not None:
            try:
                payload["options"]["num_ctx"] = int(num_ctx)
            except Exception:
                logger.debug("Invalid num_ctx for Ollama (ignoring)", call_id=call_id, num_ctx=num_ctx)

        # Add tools if model supports them.
        # Tool availability is resolved per-context by the engine (contexts are the source of truth).
        tool_names = merged.get("tools", [])
        tools_enabled = bool(merged.get("tools_enabled", True))
        use_tools = (
            tool_names
            and tools_enabled
            and self._model_supports_tools(model)
            and not session_state.get("tools_failed", False)
        )
        
        if use_tools:
            tools_schema = self._build_tools_schema(tool_names)
            if tools_schema:
                payload["tools"] = tools_schema
                session_state["tools_attempted"] = True
        
        logger.debug(
            "Ollama chat request",
            call_id=call_id,
            model=model,
            messages_count=len(messages),
            tools_enabled=use_tools,
        )
        
        try:
            timeout = aiohttp.ClientTimeout(total=merged["timeout_sec"])
            async with self._session.post(url, json=payload, timeout=timeout) as response:
                if response.status >= 400:
                    body = await response.text()
                    logger.error(
                        "Ollama API error",
                        call_id=call_id,
                        status=response.status,
                        body_preview=body[:200],
                    )
                    # If tools failed, retry without them
                    if use_tools and "tool" in body.lower():
                        logger.warning(
                            "Ollama tool calling failed, retrying without tools",
                            call_id=call_id,
                            model=model,
                        )
                        session_state["tools_failed"] = True
                        # Remove the user message we just added and retry
                        messages.pop()
                        return await self.generate(call_id, transcript, context, options)
                    return LLMResponse(text="I'm having trouble connecting right now. Please try again.")
                
                data = await response.json()
                message = data.get("message", {})
                text = message.get("content", "").strip()
                tool_calls_raw = message.get("tool_calls", [])
                
                # Parse tool calls if present
                parsed_tool_calls = []
                if tool_calls_raw:
                    for tc in tool_calls_raw:
                        func = tc.get("function", {})
                        parsed_tool_calls.append({
                            "id": tc.get("id", f"call_{len(parsed_tool_calls)}"),
                            "name": func.get("name"),
                            "parameters": func.get("arguments", {}),
                            "type": "function",
                        })
                    logger.info(
                        "Ollama tool calls detected",
                        call_id=call_id,
                        tools=[tc["name"] for tc in parsed_tool_calls],
                    )
                
                # Clean up response text
                if "<think>" in text:
                    parts = text.split("</think>")
                    if len(parts) > 1:
                        text = parts[-1].strip()
                    else:
                        text = text.split("<think>")[0].strip()
                
                # Truncate if too long for voice
                if len(text) > 500:
                    text = text[:500]
                    last_period = text.rfind(".")
                    if last_period > 200:
                        text = text[:last_period + 1]
                
                logger.info(
                    "Ollama response",
                    call_id=call_id,
                    model=model,
                    response_length=len(text),
                    tool_calls=len(parsed_tool_calls),
                    preview=text[:80] if text else "(tool call only)",
                )
                
                # Add assistant response to history
                messages.append({"role": "assistant", "content": text})
                
                return LLMResponse(
                    text=text,
                    tool_calls=parsed_tool_calls,
                    metadata={"model": model, "done": data.get("done", True)},
                )
                
        except asyncio.TimeoutError:
            logger.warning("Ollama request timeout", call_id=call_id, timeout=merged["timeout_sec"])
            return LLMResponse(text="I'm taking too long to respond. Please try again.")
        except Exception as e:
            logger.error("Ollama request failed", call_id=call_id, error=str(e))
            return LLMResponse(text="I encountered an error. Please try again.")

    async def validate_connectivity(self, options: Dict[str, Any]) -> Dict[str, Any]:
        """Test connectivity to the Ollama instance and list available models."""
        merged = self._compose_options(options)
        base_url = merged.get("base_url", _DEFAULT_BASE_URL)
        
        try:
            # Check connectivity by listing available models
            url = f"{base_url.rstrip('/')}/api/tags"
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=timeout) as response:
                    if response.status == 200:
                        data = await response.json()
                        models = data.get("models", [])
                        model_names = [m.get("name", "") for m in models]
                        configured_model = merged.get("model", _DEFAULT_MODEL)
                        model_available = any(
                            configured_model in name or name.startswith(configured_model.split(":")[0])
                            for name in model_names
                        )
                        
                        return {
                            "healthy": True,
                            "error": None,
                            "details": {
                                "endpoint": base_url,
                                "configured_model": configured_model,
                                "model_available": model_available,
                                "available_models": model_names[:10],  # First 10 models
                                "tools_capable": self._model_supports_tools(configured_model),
                            },
                        }
                    body = await response.text()
                    return {
                        "healthy": False,
                        "error": f"Ollama API returned status {response.status}",
                        "details": {"endpoint": url, "response": body[:200]},
                    }
        except asyncio.TimeoutError:
            return {
                "healthy": False,
                "error": "Connection timeout - is Ollama running?",
                "details": {"endpoint": base_url, "hint": "Run: OLLAMA_HOST=0.0.0.0 ollama serve"},
            }
        except aiohttp.ClientConnectorError as e:
            return {
                "healthy": False,
                "error": f"Cannot connect to Ollama: {str(e)}",
                "details": {
                    "endpoint": base_url,
                    "hint": "Ensure Ollama is running and accessible. For Docker, use your host IP, not localhost.",
                },
            }
        except Exception as e:
            return {
                "healthy": False,
                "error": f"Connection failed: {str(e)}",
                "details": {"endpoint": base_url},
            }

    async def list_models(self, base_url: Optional[str] = None) -> Dict[str, Any]:
        """List available models from the Ollama instance."""
        url_to_use = base_url or self._pipeline_defaults.get("base_url", _DEFAULT_BASE_URL)
        
        try:
            url = f"{url_to_use.rstrip('/')}/api/tags"
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=timeout) as response:
                    if response.status == 200:
                        data = await response.json()
                        models = data.get("models", [])
                        
                        # Enrich with tool capability info
                        enriched_models = []
                        for model in models:
                            name = model.get("name", "")
                            enriched_models.append({
                                "name": name,
                                "size": model.get("size", 0),
                                "modified_at": model.get("modified_at", ""),
                                "tools_capable": self._model_supports_tools(name),
                            })
                        
                        return {
                            "success": True,
                            "models": enriched_models,
                            "error": None,
                        }
                    return {
                        "success": False,
                        "models": [],
                        "error": f"API returned status {response.status}",
                    }
        except Exception as e:
            return {
                "success": False,
                "models": [],
                "error": str(e),
            }
