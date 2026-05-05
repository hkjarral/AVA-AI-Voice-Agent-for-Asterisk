"""
Generic Webhook Tool - Post-call webhook notifications.

Sends call data to external systems after call ends (fire-and-forget).
"""

import asyncio
import os
import re
import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

import aiohttp


# --- Response body capture knob (used by call-history tracking) ---
# Per-tool YAML override: ``response_body_max_chars`` on the tool config.
# Falls back to env ``CALL_HISTORY_RESPONSE_BODY_MAX_CHARS`` (default 512).
# Set to 0 to disable body capture entirely (status code + error only).
_DEFAULT_RESPONSE_BODY_MAX_CHARS = 512


def _resolve_body_max_chars(per_tool: Optional[int]) -> int:
    if per_tool is not None:
        try:
            return max(0, int(per_tool))
        except (TypeError, ValueError):
            pass
    raw = os.environ.get("CALL_HISTORY_RESPONSE_BODY_MAX_CHARS")
    if raw is not None and raw != "":
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return _DEFAULT_RESPONSE_BODY_MAX_CHARS


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

from src.tools.base import PostCallTool, ToolDefinition, ToolCategory, ToolPhase
from src.tools.context import PostCallContext
from src.tools.http.debug_trace import (
    build_var_snapshot,
    debug_enabled,
    extract_used_brace_vars,
    extract_used_env_vars,
    preview,
)

logger = logging.getLogger(__name__)

try:
    import openai  # type: ignore
except ImportError:  # pragma: no cover
    openai = None


@dataclass
class WebhookConfig:
    """Configuration for a generic webhook tool instance."""
    name: str
    enabled: bool = True
    phase: str = "post_call"
    is_global: bool = False
    timeout_ms: int = 5000
    
    # HTTP request configuration
    url: str = ""
    method: str = "POST"
    headers: Dict[str, str] = field(default_factory=dict)
    
    # Payload template (JSON string with variable substitution)
    payload_template: Optional[str] = None
    
    # Default content type
    content_type: str = "application/json"
    
    # Summary generation (optional - uses LLM to summarize transcript)
    generate_summary: bool = False
    summary_max_words: int = 100
    # Custom system prompt template for the summarizer. If set, ``{max_words}``
    # is interpolated; otherwise a sensible default (caller-perspective recap)
    # is used. Useful for branding or perspective changes (e.g. "We discussed…"
    # instead of "The caller asked about…").
    summary_prompt: Optional[str] = None

    # Per-tool override for response body capture in call history.
    # None → use CALL_HISTORY_RESPONSE_BODY_MAX_CHARS env (default 512).
    # 0 → don't capture any response body (status code + error only).
    response_body_max_chars: Optional[int] = None


class GenericWebhookTool(PostCallTool):
    """
    Generic webhook tool for post-call notifications.
    
    Configured via YAML, sends call data to external endpoints
    after the call ends. Fire-and-forget (no retry).
    
    Example config:
    ```yaml
    tools:
      n8n_webhook:
        kind: generic_webhook
        phase: post_call
        enabled: true
        is_global: true
        timeout_ms: 5000
        url: "https://n8n.example.com/webhook/call-completed"
        method: POST
        headers:
          Content-Type: "application/json"
          Authorization: "Bearer ${N8N_API_KEY}"
        payload_template: |
          {
            "schema_version": 1,
            "event_type": "call_completed",
            "call_id": "{call_id}",
            "caller_number": "{caller_number}",
            "caller_name": "{caller_name}",
            "call_duration": {call_duration},
            "call_outcome": "{call_outcome}",
            "transcript": {transcript_json},
            "summary": "{summary}",
            "context": "{context_name}",
            "provider": "{provider}",
            "timestamp": "{call_end_time}"
          }
    ```
    """
    
    def __init__(self, config: WebhookConfig):
        self.config = config
        self._definition = ToolDefinition(
            name=config.name,
            description=f"Webhook: {config.name}",
            category=ToolCategory.BUSINESS,
            phase=ToolPhase.POST_CALL,
            is_global=config.is_global,
            timeout_ms=config.timeout_ms,
        )
        # Diagnostics for call-history tracking — populated at every exit path of
        # execute() and keyed by ``call_id``. The tool registry holds a single
        # instance and post-call tools fire concurrently across calls, so a
        # per-instance ``self._last_result`` would race; the engine reads the
        # entry by call_id immediately after ``execute()`` returns and pops it.
        self._last_results: Dict[str, Dict[str, Any]] = {}

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def get_last_result(self, call_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Return diagnostics from the last execute() call (HTTP status, body preview, error).

        ``call_id`` selects the per-call diagnostics entry. Pops on read so the
        store does not grow unboundedly across the lifetime of the worker.
        ``None`` ``call_id`` returns ``None`` (no fallback to global state).
        """
        if not call_id:
            return None
        return self._last_results.pop(call_id, None)

    def _record_result(
        self,
        *,
        call_id: str,
        status: str,
        started_iso: str,
        started_monotonic: float,
        http_status: Optional[int] = None,
        body_text: str = "",
        error_message: Optional[str] = None,
    ) -> None:
        """Store execution diagnostics for ``call_id`` in ``self._last_results``."""
        max_chars = _resolve_body_max_chars(self.config.response_body_max_chars)
        response_summary: Optional[str] = None
        if max_chars > 0 and body_text:
            response_summary = body_text if len(body_text) <= max_chars else body_text[:max_chars] + "…"
        finished_iso = _now_iso()
        duration_ms = round((time.monotonic() - started_monotonic) * 1000, 2)
        if not call_id:
            # Engine should always pass call_id; if missing, drop diagnostics
            # rather than overwrite a sibling call's entry.
            logger.debug(
                "_record_result called without call_id; dropping diagnostics for webhook %s status=%s",
                self.config.name,
                status,
            )
            return
        self._last_results[call_id] = {
            "status": status,
            "http_status": http_status,
            "response_summary": response_summary,
            "error_message": (error_message[:500] if error_message else None),
            "started_at": started_iso,
            "finished_at": finished_iso,
            "duration_ms": duration_ms,
        }

    async def execute(self, context: PostCallContext) -> None:
        """
        Execute the webhook (fire-and-forget).

        Args:
            context: PostCallContext with comprehensive call data
        """
        started_iso = _now_iso()
        started = time.monotonic()
        # Diagnostics keyed by call_id (no shared instance state across calls).
        call_id = getattr(context, "call_id", None) or ""

        if not self.config.enabled:
            logger.debug(f"Webhook tool disabled: {self.config.name}")
            self._record_result(
                call_id=call_id,
                status="skipped",
                started_iso=started_iso,
                started_monotonic=started,
                error_message="tool disabled",
            )
            return

        if not self.config.url:
            logger.warning(f"Webhook tool has no URL configured: {self.config.name}")
            self._record_result(
                call_id=call_id,
                status="skipped",
                started_iso=started_iso,
                started_monotonic=started,
                error_message="no URL configured",
            )
            return

        try:
            # Generate summary if requested and not already present
            if self.config.generate_summary and not context.summary:
                context.summary = await self._generate_summary(context)
            
            # Build request
            url = self._substitute_variables(self.config.url, context)
            headers = {
                k: self._substitute_variables(v, context)
                for k, v in self.config.headers.items()
            }
            
            # Ensure content-type is set
            if 'Content-Type' not in headers and 'content-type' not in headers:
                headers['Content-Type'] = self.config.content_type
            
            # Build payload
            payload = None
            if self.config.payload_template:
                payload = self._build_payload(context)
            else:
                # Default payload using context's to_payload_dict
                payload = json.dumps(context.to_payload_dict())

            if debug_enabled(logger):
                used_brace = extract_used_brace_vars(
                    self.config.url,
                    *(self.config.headers or {}).values(),
                    self.config.payload_template,
                )
                used_env = extract_used_env_vars(
                    self.config.url,
                    *(self.config.headers or {}).values(),
                    self.config.payload_template,
                )
                values = context.to_payload_dict()
                logger.debug(
                    "[HTTP_TOOL_TRACE] request_resolved post_call tool=%s method=%s url=%s headers=%s payload=%s vars=%s call_id=%s",
                    self.config.name,
                    self.config.method,
                    url,
                    headers,
                    preview(payload),
                    build_var_snapshot(
                        used_brace_vars=used_brace,
                        used_env_vars=used_env,
                        values=values,
                        env=os.environ,
                    ),
                    getattr(context, "call_id", None),
                )
            
            logger.info(f"Sending webhook: {self.config.name} {self.config.method} {self._redact_url(url)}")
            
            # Make request (fire-and-forget)
            timeout = aiohttp.ClientTimeout(total=self.config.timeout_ms / 1000.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method=self.config.method,
                    url=url,
                    headers=headers,
                    data=payload,
                ) as response:
                    status = response.status
                    body_text = ""
                    try:
                        body_text = await response.text()
                    except Exception as e:
                        logger.debug(f"Failed to read response body: {e}")
                    
                    if 200 <= status < 300:
                        logger.info(f"Webhook sent successfully: {self.config.name} status={status}")
                        if debug_enabled(logger):
                            elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                            logger.debug(
                                "[HTTP_TOOL_TRACE] response_ok post_call tool=%s status=%s elapsed_ms=%s body_preview=%s call_id=%s",
                                self.config.name,
                                status,
                                elapsed_ms,
                                preview(body_text),
                                getattr(context, "call_id", None),
                            )
                        self._record_result(
                            call_id=call_id,
                            status="ok",
                            started_iso=started_iso,
                            started_monotonic=started,
                            http_status=status,
                            body_text=body_text,
                        )
                    else:
                        # Log but don't fail (fire-and-forget)
                        body_preview = (body_text[:200] if body_text else "")
                        logger.warning(
                            f"Webhook returned non-2xx: {self.config.name} status={status} body={body_preview}"
                        )
                        if debug_enabled(logger):
                            elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                            logger.debug(
                                "[HTTP_TOOL_TRACE] response_non_2xx post_call tool=%s status=%s elapsed_ms=%s body_preview=%s call_id=%s",
                                self.config.name,
                                status,
                                elapsed_ms,
                                preview(body_text),
                                getattr(context, "call_id", None),
                            )
                        self._record_result(
                            call_id=call_id,
                            status="error",
                            started_iso=started_iso,
                            started_monotonic=started,
                            http_status=status,
                            body_text=body_text,
                            error_message=f"HTTP {status}",
                        )

        except (asyncio.TimeoutError, aiohttp.ServerTimeoutError) as e:
            # Explicit timeout exception types — covers asyncio.TimeoutError
            # raised by aiohttp.ClientTimeout and SocketTimeoutError aliases.
            logger.warning(f"Webhook timed out: {self.config.name} error={e}")
            self._record_result(
                call_id=call_id,
                status="timeout",
                started_iso=started_iso,
                started_monotonic=started,
                error_message=f"{e.__class__.__name__}: {e}",
            )
        except aiohttp.ClientError as e:
            logger.warning(f"Webhook request failed: {self.config.name} error={e}")
            self._record_result(
                call_id=call_id,
                status="error",
                started_iso=started_iso,
                started_monotonic=started,
                error_message=f"{e.__class__.__name__}: {e}",
            )
        except Exception as e:
            logger.error(f"Webhook unexpected error: {self.config.name} error={e}", exc_info=True)
            self._record_result(
                call_id=call_id,
                status="error",
                started_iso=started_iso,
                started_monotonic=started,
                error_message=f"{e.__class__.__name__}: {e}",
            )
    
    def _build_payload(self, context: PostCallContext) -> str:
        """
        Build payload from template with variable substitution.
        """
        template = self.config.payload_template or "{}"
        
        # Get payload dict from context
        payload_vars = context.to_payload_dict()
        
        # Add schema_version
        payload_vars["schema_version"] = "1"
        
        # Add summary_json as separate variable (keeps transcript_json intact)
        if context.summary:
            payload_vars["summary_json"] = json.dumps(context.summary)
        else:
            payload_vars["summary_json"] = json.dumps("")
        
        # Substitute variables
        result = template
        
        # Simple variable substitution: {var_name}
        for key, value in payload_vars.items():
            placeholder = "{" + key + "}"
            if placeholder in result:
                # For JSON fields (ending with _json), don't quote
                if key.endswith("_json"):
                    result = result.replace(placeholder, str(value))
                else:
                    # Escape for JSON string
                    escaped = json.dumps(str(value))[1:-1]  # Remove outer quotes
                    result = result.replace(placeholder, escaped)
        
        # Environment variables: ${VAR_NAME}
        env_pattern = r'\$\{([A-Z_][A-Z0-9_]*)\}'
        def env_replacer(match):
            var_name = match.group(1)
            value = os.environ.get(var_name, "")
            return json.dumps(value)[1:-1]  # Escape for JSON
        
        result = re.sub(env_pattern, env_replacer, result)
        
        return result
    
    def _substitute_variables(self, template: str, context: PostCallContext) -> str:
        """
        Substitute variables in URL/headers.
        """
        result = template
        
        # Context variables
        replacements = {
            "{call_id}": context.call_id or "",
            "{caller_number}": context.caller_number or "",
            "{called_number}": context.called_number or "",
            "{caller_name}": context.caller_name or "",
            "{context_name}": context.context_name or "",
            "{provider}": context.provider or "",
            "{call_direction}": context.call_direction or "",
            "{campaign_id}": context.campaign_id or "",
            "{lead_id}": context.lead_id or "",
        }
        
        for placeholder, value in replacements.items():
            result = result.replace(placeholder, value)
        
        # Environment variables: ${VAR_NAME}
        env_pattern = r'\$\{([A-Z_][A-Z0-9_]*)\}'
        def env_replacer(match):
            var_name = match.group(1)
            return os.environ.get(var_name, "")
        
        result = re.sub(env_pattern, env_replacer, result)
        
        return result
    
    def _redact_url(self, url: str) -> str:
        """Redact sensitive parts of URL for logging."""
        redacted = re.sub(r'(api_key|apikey|key|token|auth)=([^&]+)', r'\1=***', url, flags=re.IGNORECASE)
        return redacted
    
    def _resolve_summary_prompt(self, max_words: int) -> str:
        """Build the summarizer system prompt, falling back to the default if a
        custom ``summary_prompt`` raises (literal braces, unknown placeholders).
        """
        default_prompt = (
            f"You are a call summarizer. Summarize the following phone "
            f"conversation in {max_words} words or less. Focus on: the "
            f"caller's main request, key information exchanged, and the "
            f"outcome. Be concise and factual."
        )
        custom = self.config.summary_prompt
        if not custom:
            return default_prompt
        try:
            return custom.format(max_words=max_words)
        except (KeyError, IndexError, ValueError) as e:
            # Operator supplied a prompt with literal `{` / `}` (JSON snippet)
            # or a placeholder other than `{max_words}`. Falling back keeps
            # summary generation working instead of silently returning "".
            logger.warning(
                "summary_prompt format failed for webhook %s (%s); using default. "
                "Use {{ }} to escape literal braces and only the {max_words} placeholder.",
                self.config.name,
                e,
            )
            return default_prompt

    async def _generate_summary(self, context: PostCallContext) -> str:
        """
        Generate a concise summary of the conversation using OpenAI.
        
        Returns:
            Summary string, or empty string if generation fails.
        """
        if not context.conversation_history:
            return ""
        
        try:
            if openai is None:
                logger.warning(f"Cannot generate summary - openai package not installed: {self.config.name}")
                return ""
            
            # Format transcript for summarization
            transcript_text = "\n".join([
                f"{msg.get('role', 'unknown')}: {msg.get('content', '')}"
                for msg in context.conversation_history
            ])
            
            if not transcript_text.strip():
                return ""
            
            # Use OpenAI to generate summary
            api_key = os.environ.get('OPENAI_API_KEY')
            if not api_key:
                logger.warning(f"Cannot generate summary - OPENAI_API_KEY not set: {self.config.name}")
                return ""
            
            client = openai.AsyncOpenAI(api_key=api_key)
            
            max_words = self.config.summary_max_words
            
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": self._resolve_summary_prompt(max_words),
                    },
                    {
                        "role": "user",
                        "content": transcript_text
                    }
                ],
                max_tokens=200,
                temperature=0.3,
            )
            
            summary = response.choices[0].message.content.strip() if response.choices else ""
            logger.info(f"Generated summary for webhook: {self.config.name} length={len(summary)}")
            return summary
            
        except Exception as e:
            logger.warning(f"Failed to generate summary: {self.config.name} error={e}")
            return ""


def create_webhook_tool(name: str, config_dict: Dict[str, Any]) -> GenericWebhookTool:
    """
    Factory function to create a webhook tool from YAML config.
    
    Args:
        name: Tool name from YAML key
        config_dict: Tool configuration dictionary
    
    Returns:
        Configured GenericWebhookTool instance
    """
    config = WebhookConfig(
        name=name,
        enabled=config_dict.get('enabled', True),
        phase=config_dict.get('phase', 'post_call'),
        is_global=config_dict.get('is_global', False),
        timeout_ms=config_dict.get('timeout_ms', 5000),
        url=config_dict.get('url', ''),
        method=config_dict.get('method', 'POST'),
        headers=config_dict.get('headers', {}),
        payload_template=config_dict.get('payload_template'),
        content_type=config_dict.get('content_type', 'application/json'),
        generate_summary=config_dict.get('generate_summary', False),
        summary_max_words=config_dict.get('summary_max_words', 100),
        summary_prompt=config_dict.get('summary_prompt'),
        response_body_max_chars=config_dict.get('response_body_max_chars'),
    )
    
    return GenericWebhookTool(config)
