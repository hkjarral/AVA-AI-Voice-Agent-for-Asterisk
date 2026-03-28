"""
Generic HTTP Lookup Tool - Pre-call CRM/API lookups.

Fetches enrichment data from external APIs (e.g., GoHighLevel, HubSpot)
and returns output variables for prompt injection.
"""

import os
import re
import json
import logging
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

from src.tools.http.path_utils import extract_path

import aiohttp

from src.tools.base import PreCallTool, ToolDefinition, ToolCategory, ToolPhase
from src.tools.context import PreCallContext
from src.tools.http.debug_trace import (
    build_var_snapshot,
    debug_enabled,
    extract_used_brace_vars,
    extract_used_env_vars,
    preview,
)

logger = logging.getLogger(__name__)


@dataclass
class HTTPLookupConfig:
    """Configuration for a generic HTTP lookup tool instance."""
    name: str
    enabled: bool = True
    phase: str = "pre_call"
    is_global: bool = False
    timeout_ms: int = 2000
    hold_audio_file: Optional[str] = None
    hold_audio_threshold_ms: int = 500
    
    # HTTP request configuration
    url: str = ""
    method: str = "GET"
    headers: Dict[str, str] = field(default_factory=dict)
    query_params: Dict[str, str] = field(default_factory=dict)
    body_template: Optional[str] = None
    
    # Response mapping (JMESPath-like simple dot notation for MVP)
    output_variables: Dict[str, str] = field(default_factory=dict)
    
    # Response limits
    max_response_size_bytes: int = 65536  # 64KB max


class GenericHTTPLookupTool(PreCallTool):
    """
    Generic HTTP lookup tool for pre-call enrichment.
    
    Configured via YAML, makes HTTP requests to fetch caller data
    and maps response fields to output variables for prompt injection.
    
    Example config:
    ```yaml
    tools:
      ghl_contact_lookup:
        kind: generic_http_lookup
        phase: pre_call
        enabled: true
        timeout_ms: 2000
        url: "https://rest.gohighlevel.com/v1/contacts/lookup"
        method: GET
        headers:
          Authorization: "Bearer ${GHL_API_KEY}"
        query_params:
          phone: "{caller_number}"
        output_variables:
          customer_name: "contacts[0].firstName + ' ' + contacts[0].lastName"
          customer_email: "contacts[0].email"
    ```
    """
    
    def __init__(self, config: HTTPLookupConfig):
        self.config = config
        self._definition = ToolDefinition(
            name=config.name,
            description=f"HTTP lookup: {config.name}",
            category=ToolCategory.BUSINESS,
            phase=ToolPhase.PRE_CALL,
            is_global=config.is_global,
            output_variables=list(config.output_variables.keys()),
            timeout_ms=config.timeout_ms,
            hold_audio_file=config.hold_audio_file,
            hold_audio_threshold_ms=config.hold_audio_threshold_ms,
        )
    
    @property
    def definition(self) -> ToolDefinition:
        return self._definition
    
    async def execute(self, context: PreCallContext) -> Dict[str, str]:
        """
        Execute the HTTP lookup and return output variables.
        
        Args:
            context: PreCallContext with caller info
        
        Returns:
            Dictionary of output_variable_name -> value (strings)
        """
        results: Dict[str, str] = {var: "" for var in self.config.output_variables.keys()}
        
        if not self.config.enabled:
            logger.debug(f"HTTP lookup tool disabled: {self.config.name}")
            return results
        
        if not self.config.url:
            logger.warning(f"HTTP lookup tool has no URL configured: {self.config.name}")
            return results
        
        try:
            started = time.monotonic()
            # Build request
            url = self._substitute_variables(self.config.url, context)
            headers = {
                k: self._substitute_variables(v, context)
                for k, v in self.config.headers.items()
            }
            params = {
                k: self._substitute_variables(v, context)
                for k, v in self.config.query_params.items()
            }
            
            body = None
            if self.config.body_template:
                body = self._substitute_variables(self.config.body_template, context)

            if debug_enabled(logger):
                used_brace = extract_used_brace_vars(
                    self.config.url,
                    *(self.config.headers or {}).values(),
                    *(self.config.query_params or {}).values(),
                    self.config.body_template,
                )
                used_env = extract_used_env_vars(
                    self.config.url,
                    *(self.config.headers or {}).values(),
                    *(self.config.query_params or {}).values(),
                    self.config.body_template,
                )
                ctx_values = {
                    "caller_number": getattr(context, "caller_number", None),
                    "called_number": getattr(context, "called_number", None),
                    "caller_name": getattr(context, "caller_name", None),
                    "context_name": getattr(context, "context_name", None),
                    "call_id": getattr(context, "call_id", None),
                    "campaign_id": getattr(context, "campaign_id", None),
                    "lead_id": getattr(context, "lead_id", None),
                }
                logger.debug(
                    "[HTTP_TOOL_TRACE] request_resolved pre_call tool=%s method=%s url=%s headers=%s params=%s body=%s vars=%s",
                    self.config.name,
                    self.config.method,
                    url,
                    headers,
                    params,
                    preview(body),
                    build_var_snapshot(
                        used_brace_vars=used_brace,
                        used_env_vars=used_env,
                        values=ctx_values,
                        env=os.environ,
                    ),
                )

            logger.info(f"Executing HTTP lookup: {self.config.name} {self.config.method} {self._redact_url(url)}")
            
            # Make request
            timeout = aiohttp.ClientTimeout(total=self.config.timeout_ms / 1000.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method=self.config.method,
                    url=url,
                    headers=headers,
                    params=params,
                    data=body,
                ) as response:
                    if response.status != 200:
                        logger.warning(f"HTTP lookup returned non-200: {self.config.name} status={response.status}")
                        if debug_enabled(logger):
                            elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                            body_preview = ""
                            try:
                                body_preview = preview(await response.content.read(4096))
                            except Exception as e:
                                body_preview = f"<failed to read body: {e}>"
                            logger.debug(
                                "[HTTP_TOOL_TRACE] response_non_200 pre_call tool=%s status=%s elapsed_ms=%s body_preview=%s",
                                self.config.name,
                                response.status,
                                elapsed_ms,
                                body_preview,
                            )
                        return results

                    # Check declared response size (best-effort) but always enforce actual size below.
                    content_length = response.headers.get('Content-Length')
                    if content_length:
                        try:
                            if int(content_length) > self.config.max_response_size_bytes:
                                logger.warning(
                                    "Response too large, skipping: %s size=%s max=%s",
                                    self.config.name,
                                    content_length,
                                    self.config.max_response_size_bytes,
                                )
                                return results
                        except Exception:
                            pass

                    # Read body with enforced size limit (do not trust Content-Length header).
                    body_bytes = b""
                    try:
                        max_bytes = int(self.config.max_response_size_bytes or 0)
                        if max_bytes <= 0:
                            logger.warning(
                                "Invalid max_response_size_bytes for %s: %s",
                                self.config.name,
                                self.config.max_response_size_bytes,
                            )
                            return results

                        total = 0
                        chunks: list[bytes] = []
                        async for chunk in response.content.iter_chunked(8192):
                            if not chunk:
                                continue
                            total += len(chunk)
                            if total > max_bytes:
                                logger.warning(
                                    "Response too large, skipping: %s max=%s",
                                    self.config.name,
                                    max_bytes,
                                )
                                return results
                            chunks.append(chunk)

                        body_bytes = b"".join(chunks)
                        charset = getattr(response, "charset", None) or "utf-8"
                        data = json.loads(body_bytes.decode(charset, errors="replace"))
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse JSON response: {self.config.name} error={e}")
                        if debug_enabled(logger):
                            elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                            logger.debug(
                                "[HTTP_TOOL_TRACE] response_invalid_json pre_call tool=%s status=%s elapsed_ms=%s body_len=%s body_preview=%s",
                                self.config.name,
                                response.status,
                                elapsed_ms,
                                len(body_bytes or b""),
                                preview(body_bytes),
                            )
                        return results
                    except Exception as e:
                        logger.warning(f"Failed to read response: {self.config.name} error={e}")
                        if debug_enabled(logger):
                            elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                            logger.debug(
                                "[HTTP_TOOL_TRACE] response_read_failed pre_call tool=%s status=%s elapsed_ms=%s error=%s body_len=%s body_preview=%s",
                                self.config.name,
                                getattr(response, "status", None),
                                elapsed_ms,
                                str(e),
                                len(body_bytes or b""),
                                preview(body_bytes),
                            )
                        return results
                    
                    # Extract output variables
                    results = self._extract_output_variables(data)

                    if debug_enabled(logger):
                        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                        logger.debug(
                            "[HTTP_TOOL_TRACE] response_ok pre_call tool=%s status=%s elapsed_ms=%s body_len=%s body_preview=%s outputs=%s",
                            self.config.name,
                            response.status,
                            elapsed_ms,
                            len(body_bytes or b""),
                            preview(body_bytes),
                            results,
                        )
                    
                    logger.info(f"HTTP lookup completed: {self.config.name} status={response.status} keys={list(results.keys())}")
        
        except aiohttp.ClientError as e:
            logger.warning(f"HTTP lookup request failed: {self.config.name} error={e}")
        except Exception as e:
            logger.error(f"HTTP lookup unexpected error: {self.config.name} error={e}", exc_info=True)
        
        return results
    
    def _substitute_variables(self, template: str, context: PreCallContext) -> str:
        """
        Substitute variables in template string.
        
        Supports:
        - {caller_number} - Caller's phone number
        - {called_number} - DID that was called
        - {caller_name} - Caller ID name
        - {context_name} - AI context name
        - {call_id} - Call identifier
        - ${ENV_VAR} - Environment variable
        """
        result = template
        
        # Context variables
        replacements = {
            "{caller_number}": context.caller_number or "",
            "{called_number}": context.called_number or "",
            "{caller_name}": context.caller_name or "",
            "{context_name}": context.context_name or "",
            "{call_id}": context.call_id or "",
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
    
    def _extract_output_variables(self, data: Any) -> Dict[str, str]:
        """
        Extract output variables from JSON response using dot notation.

        Supports simple paths, numeric indices, and [*] wildcards.
        List/dict results are JSON-serialized; scalars use str().
        """
        results = {}

        for var_name, path in self.config.output_variables.items():
            try:
                value = self._extract_path(data, path)
                if value is None:
                    results[var_name] = ""
                elif isinstance(value, (list, dict)):
                    results[var_name] = json.dumps(value)
                elif isinstance(value, str):
                    results[var_name] = value
                else:
                    results[var_name] = str(value)
            except Exception as e:
                logger.debug(
                    "Failed to extract output variable '%s' (path=%s): %s",
                    var_name,
                    path,
                    str(e),
                    exc_info=True,
                )
                results[var_name] = ""

        return results

    def _extract_path(self, data: Any, path: str) -> Any:
        """Extract value from nested data using dot notation path.

        Delegates to the shared ``extract_path`` utility which supports
        simple keys, numeric indices, and ``[*]`` wildcards.
        """
        return extract_path(data, path)
    
    def _redact_url(self, url: str) -> str:
        """Redact sensitive parts of URL for logging."""
        # Redact API keys in query params
        redacted = re.sub(r'(api_key|apikey|key|token|auth)=([^&]+)', r'\1=***', url, flags=re.IGNORECASE)
        return redacted


def create_http_lookup_tool(name: str, config_dict: Dict[str, Any]) -> GenericHTTPLookupTool:
    """
    Factory function to create an HTTP lookup tool from YAML config.
    
    Args:
        name: Tool name from YAML key
        config_dict: Tool configuration dictionary
    
    Returns:
        Configured GenericHTTPLookupTool instance
    """
    config = HTTPLookupConfig(
        name=name,
        enabled=config_dict.get('enabled', True),
        phase=config_dict.get('phase', 'pre_call'),
        is_global=config_dict.get('is_global', False),
        timeout_ms=config_dict.get('timeout_ms', 2000),
        hold_audio_file=config_dict.get('hold_audio_file'),
        hold_audio_threshold_ms=config_dict.get('hold_audio_threshold_ms', 500),
        url=config_dict.get('url', ''),
        method=config_dict.get('method', 'GET'),
        headers=config_dict.get('headers', {}),
        query_params=config_dict.get('query_params', {}),
        body_template=config_dict.get('body_template'),
        output_variables=config_dict.get('output_variables', {}),
        max_response_size_bytes=config_dict.get('max_response_size_bytes', 65536),
    )
    
    return GenericHTTPLookupTool(config)
