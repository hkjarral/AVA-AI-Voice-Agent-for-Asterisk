"""
Telnyx AI Inference modular LLM adapter (OpenAI-compatible Chat Completions).

This adapter is intentionally separate from OpenAI's adapter to:
- Use TELNYX_API_KEY (env-only) without interacting with OPENAI_API_KEY injection rules
- Support arbitrary model IDs (Claude/Llama/Mistral/etc.) without OpenAI-specific heuristics
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

import aiohttp

from ..config import AppConfig, TelnyxLLMProviderConfig
from ..logging_config import get_logger
from ..tools.registry import tool_registry
from .base import LLMComponent, LLMResponse

logger = get_logger(__name__)


_THINK_TAG_RE = re.compile(r"<think>.*?</think>\s*", flags=re.IGNORECASE | re.DOTALL)


def _strip_thinking(content: str) -> str:
    """
    Some Telnyx-hosted models (notably Qwen) may include chain-of-thought in `<think>...</think>`.
    We never want to speak that verbatim on a phone call.
    """
    text = (content or "").strip()
    if not text:
        return ""

    # Remove explicit <think> blocks when present.
    stripped = _THINK_TAG_RE.sub("", text).strip()
    if stripped != text:
        return stripped

    # If the model starts with a <think> tag but never closes it, drop the first block.
    if re.match(r"^\s*<think>\b", text, flags=re.IGNORECASE):
        without_tag = re.sub(r"^\s*<think>\b", "", text, flags=re.IGNORECASE).lstrip()
        parts = re.split(r"\n\s*\n", without_tag, maxsplit=1)
        if len(parts) == 2:
            return parts[1].strip()

        # Fallback: look for "Answer:" / "Final:" markers.
        m = re.search(r"\n\s*(final|answer)\s*[:：]\s*", without_tag, flags=re.IGNORECASE)
        if m:
            return without_tag[m.end() :].strip()

        return without_tag.strip()

    return text


def _url_host(url: str) -> str:
    try:
        return (urlparse(str(url)).hostname or "").lower()
    except Exception:
        return ""


def _make_http_headers(api_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "Asterisk-AI-Voice-Agent/1.0",
    }


class TelnyxLLMAdapter(LLMComponent):
    """Telnyx AI Inference LLM adapter using OpenAI-compatible /chat/completions."""

    DEFAULT_CHAT_BASE_URL = "https://api.telnyx.com/v2/ai"
    _MODELS_CACHE_TTL_SEC = 10 * 60

    def __init__(
        self,
        component_key: str,
        app_config: AppConfig,
        provider_config: TelnyxLLMProviderConfig,
        options: Optional[Dict[str, Any]] = None,
        *,
        session_factory: Optional[Callable[[], aiohttp.ClientSession]] = None,
    ):
        self.component_key = component_key
        self._app_config = app_config
        self._provider_defaults = provider_config
        self._pipeline_defaults = options or {}
        self._session_factory = session_factory
        self._session: Optional[aiohttp.ClientSession] = None
        self._default_timeout = float(
            self._pipeline_defaults.get("response_timeout_sec", provider_config.response_timeout_sec)
        )
        self._models_cache: Optional[list[str]] = None
        self._models_cache_at: float = 0.0

    async def start(self) -> None:
        logger.debug(
            "Telnyx LLM adapter initialized",
            component=self.component_key,
            default_base_url=_url_host(self._provider_defaults.chat_base_url),
            default_model=self._provider_defaults.chat_model,
        )

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _ensure_session(self) -> None:
        if self._session and not self._session.closed:
            return
        factory = self._session_factory or aiohttp.ClientSession
        self._session = factory()

    async def _fetch_model_ids(self, *, api_key: str, chat_base_url: str) -> list[str]:
        base = str(chat_base_url or self.DEFAULT_CHAT_BASE_URL).rstrip("/")
        url = f"{base}/models"
        headers = _make_http_headers(api_key)
        await self._ensure_session()
        assert self._session
        async with self._session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10.0)) as resp:
            body = await resp.text()
            resp.raise_for_status()
            data = json.loads(body)
            items = data.get("data") or []
            ids: list[str] = []
            for it in items:
                if isinstance(it, dict) and it.get("id"):
                    ids.append(str(it["id"]))
            return ids

    async def _maybe_resolve_model_id(
        self, model: str, *, api_key: str, chat_base_url: str, api_key_ref: Optional[str]
    ) -> str:
        """
        Telnyx model IDs are namespaced (e.g., `openai/gpt-4o-mini`).
        If the user supplies an un-namespaced model (e.g., `gpt-4o-mini`), try to
        resolve it by suffix match against the account's `/models` list.
        """
        raw = str(model or "").strip()
        if not raw or "/" in raw:
            return raw

        now = time.time()
        if not self._models_cache or (now - self._models_cache_at) > self._MODELS_CACHE_TTL_SEC:
            try:
                self._models_cache = await self._fetch_model_ids(api_key=api_key, chat_base_url=chat_base_url)
                self._models_cache_at = now
            except Exception as exc:
                logger.debug("Failed to refresh Telnyx model cache", error=str(exc), exc_info=True)
                return raw

        assert self._models_cache is not None
        matches = [mid for mid in self._models_cache if str(mid).rsplit("/", 1)[-1] == raw]
        if len(matches) == 1:
            resolved = matches[0]
            # Guardrail: OpenAI-prefixed models require an integration secret reference.
            if str(resolved).startswith("openai/") and not api_key_ref:
                return raw
            logger.info(
                "Resolved Telnyx model alias",
                component=self.component_key,
                requested_model=raw,
                resolved_model=resolved,
            )
            return resolved
        return raw

    def _compose_options(self, runtime_options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        runtime_options = runtime_options or {}
        merged = {
            "api_key": runtime_options.get("api_key", self._pipeline_defaults.get("api_key", self._provider_defaults.api_key)),
            "api_key_ref": runtime_options.get(
                "api_key_ref",
                self._pipeline_defaults.get("api_key_ref", getattr(self._provider_defaults, "api_key_ref", None)),
            ),
            "chat_base_url": runtime_options.get(
                "chat_base_url",
                runtime_options.get(
                    "base_url",
                    self._pipeline_defaults.get(
                        "chat_base_url",
                        self._pipeline_defaults.get("base_url", self._provider_defaults.chat_base_url),
                    ),
                ),
            ),
            "chat_model": runtime_options.get(
                "chat_model",
                runtime_options.get(
                    "model",
                    self._pipeline_defaults.get(
                        "chat_model",
                        self._pipeline_defaults.get("model", self._provider_defaults.chat_model),
                    ),
                ),
            ),
            "system_prompt": runtime_options.get("system_prompt", self._pipeline_defaults.get("system_prompt")),
            "instructions": runtime_options.get("instructions", self._pipeline_defaults.get("instructions")),
            "temperature": runtime_options.get(
                "temperature",
                self._pipeline_defaults.get("temperature", getattr(self._provider_defaults, "temperature", 0.7)),
            ),
            "max_tokens": runtime_options.get(
                "max_tokens",
                self._pipeline_defaults.get("max_tokens", getattr(self._provider_defaults, "max_tokens", None)),
            ),
            "timeout_sec": float(runtime_options.get("timeout_sec", self._pipeline_defaults.get("timeout_sec", self._default_timeout))),
            "tools": runtime_options.get("tools", self._pipeline_defaults.get("tools", [])),
        }

        if not (merged.get("chat_base_url") or "").strip():
            merged["chat_base_url"] = self.DEFAULT_CHAT_BASE_URL

        sys_p = str(merged.get("system_prompt") or "").strip()
        instr = str(merged.get("instructions") or "").strip()
        if not sys_p and not instr:
            try:
                merged["system_prompt"] = getattr(self._app_config.llm, "prompt", None)
            except Exception:
                merged["system_prompt"] = None

        return merged

    def _coalesce_messages(self, transcript: str, context: Dict[str, Any], merged: Dict[str, Any]) -> list[Dict[str, str]]:
        messages = context.get("messages")
        if messages:
            return messages

        conversation: list[Dict[str, str]] = []
        system_prompt = (merged.get("system_prompt") or merged.get("instructions") or context.get("system_prompt") or "").strip()
        if system_prompt:
            conversation.append({"role": "system", "content": system_prompt})

        prior = context.get("prior_messages") or []
        conversation.extend(prior)

        if transcript:
            conversation.append({"role": "user", "content": transcript})
        return conversation

    def _build_chat_payload(self, transcript: str, context: Dict[str, Any], merged: Dict[str, Any]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": merged["chat_model"],
            "messages": self._coalesce_messages(transcript, context, merged),
        }
        if merged.get("api_key_ref"):
            payload["api_key_ref"] = merged["api_key_ref"]
        if merged.get("temperature") is not None:
            payload["temperature"] = merged["temperature"]
        if merged.get("max_tokens") is not None:
            payload["max_tokens"] = merged["max_tokens"]
        return payload

    async def validate_connectivity(self, options: Dict[str, Any]) -> Dict[str, Any]:
        merged = self._compose_options(options or {})
        api_key = merged.get("api_key")
        if not api_key:
            return {"healthy": False, "error": "TELNYX_API_KEY not set", "details": {"component": self.component_key}}

        base = str(merged.get("chat_base_url") or self.DEFAULT_CHAT_BASE_URL).rstrip("/")
        endpoint = f"{base}/models"
        headers = _make_http_headers(str(api_key))

        try:
            # Connectivity validation is called during pipeline validation and may run
            # on short-lived adapter instances. Use a one-off session to avoid
            # "Unclosed client session" warnings at startup.
            async with aiohttp.ClientSession() as session:
                async with session.get(endpoint, headers=headers, timeout=aiohttp.ClientTimeout(total=10.0)) as resp:
                    if resp.status in (401, 403):
                        return {"healthy": False, "error": f"Auth failed (HTTP {resp.status})", "details": {"endpoint": endpoint}}
                    if resp.status >= 400:
                        body = await resp.text()
                        return {
                            "healthy": False,
                            "error": f"API error: HTTP {resp.status}",
                            "details": {"endpoint": endpoint, "body_preview": body[:200]},
                        }
                    return {"healthy": True, "error": None, "details": {"endpoint": endpoint, "protocol": "https"}}
        except Exception as exc:
            logger.debug("Telnyx connectivity validation failed", error=str(exc), exc_info=True)
            return {"healthy": False, "error": "Connection failed (see logs)", "details": {"endpoint": endpoint}}

    async def generate(
        self,
        call_id: str,
        transcript: str,
        context: Dict[str, Any],
        options: Dict[str, Any],
    ) -> str | LLMResponse:
        merged = self._compose_options(options)
        api_key = merged.get("api_key")
        if not api_key:
            raise RuntimeError("Telnyx LLM requires TELNYX_API_KEY")

        await self._ensure_session()
        assert self._session

        # Telnyx supports external inference providers (e.g. OpenAI) via Integration Secrets.
        # If a user selects an external model, require api_key_ref to avoid confusing 400s.
        requested_model = str(merged.get("chat_model") or "").strip()
        if requested_model.startswith("openai/") and not merged.get("api_key_ref"):
            raise RuntimeError(
                "Telnyx external models like 'openai/*' require 'api_key_ref' (Integration Secret identifier). "
                "Set providers.telnyx_llm.api_key_ref or choose a Telnyx-hosted model like 'meta-llama/*'."
            )

        # Normalize model ID: allow un-namespaced IDs by resolving to the account's model list.
        try:
            merged = dict(merged)
            merged["chat_model"] = await self._maybe_resolve_model_id(
                str(merged.get("chat_model") or ""),
                api_key=str(api_key),
                chat_base_url=str(merged.get("chat_base_url") or self.DEFAULT_CHAT_BASE_URL),
                api_key_ref=str(merged.get("api_key_ref") or "") or None,
            )
        except Exception:
            logger.debug("Telnyx model alias resolution failed", call_id=call_id, exc_info=True)

        payload = self._build_chat_payload(transcript, context, merged)

        tools_list = merged.get("tools")
        tool_schemas = []
        if tools_list and isinstance(tools_list, list):
            for tool_name in tools_list:
                tool = tool_registry.get(tool_name)
                if tool:
                    try:
                        from src.tools.base import ToolPhase

                        if getattr(tool.definition, "phase", ToolPhase.IN_CALL) != ToolPhase.IN_CALL:
                            logger.warning("Skipping non-in-call tool in pipeline schema", tool=tool_name)
                            continue
                    except Exception:
                        pass
                    tool_schemas.append(tool.definition.to_openai_schema())
                else:
                    logger.warning("Tool not found in registry", tool=tool_name)

        if tool_schemas:
            payload["tools"] = tool_schemas
            payload["tool_choice"] = "auto"
            # Telnyx currently rejects requests that combine function tools with max_tokens.
            # We keep max_tokens for non-tool requests, but strip it when tools are present.
            if payload.get("max_tokens") is not None:
                removed = payload.pop("max_tokens", None)
                logger.debug(
                    "Stripped max_tokens for Telnyx tool calling compatibility",
                    call_id=call_id,
                    model=payload.get("model"),
                    removed_max_tokens=removed,
                )

        base_url = str(merged.get("chat_base_url") or self.DEFAULT_CHAT_BASE_URL).rstrip("/")
        url = f"{base_url}/chat/completions"
        headers = _make_http_headers(str(api_key))

        logger.debug(
            "Telnyx chat completion request",
            call_id=call_id,
            host=_url_host(base_url),
            model=payload.get("model"),
            temperature=payload.get("temperature"),
            tools_count=len(payload.get("tools", [])),
        )

        retries = 1
        tools_stripped = False
        for attempt in range(retries + 1):
            timeout = aiohttp.ClientTimeout(total=float(merged["timeout_sec"]))
            async with self._session.post(url, json=payload, headers=headers, timeout=timeout) as response:
                body = await response.text()
                if response.status >= 400:
                    err_code = None
                    err_title = None
                    err_detail = None
                    try:
                        parsed = json.loads(body)
                        if isinstance(parsed, dict):
                            errors = parsed.get("errors") or []
                            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                                err_code = errors[0].get("code")
                                err_title = errors[0].get("title")
                                err_detail = errors[0].get("detail")
                    except Exception:
                        pass
                    logger.error(
                        "Telnyx chat completion failed",
                        call_id=call_id,
                        status=response.status,
                        model=payload.get("model"),
                        tools_count=len(payload.get("tools", [])),
                        error_code=err_code,
                        error_title=err_title,
                        error_detail=(str(err_detail)[:200] if err_detail else None),
                        body_preview=body[:128],
                    )
                    if (
                        not tools_stripped
                        and payload.get("tools")
                        and response.status in (400, 422)
                        and attempt < retries
                    ):
                        tools_stripped = True
                        payload = dict(payload)
                        payload.pop("tools", None)
                        payload.pop("tool_choice", None)
                        # max_tokens may have been stripped earlier; keep payload minimal on retry.
                        logger.warning(
                            "Tool calling failed; retrying chat completion without tools",
                            call_id=call_id,
                            status=response.status,
                        )
                        continue
                    response.raise_for_status()

                data = json.loads(body)
                choices = data.get("choices") or []
                if not choices:
                    logger.warning("Telnyx chat completion returned no choices", call_id=call_id)
                    return ""

                message = choices[0].get("message") or {}
                content = _strip_thinking(str(message.get("content", "") or ""))
                tool_calls = message.get("tool_calls") or []

                if tool_calls:
                    parsed_tool_calls = []
                    for tc in tool_calls:
                        try:
                            func = tc.get("function", {})
                            name = func.get("name")
                            args = func.get("arguments", "{}")
                            parsed_tool_calls.append(
                                {
                                    "id": tc.get("id"),
                                    "name": name,
                                    "parameters": json.loads(args),
                                    "type": tc.get("type", "function"),
                                }
                            )
                        except Exception as exc:
                            logger.warning("Failed to parse tool call", call_id=call_id, error=str(exc))
                    logger.info(
                        "Telnyx chat completion received with tools",
                        call_id=call_id,
                        model=payload.get("model"),
                        tool_calls=len(parsed_tool_calls),
                        preview=(content or "")[:80],
                    )
                    return LLMResponse(text=content or "", tool_calls=parsed_tool_calls, metadata=data.get("usage", {}))

                logger.info(
                    "Telnyx chat completion received",
                    call_id=call_id,
                    model=payload.get("model"),
                    preview=(content or "")[:80],
                )
                return LLMResponse(text=content or "", tool_calls=[], metadata=data.get("usage", {}))

        return ""
