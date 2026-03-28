"""
In-Call HTTP Lookup Tool - AI-invoked HTTP requests during conversation.

Allows AI to make HTTP requests mid-call to fetch data (e.g., check availability,
lookup order status) and receive results to inform the conversation.
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

from src.tools.base import Tool, ToolDefinition, ToolCategory, ToolPhase, ToolParameter
from src.tools.context import ToolExecutionContext
from src.tools.http.debug_trace import (
    build_var_snapshot,
    debug_enabled,
    extract_used_brace_vars,
    extract_used_env_vars,
    preview,
)

logger = logging.getLogger(__name__)


@dataclass
class InCallHTTPConfig:
    """Configuration for an in-call HTTP lookup tool instance."""
    name: str
    description: str = ""
    enabled: bool = True
    is_global: bool = False
    timeout_ms: int = 5000
    
    # Hold audio (played if request exceeds threshold)
    hold_audio_file: Optional[str] = None
    hold_audio_threshold_ms: int = 500
    
    # HTTP request configuration
    url: str = ""
    method: str = "POST"
    headers: Dict[str, str] = field(default_factory=dict)
    query_params: Dict[str, str] = field(default_factory=dict)
    body_template: Optional[str] = None
    
    # AI-provided parameters (registered with provider for function calling)
    parameters: List[Dict[str, Any]] = field(default_factory=list)
    
    # Response handling
    output_variables: Dict[str, str] = field(default_factory=dict)  # var_name -> JSON path
    return_raw_json: bool = False  # If True, return full JSON to AI
    
    # Response limits
    max_response_size_bytes: int = 65536  # 64KB max
    
    # Error handling
    error_message: str = "I'm sorry, I couldn't retrieve that information right now."


class InCallHTTPTool(Tool):
    """
    In-call HTTP lookup tool for AI-invoked requests during conversation.
    
    Configured via YAML, allows AI to make HTTP requests mid-call and receive
    results. Supports both selected output variables and raw JSON responses.
    
    Example config:
    ```yaml
    in_call_tools:
      check_appointment:
        kind: in_call_http_lookup
        enabled: true
        description: "Check if an appointment slot is available"
        timeout_ms: 5000
        hold_audio_file: "custom/please-wait"
        hold_audio_threshold_ms: 500
        url: "https://api.example.com/appointments/check"
        method: POST
        headers:
          Authorization: "Bearer ${API_KEY}"
          Content-Type: "application/json"
        body_template: |
          {
            "caller_number": "{caller_number}",
            "date": "{date}",
            "time": "{time}"
          }
        parameters:
          - name: date
            type: string
            description: "Appointment date in YYYY-MM-DD format"
            required: true
          - name: time
            type: string
            description: "Appointment time in HH:MM format"
            required: true
        output_variables:
          available: "available"
          next_available_slot: "next_slot"
        return_raw_json: false
        error_message: "I couldn't check the appointment availability. Would you like me to try again?"
    ```
    """
    
    def __init__(self, config: InCallHTTPConfig):
        self.config = config
        
        # Convert config parameters to ToolParameter objects
        tool_params = []
        for p in config.parameters:
            tool_params.append(ToolParameter(
                name=p.get('name', ''),
                type=p.get('type', 'string'),
                description=p.get('description', ''),
                required=p.get('required', False),
                enum=p.get('enum'),
                default=p.get('default'),
            ))
        
        self._definition = ToolDefinition(
            name=config.name,
            description=config.description or f"HTTP lookup: {config.name}",
            category=ToolCategory.BUSINESS,
            phase=ToolPhase.IN_CALL,
            is_global=config.is_global,
            parameters=tool_params,
            timeout_ms=config.timeout_ms,
            hold_audio_file=config.hold_audio_file,
            hold_audio_threshold_ms=config.hold_audio_threshold_ms,
        )
    
    @property
    def definition(self) -> ToolDefinition:
        return self._definition
    
    async def execute(
        self,
        parameters: Dict[str, Any],
        context: ToolExecutionContext
    ) -> Dict[str, Any]:
        """
        Execute the HTTP lookup and return results to AI.
        
        Args:
            parameters: AI-provided parameters
            context: ToolExecutionContext with call info
        
        Returns:
            Dictionary with:
            - status: "success" | "failed" | "error"
            - message: Human-readable message for AI
            - data: Output variables or raw JSON (if return_raw_json=True)
        """
        if not self.config.enabled:
            logger.debug(f"In-call HTTP tool disabled: {self.config.name}")
            return {
                "status": "failed",
                "message": self.config.error_message,
            }
        
        if not self.config.url:
            logger.warning(f"In-call HTTP tool has no URL configured: {self.config.name}")
            return {
                "status": "error",
                "message": self.config.error_message,
            }
        
        try:
            started = time.monotonic()
            # Build substitution context (context vars + pre-call results + AI params)
            sub_context = await self._build_substitution_context(parameters, context)
            
            # Build request
            url = self._substitute_variables(self.config.url, sub_context)
            headers = {
                k: self._substitute_variables(v, sub_context)
                for k, v in self.config.headers.items()
            }
            query_params = {
                k: self._substitute_variables(v, sub_context)
                for k, v in self.config.query_params.items()
            }
            
            body = None
            json_body = None
            if self.config.body_template:
                body_str = self._substitute_variables(self.config.body_template, sub_context)
                # Try to parse as JSON for proper Content-Type handling
                try:
                    json_body = json.loads(body_str)
                except json.JSONDecodeError:
                    body = body_str

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
                logger.debug(
                    "[HTTP_TOOL_TRACE] request_resolved in_call tool=%s method=%s url=%s headers=%s params=%s body=%s json_body=%s vars=%s call_id=%s",
                    self.config.name,
                    self.config.method,
                    url,
                    headers,
                    query_params,
                    preview(body),
                    preview(json.dumps(json_body)) if json_body is not None else "",
                    build_var_snapshot(
                        used_brace_vars=used_brace,
                        used_env_vars=used_env,
                        values=sub_context,
                        env=os.environ,
                    ),
                    context.call_id,
                )
            
            logger.info(
                f"Executing in-call HTTP tool: {self.config.name}",
                extra={
                    "method": self.config.method,
                    "url": self._redact_url(url),
                    "call_id": context.call_id,
                }
            )
            
            # Make request
            timeout = aiohttp.ClientTimeout(total=self.config.timeout_ms / 1000.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                request_kwargs = {
                    "method": self.config.method,
                    "url": url,
                    "headers": headers,
                    "params": query_params if query_params else None,
                }
                
                if json_body is not None:
                    request_kwargs["json"] = json_body
                elif body is not None:
                    request_kwargs["data"] = body
                
                async with session.request(**request_kwargs) as response:
                    # Check response size
                    content_length = response.headers.get('Content-Length')
                    if content_length and int(content_length) > self.config.max_response_size_bytes:
                        logger.warning(
                            f"Response too large: {self.config.name}",
                            extra={"size": content_length, "max": self.config.max_response_size_bytes}
                        )
                        return {
                            "status": "error",
                            "message": self.config.error_message,
                        }
                    
                    if response.status != 200:
                        logger.warning(
                            f"In-call HTTP tool returned non-200: {self.config.name}",
                            extra={"status": response.status, "call_id": context.call_id}
                        )
                        if debug_enabled(logger):
                            elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                            body_preview = ""
                            try:
                                body_preview = preview(await response.text())
                            except Exception as e:
                                body_preview = f"<failed to read body: {e}>"
                            logger.debug(
                                "[HTTP_TOOL_TRACE] response_non_200 in_call tool=%s status=%s elapsed_ms=%s body_preview=%s call_id=%s",
                                self.config.name,
                                response.status,
                                elapsed_ms,
                                body_preview,
                                context.call_id,
                            )
                        return {
                            "status": "failed",
                            "message": self.config.error_message,
                        }
                    
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
                            return {
                                "status": "error",
                                "message": self.config.error_message,
                            }

                        total = 0
                        chunks: list[bytes] = []
                        async for chunk in response.content.iter_chunked(8192):
                            if not chunk:
                                continue
                            total += len(chunk)
                            if total > max_bytes:
                                logger.warning(
                                    "Response too large: %s max=%s",
                                    self.config.name,
                                    max_bytes,
                                )
                                if debug_enabled(logger):
                                    elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                                    logger.debug(
                                        "[HTTP_TOOL_TRACE] response_too_large in_call tool=%s status=%s elapsed_ms=%s body_len=%s max=%s call_id=%s",
                                        self.config.name,
                                        getattr(response, "status", None),
                                        elapsed_ms,
                                        total,
                                        max_bytes,
                                        context.call_id,
                                    )
                                return {
                                    "status": "error",
                                    "message": self.config.error_message,
                                }
                            chunks.append(chunk)

                        body_bytes = b"".join(chunks)
                        charset = getattr(response, "charset", None) or "utf-8"
                        body_text = body_bytes.decode(charset, errors="replace")
                        data = json.loads(body_text)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse JSON response: {self.config.name} error={e}")
                        if debug_enabled(logger):
                            elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                            logger.debug(
                                "[HTTP_TOOL_TRACE] response_invalid_json in_call tool=%s elapsed_ms=%s body_len=%s body_preview=%s call_id=%s error=%s",
                                self.config.name,
                                elapsed_ms,
                                len(body_bytes or b""),
                                preview(body_bytes),
                                context.call_id,
                                str(e),
                            )
                        return {
                            "status": "error",
                            "message": self.config.error_message,
                        }
                    except Exception as e:
                        logger.warning(f"Failed to read response: {self.config.name} error={e}")
                        if debug_enabled(logger):
                            elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                            logger.debug(
                                "[HTTP_TOOL_TRACE] response_read_failed in_call tool=%s status=%s elapsed_ms=%s error=%s body_len=%s body_preview=%s call_id=%s",
                                self.config.name,
                                getattr(response, "status", None),
                                elapsed_ms,
                                str(e),
                                len(body_bytes or b""),
                                preview(body_bytes),
                                context.call_id,
                            )
                        return {
                            "status": "error",
                            "message": self.config.error_message,
                        }

                    if debug_enabled(logger):
                        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                        logger.debug(
                            "[HTTP_TOOL_TRACE] response_ok in_call tool=%s status=%s elapsed_ms=%s body_preview=%s call_id=%s",
                            self.config.name,
                            response.status,
                            elapsed_ms,
                            preview(body_text),
                            context.call_id,
                        )
                    
                    # Build result
                    result = {
                        "status": "success",
                    }
                    
                    if self.config.return_raw_json:
                        # Return full JSON to AI
                        result["data"] = data
                        result["message"] = f"Retrieved data successfully."
                    else:
                        # Extract output variables
                        extracted = self._extract_output_variables(data)
                        result["data"] = extracted
                        # Build human-readable message
                        result["message"] = self._build_result_message(extracted)

                        if debug_enabled(logger):
                            elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                            logger.debug(
                                "[HTTP_TOOL_TRACE] outputs in_call tool=%s elapsed_ms=%s outputs=%s call_id=%s",
                                self.config.name,
                                elapsed_ms,
                                extracted,
                                context.call_id,
                            )
                    
                    logger.info(
                        f"In-call HTTP tool completed: {self.config.name}",
                        extra={
                            "status": response.status,
                            "call_id": context.call_id,
                            "output_keys": list(result.get("data", {}).keys()),
                        }
                    )
                    
                    return result
        
        except aiohttp.ClientError as e:
            logger.warning(f"In-call HTTP tool request failed: {self.config.name} error={e}")
            return {
                "status": "error",
                "message": self.config.error_message,
            }
        except Exception as e:
            logger.error(f"In-call HTTP tool unexpected error: {self.config.name} error={e}", exc_info=True)
            return {
                "status": "error",
                "message": self.config.error_message,
            }
    
    async def _build_substitution_context(
        self,
        ai_params: Dict[str, Any],
        context: ToolExecutionContext
    ) -> Dict[str, str]:
        """
        Build combined substitution context from call context, pre-call results, and AI parameters.
        
        Context variables (auto-injected):
        - caller_number, called_number, caller_name
        - context_name, call_id
        
        Pre-call variables (from pre-call HTTP lookups):
        - Any variables fetched by pre-call tools (e.g., customer_name, account_id)
        
        AI parameters (provided by AI during function call):
        - Whatever parameters are defined in the tool config
        """
        sub = {
            "caller_number": context.caller_number or "",
            "called_number": context.called_number or "",
            "caller_name": context.caller_name or "",
            "context_name": context.context_name or "",
            "call_id": context.call_id or "",
        }
        
        # Add pre-call tool results (fetched before call started)
        # These are stored in session.pre_call_results by pre-call HTTP lookup tools
        try:
            if context.session_store:
                session = await context.session_store.get_by_call_id(context.call_id)
                if session:
                    pre_call_results = getattr(session, 'pre_call_results', None) or {}
                    for key, value in pre_call_results.items():
                        # Don't override built-in context variables
                        if key not in sub:
                            sub[key] = str(value) if value is not None else ""
                    if pre_call_results:
                        logger.debug(
                            f"Added pre-call variables to in-call tool context: {list(pre_call_results.keys())}",
                            extra={"tool": self.config.name, "call_id": context.call_id}
                        )
        except Exception as e:
            logger.warning(f"Failed to load pre-call results for in-call tool: {e}")
        
        # Add AI-provided parameters (these override pre-call vars if same name)
        for key, value in ai_params.items():
            if value is not None:
                sub[key] = str(value)
            else:
                sub[key] = ""
        
        return sub
    
    def _substitute_variables(self, template: str, context: Dict[str, str]) -> str:
        """
        Substitute variables in template string.
        
        Supports:
        - {variable} - Context or AI parameter
        - ${ENV_VAR} - Environment variable
        """
        result = template
        
        # Context/parameter variables: {var_name}
        for key, value in context.items():
            result = result.replace(f"{{{key}}}", value)
        
        # Environment variables: ${VAR_NAME}
        env_pattern = r'\$\{([A-Z_][A-Z0-9_]*)\}'
        def env_replacer(match):
            var_name = match.group(1)
            return os.environ.get(var_name, "")
        
        result = re.sub(env_pattern, env_replacer, result)
        
        return result
    
    def _extract_output_variables(self, data: Any) -> Dict[str, Any]:
        """
        Extract output variables from JSON response.

        List/dict results are JSON-serialized; scalars preserved as-is.
        """
        results = {}

        for var_name, path in self.config.output_variables.items():
            try:
                value = self._extract_path(data, path)
                if value is None:
                    results[var_name] = ""
                elif isinstance(value, (list, dict)):
                    results[var_name] = json.dumps(value)
                else:
                    results[var_name] = value
            except Exception as e:
                logger.debug(f"Failed to extract variable {var_name}: {e}")
                results[var_name] = ""

        return results

    def _extract_path(self, data: Any, path: str) -> Any:
        """Extract value from nested data using dot notation path.

        Delegates to the shared ``extract_path`` utility which supports
        simple keys, numeric indices, and ``[*]`` wildcards.
        """
        return extract_path(data, path)
    
    def _build_result_message(self, data: Dict[str, Any]) -> str:
        """
        Build a human-readable message from extracted data.
        """
        if not data:
            return "No data retrieved."
        
        # Simple key-value format
        parts = []
        for key, value in data.items():
            if value is not None and value != "":
                readable_key = key.replace('_', ' ').title()
                parts.append(f"{readable_key}: {value}")
        
        if parts:
            return "Retrieved: " + ", ".join(parts)
        return "Data retrieved successfully."
    
    def _redact_url(self, url: str) -> str:
        """Redact sensitive parts of URL for logging."""
        redacted = re.sub(
            r'(api_key|apikey|key|token|auth|password)=([^&]+)',
            r'\1=***',
            url,
            flags=re.IGNORECASE
        )
        return redacted


def create_in_call_http_tool(name: str, config_dict: Dict[str, Any]) -> InCallHTTPTool:
    """
    Factory function to create an in-call HTTP tool from YAML config.
    
    Args:
        name: Tool name from YAML key
        config_dict: Tool configuration dictionary
    
    Returns:
        Configured InCallHTTPTool instance
    """
    config = InCallHTTPConfig(
        name=name,
        description=config_dict.get('description', ''),
        enabled=config_dict.get('enabled', True),
        is_global=config_dict.get('is_global', False),
        timeout_ms=config_dict.get('timeout_ms', 5000),
        hold_audio_file=config_dict.get('hold_audio_file'),
        hold_audio_threshold_ms=config_dict.get('hold_audio_threshold_ms', 500),
        url=config_dict.get('url', ''),
        method=config_dict.get('method', 'POST'),
        headers=config_dict.get('headers', {}),
        query_params=config_dict.get('query_params', {}),
        body_template=config_dict.get('body_template'),
        parameters=config_dict.get('parameters', []),
        output_variables=config_dict.get('output_variables', {}),
        return_raw_json=config_dict.get('return_raw_json', False),
        max_response_size_bytes=config_dict.get('max_response_size_bytes', 65536),
        error_message=config_dict.get('error_message', "I'm sorry, I couldn't retrieve that information right now."),
    )
    
    return InCallHTTPTool(config)
