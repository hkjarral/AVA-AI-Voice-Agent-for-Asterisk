"""
Tools API endpoints for testing HTTP tools before saving.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
import httpx
import json
import re
import os
import logging
import time
import ipaddress
import socket
from urllib.parse import urlparse, urljoin
from settings import get_setting
from . import config as config_api

router = APIRouter()
logger = logging.getLogger(__name__)

# Default test values for template variables
DEFAULT_TEST_VALUES = {
    "caller_number": "+15551234567",
    "called_number": "+18005551234",
    "caller_name": "Test Caller",
    "caller_id": "+15551234567",
    "call_id": "1234567890.123",
    "context_name": "test-context",
    "campaign_id": "test-campaign",
    "lead_id": "test-lead-123",
}


class TestHTTPRequest(BaseModel):
    """Request model for testing HTTP tools."""
    url: str
    method: str = "GET"
    headers: Dict[str, str] = {}
    query_params: Dict[str, str] = {}
    body_template: Optional[str] = None
    timeout_ms: int = 5000
    test_values: Dict[str, str] = {}


class TestHTTPResponse(BaseModel):
    """Response model for HTTP tool test results."""
    success: bool
    status_code: Optional[int] = None
    response_time_ms: float
    headers: Dict[str, str] = {}
    body: Optional[Any] = None
    body_raw: Optional[str] = None
    error: Optional[str] = None
    resolved_url: str
    resolved_body: Optional[str] = None
    suggested_mappings: List[Dict[str, str]] = []


def _substitute_variables(template: str, values: Dict[str, str]) -> str:
    """
    Substitute template variables like {caller_number} and ${ENV_VAR}.
    """
    result = template
    
    # First, substitute {variable} style placeholders
    for key, value in values.items():
        result = result.replace(f"{{{key}}}", str(value))
    
    # Then substitute ${ENV_VAR} style environment variables
    env_pattern = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')
    def env_replacer(match):
        env_name = match.group(1)
        resolved = get_setting(env_name, default=f"${{{env_name}}}")
        return resolved
    
    result = env_pattern.sub(env_replacer, result)
    return result


def _normalize_template(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    return s or None


def _format_pretty_html(text: str) -> str:
    # Keep in sync with AI Engine email tools.
    safe = (text or "")
    safe = safe.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe = safe.replace('"', "&quot;").replace("'", "&#39;")
    safe = safe.replace("\r\n", "\n").replace("\r", "\n")
    return safe.replace("\n", "<br/>\n")


def _load_email_template_defaults() -> Dict[str, Any]:
    """
    Load default templates/variable reference from the main project tree.

    The Admin UI container mounts the repo at /app/project (PROJECT_ROOT), but for local
    dev we also fall back to resolving the repo root relative to this file.
    """
    import sys
    import importlib

    global _EMAIL_TEMPLATE_DEFAULTS_CACHE
    if _EMAIL_TEMPLATE_DEFAULTS_CACHE is not None:
        return _EMAIL_TEMPLATE_DEFAULTS_CACHE

    project_root = os.environ.get("PROJECT_ROOT")
    if not project_root:
        here = os.path.abspath(os.path.dirname(__file__))
        project_root = os.path.abspath(os.path.join(here, "..", "..", "..", ".."))

    if not os.path.isdir(project_root) or not os.path.isdir(os.path.join(project_root, "src")):
        raise HTTPException(
            status_code=503,
            detail=f"Project source not mounted yet at PROJECT_ROOT={project_root}",
        )

    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            importlib.invalidate_caches()
            from src.tools.business.email_templates import (  # type: ignore
                DEFAULT_SEND_EMAIL_SUMMARY_HTML_TEMPLATE,
                DEFAULT_REQUEST_TRANSCRIPT_HTML_TEMPLATE,
                EMAIL_TEMPLATE_VARIABLES,
            )
            _EMAIL_TEMPLATE_DEFAULTS_CACHE = {
                "send_email_summary": DEFAULT_SEND_EMAIL_SUMMARY_HTML_TEMPLATE,
                "request_transcript": DEFAULT_REQUEST_TRANSCRIPT_HTML_TEMPLATE,
                "variables": EMAIL_TEMPLATE_VARIABLES,
            }
            return _EMAIL_TEMPLATE_DEFAULTS_CACHE
        except Exception as e:  # pragma: no cover - environment-specific
            last_exc = e
            time.sleep(0.15 * (attempt + 1))
            continue

    logger.exception("Failed to load email template defaults", exc_info=last_exc)
    raise HTTPException(
        status_code=503,
        detail=f"Failed to load email templates from project: {last_exc}",
    ) from last_exc

    # Unreachable; kept for type checkers.
    return {}


_EMAIL_TEMPLATE_DEFAULTS_CACHE: Optional[Dict[str, Any]] = None


class EmailTemplateDefaultsResponse(BaseModel):
    send_email_summary: str
    request_transcript: str
    variables: List[Dict[str, str]]


@router.get("/email-templates/defaults", response_model=EmailTemplateDefaultsResponse)
async def get_email_template_defaults():
    """Return default HTML templates and variable reference for email tools."""
    return _load_email_template_defaults()


class EmailTemplatePreviewRequest(BaseModel):
    tool: str
    html_template: Optional[str] = None
    include_transcript: Optional[bool] = True
    test_values: Dict[str, str] = {}


class EmailTemplatePreviewResponse(BaseModel):
    success: bool
    html: Optional[str] = None
    error: Optional[str] = None


@router.post("/email-templates/preview", response_model=EmailTemplatePreviewResponse)
async def preview_email_template(request: EmailTemplatePreviewRequest):
    """
    Render a Jinja2 email template using safe test values for preview.

    Templates are sandboxed. If no `html_template` is provided, the default template
    for the requested tool is used.
    """
    from jinja2.sandbox import SandboxedEnvironment

    defaults = _load_email_template_defaults()
    tool = (request.tool or "").strip()
    if tool not in ("send_email_summary", "request_transcript"):
        raise HTTPException(status_code=400, detail="Unsupported tool; use send_email_summary or request_transcript")

    default_template = defaults[tool]
    override = _normalize_template(request.html_template)
    template_str = override or default_template

    # Merge default test values with caller-provided overrides.
    test_values = {**DEFAULT_TEST_VALUES, **(request.test_values or {})}

    # Email-specific placeholders
    transcript_text = (
        "[00:00:03] Caller: Hi, I need help with my account.\n"
        "[00:00:06] Agent: Sure — what seems to be the issue?\n"
        "[00:00:12] Caller: I can’t log in.\n"
    )

    variables: Dict[str, Any] = {
        "call_id": test_values.get("call_id", "1234567890.123"),
        "context_name": test_values.get("context_name", "test-context"),
        "recipient_email": "caller@example.com",
        "call_date": "2026-02-05 12:34:56",
        "call_start_time": "2026-02-05 12:34:56",
        "call_end_time": "2026-02-05 12:37:11",
        "duration": "2m 15s",
        "duration_seconds": 135,
        "caller_name": test_values.get("caller_name", "Test Caller"),
        "caller_number": test_values.get("caller_number", "+15551234567"),
        "called_number": test_values.get("called_number", "+18005551234"),
        "outcome": "caller_hangup",
        "call_outcome": "caller_hangup",
        "hangup_initiator": "caller",
        "include_transcript": bool(request.include_transcript) if request.include_transcript is not None else True,
        "transcript": transcript_text,
        "transcript_html": _format_pretty_html(transcript_text),
        "transcript_note": None,
    }

    env = SandboxedEnvironment(autoescape=False)
    try:
        rendered = env.from_string(template_str).render(**variables)
        # Prevent accidental huge responses (and keep UI responsive)
        if len(rendered) > 500_000:
            raise ValueError("Rendered HTML too large for preview")
        return EmailTemplatePreviewResponse(success=True, html=rendered)
    except Exception as e:
        return EmailTemplatePreviewResponse(success=False, error=str(e))


class ToolParameterInfo(BaseModel):
    name: str
    type: str
    description: str
    required: bool = False
    enum: Optional[Any] = None
    default: Optional[Any] = None


class ToolDefinitionInfo(BaseModel):
    name: str
    description: str
    category: str = ""
    phase: str = ""
    is_global: bool = False
    requires_channel: bool = False
    max_execution_time: int = 0
    timeout_ms: Optional[Any] = None
    output_variables: Optional[Any] = None
    parameters: List[ToolParameterInfo] = []
    has_input_schema: bool = False
    source: str = "builtin"  # builtin | http | mcp | unknown


class ToolCatalogResponse(BaseModel):
    tools: List[ToolDefinitionInfo]
    source: str = "ai_engine"  # ai_engine | local_fallback


def _ai_engine_base_urls() -> list[str]:
    """Return candidate ai-engine health base URLs (no trailing /health)."""
    candidates: list[str] = []
    env = os.getenv("HEALTH_CHECK_AI_ENGINE_URL")
    if env:
        candidates.append(env.replace("/health", ""))
    candidates.extend(["http://127.0.0.1:15000", "http://ai-engine:15000", "http://ai_engine:15000"])
    out: list[str] = []
    for c in candidates:
        c = (c or "").strip().rstrip("/")
        if c and c not in out:
            out.append(c)
    return out


def _safe_jsonable(obj: Any, *, max_depth: int = 5, max_items: int = 50, depth: int = 0) -> Any:
    if depth >= max_depth:
        return str(obj)
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for idx, (k, v) in enumerate(list(obj.items())[:max_items]):
            out[str(k)] = _safe_jsonable(v, max_depth=max_depth, max_items=max_items, depth=depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [_safe_jsonable(v, max_depth=max_depth, max_items=max_items, depth=depth + 1) for v in list(obj)[:max_items]]
    return str(obj)


def _http_tool_names_from_config(cfg: dict) -> set[str]:
    names: set[str] = set()
    tools_block = cfg.get("tools") if isinstance(cfg, dict) else None
    if isinstance(tools_block, dict):
        for name, tool_cfg in tools_block.items():
            if not isinstance(name, str) or not name:
                continue
            if not isinstance(tool_cfg, dict):
                continue
            kind = str(tool_cfg.get("kind") or "").strip()
            if kind in ("generic_http_lookup", "generic_webhook"):
                names.add(name)
    in_call = cfg.get("in_call_tools") if isinstance(cfg, dict) else None
    if isinstance(in_call, dict):
        for name, tool_cfg in in_call.items():
            if not isinstance(name, str) or not name:
                continue
            if not isinstance(tool_cfg, dict):
                continue
            kind = str(tool_cfg.get("kind") or "in_call_http_lookup").strip()
            if kind == "in_call_http_lookup":
                names.add(name)
    return names


@router.get("/catalog", response_model=ToolCatalogResponse)
async def get_tool_catalog():
    """
    Return a read-only tool catalog for Admin UI.

    Prefers ai-engine's live view (includes MCP tools), falls back to local
    best-effort initialization from merged YAML config.
    """
    merged_cfg: dict = {}
    try:
        merged_cfg = config_api._read_merged_config_dict() or {}
        if not isinstance(merged_cfg, dict):
            merged_cfg = {}
    except Exception:
        merged_cfg = {}

    http_tool_names = _http_tool_names_from_config(merged_cfg)

    # Preferred: ask ai-engine (authoritative for what is actually registered/runnable).
    for base in _ai_engine_base_urls():
        url = f"{base}/tools/definitions"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
            if resp.status_code != 200:
                continue
            data = resp.json() if resp.content else {}
            tools = data.get("tools") if isinstance(data, dict) else None
            if not isinstance(tools, list):
                continue

            out: List[ToolDefinitionInfo] = []
            for item in tools:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                source = "builtin"
                if name.startswith("mcp_"):
                    source = "mcp"
                elif name in http_tool_names:
                    source = "http"

                params_raw = item.get("parameters") if isinstance(item.get("parameters"), list) else []
                params_out: List[ToolParameterInfo] = []
                for p in params_raw:
                    if not isinstance(p, dict):
                        continue
                    params_out.append(
                        ToolParameterInfo(
                            name=str(p.get("name") or ""),
                            type=str(p.get("type") or ""),
                            description=str(p.get("description") or ""),
                            required=bool(p.get("required", False)),
                            enum=_safe_jsonable(p.get("enum")),
                            default=_safe_jsonable(p.get("default")),
                        )
                    )

                out.append(
                    ToolDefinitionInfo(
                        name=name,
                        description=str(item.get("description") or ""),
                        category=str(item.get("category") or ""),
                        phase=str(item.get("phase") or ""),
                        is_global=bool(item.get("is_global", False)),
                        requires_channel=bool(item.get("requires_channel", False)),
                        max_execution_time=int(item.get("max_execution_time") or 0),
                        timeout_ms=_safe_jsonable(item.get("timeout_ms")),
                        output_variables=_safe_jsonable(item.get("output_variables")),
                        parameters=params_out,
                        has_input_schema=bool(item.get("has_input_schema", False)),
                        source=source,
                    )
                )
            return ToolCatalogResponse(tools=out, source="ai_engine")
        except httpx.ConnectError:
            continue
        except Exception:
            continue

    # Fallback: local best-effort tool registry init.
    try:
        project_root = os.environ.get("PROJECT_ROOT")
        if not project_root:
            here = os.path.abspath(os.path.dirname(__file__))
            project_root = os.path.abspath(os.path.join(here, "..", "..", "..", ".."))

        import sys
        if project_root and project_root not in sys.path:
            sys.path.insert(0, project_root)

        from src.tools.registry import tool_registry  # type: ignore
        tool_registry.clear()
        tool_registry.initialize_default_tools()
        tools_cfg = merged_cfg.get("tools") if isinstance(merged_cfg, dict) else {}
        in_call_cfg = merged_cfg.get("in_call_tools") if isinstance(merged_cfg, dict) else {}
        if isinstance(tools_cfg, dict):
            tool_registry.initialize_http_tools_from_config(tools_cfg)
        if isinstance(in_call_cfg, dict):
            tool_registry.initialize_in_call_http_tools_from_config(in_call_cfg, cache_key="global")

        out: List[ToolDefinitionInfo] = []
        for d in tool_registry.get_definitions():
            name = str(getattr(d, "name", "") or "").strip()
            if not name:
                continue
            source = "builtin"
            if name.startswith("mcp_"):
                source = "mcp"
            elif name in http_tool_names:
                source = "http"
            params_out: List[ToolParameterInfo] = []
            for p in (getattr(d, "parameters", None) or []):
                params_out.append(
                    ToolParameterInfo(
                        name=str(getattr(p, "name", "")),
                        type=str(getattr(p, "type", "")),
                        description=str(getattr(p, "description", "")),
                        required=bool(getattr(p, "required", False)),
                        enum=_safe_jsonable(getattr(p, "enum", None)),
                        default=_safe_jsonable(getattr(p, "default", None)),
                    )
                )
            out.append(
                ToolDefinitionInfo(
                    name=name,
                    description=str(getattr(d, "description", "")),
                    category=str(getattr(getattr(d, "category", None), "value", "") or ""),
                    phase=str(getattr(getattr(d, "phase", None), "value", "") or ""),
                    is_global=bool(getattr(d, "is_global", False)),
                    requires_channel=bool(getattr(d, "requires_channel", False)),
                    max_execution_time=int(getattr(d, "max_execution_time", 0) or 0),
                    timeout_ms=_safe_jsonable(getattr(d, "timeout_ms", None)),
                    output_variables=_safe_jsonable(getattr(d, "output_variables", [])),
                    parameters=params_out,
                    has_input_schema=bool(getattr(d, "input_schema", None)),
                    source=source,
                )
            )
        return ToolCatalogResponse(tools=out, source="local_fallback")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build tool catalog: {str(e)}")
    except Exception as e:
        return EmailTemplatePreviewResponse(success=False, error=str(e))


def _extract_json_paths(obj: Any, prefix: str = "") -> List[Dict[str, str]]:
    """
    Extract all JSON paths from a response object for suggested mappings.
    Returns list of {path, value, type} for each leaf node.
    """
    paths = []
    
    if isinstance(obj, dict):
        for key, value in obj.items():
            new_prefix = f"{prefix}.{key}" if prefix else key
            if isinstance(value, (dict, list)):
                paths.extend(_extract_json_paths(value, new_prefix))
            else:
                paths.append({
                    "path": new_prefix,
                    "value": str(value)[:100] if value is not None else "null",
                    "type": type(value).__name__
                })
    elif isinstance(obj, list) and len(obj) > 0:
        # Only show first element of arrays
        paths.extend(_extract_json_paths(obj[0], f"{prefix}[0]"))
        if len(obj) > 1:
            paths.append({
                "path": f"{prefix}[*]",
                "value": f"(array with {len(obj)} items)",
                "type": "array"
            })
    
    return paths


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")


def _env_csv_set(name: str) -> set[str]:
    raw = os.environ.get(name, "")
    items = []
    for part in raw.split(","):
        s = part.strip()
        if s:
            items.append(s)
    return set(items)


def _is_private_or_sensitive_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _validate_http_tool_test_target(resolved_url: str) -> None:
    """
    Prevent SSRF-style abuse of the HTTP tool test endpoint.

    Defaults:
    - Allow only http/https
    - Block localhost and private network targets (incl. link-local, loopback, RFC1918, etc.)
    - Do not allow basic-auth credentials embedded in URLs

    Overrides:
    - Set `AAVA_HTTP_TOOL_TEST_ALLOW_PRIVATE=1` to allow private targets (trusted-network only)
    - Or allow specific hosts via `AAVA_HTTP_TOOL_TEST_ALLOW_HOSTS=host1,host2`
    """
    parsed = urlparse(resolved_url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only http/https URLs are supported")

    if parsed.username or parsed.password:
        raise HTTPException(
            status_code=400,
            detail="URLs with embedded credentials are not allowed; use headers/env vars instead",
        )

    hostname = (parsed.hostname or "").strip()
    if not hostname:
        raise HTTPException(status_code=400, detail="Invalid URL: missing hostname")

    # Fast-path deny common localhost hostnames.
    if hostname.lower() in ("localhost", "localhost.localdomain"):
        hostname = hostname.lower()

    allow_private = _env_bool("AAVA_HTTP_TOOL_TEST_ALLOW_PRIVATE", default=False)
    allow_hosts = {h.strip().lower() for h in _env_csv_set("AAVA_HTTP_TOOL_TEST_ALLOW_HOSTS")}
    host_allowed = hostname.lower() in allow_hosts

    # If hostname is a literal IP, validate it directly.
    try:
        ip = ipaddress.ip_address(hostname)
        if _is_private_or_sensitive_ip(ip) and not (allow_private or host_allowed):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Blocked HTTP test request to a private/localhost target. "
                    "Run Admin UI only on a trusted network. "
                    "To override, set AAVA_HTTP_TOOL_TEST_ALLOW_PRIVATE=1 "
                    "or allow a specific hostname via AAVA_HTTP_TOOL_TEST_ALLOW_HOSTS."
                ),
            )
        return
    except ValueError:
        pass

    # Resolve hostname and block private targets unless explicitly allowed.
    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to resolve hostname: {e}") from e

    ips: set[str] = set()
    for _, _, _, _, sockaddr in infos:
        ip_str = sockaddr[0]
        if ip_str:
            ips.add(ip_str)

    if not ips:
        raise HTTPException(status_code=400, detail="Failed to resolve hostname to an IP address")

    if allow_private or host_allowed:
        return

    for ip_str in ips:
        try:
            ip = ipaddress.ip_address(ip_str)
            if _is_private_or_sensitive_ip(ip):
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Blocked HTTP test request to a private/localhost target. "
                        "Run Admin UI only on a trusted network. "
                        "To override, set AAVA_HTTP_TOOL_TEST_ALLOW_PRIVATE=1 "
                        "or allow a specific hostname via AAVA_HTTP_TOOL_TEST_ALLOW_HOSTS."
                    ),
                )
        except ValueError:
            continue


@router.post("/test-http", response_model=TestHTTPResponse)
async def test_http_tool(request: TestHTTPRequest):
    """
    Test an HTTP tool configuration by making the actual request.
    
    This endpoint:
    1. Substitutes template variables with test values
    2. Makes the HTTP request
    3. Returns the response with suggested variable mappings
    """
    # Merge default test values with provided ones
    test_values = {**DEFAULT_TEST_VALUES, **request.test_values}
    
    # Resolve URL with variable substitution
    resolved_url = _substitute_variables(request.url, test_values)
    _validate_http_tool_test_target(resolved_url)
    
    # Build query parameters
    resolved_params = {}
    for key, value in request.query_params.items():
        resolved_params[key] = _substitute_variables(value, test_values)
    
    # Resolve headers
    resolved_headers = {}
    for key, value in request.headers.items():
        resolved_headers[key] = _substitute_variables(value, test_values)
    
    # Resolve body template
    resolved_body = None
    if request.body_template:
        resolved_body = _substitute_variables(request.body_template, test_values)
    
    # Prepare the response
    response_data = TestHTTPResponse(
        success=False,
        response_time_ms=0,
        resolved_url=resolved_url,
        resolved_body=resolved_body
    )
    
    method = (request.method or "GET").strip().upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
        raise HTTPException(status_code=400, detail=f"Unsupported HTTP method: {method}")

    # Make the HTTP request
    start_time = time.time()
    timeout_seconds = request.timeout_ms / 1000.0
    
    try:
        follow_redirects = _env_bool("AAVA_HTTP_TOOL_TEST_FOLLOW_REDIRECTS", default=False)
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
            # Prepare request kwargs
            kwargs: Dict[str, Any] = {
                "method": method,
                "url": resolved_url,
                "headers": resolved_headers,
                "params": resolved_params if resolved_params else None,
            }
            
            # Add body for POST/PUT/PATCH
            if method in ("POST", "PUT", "PATCH") and resolved_body:
                # Check if Content-Type is JSON
                content_type = resolved_headers.get("Content-Type", resolved_headers.get("content-type", ""))
                if "application/json" in content_type.lower():
                    # Parse and send as JSON to ensure proper encoding
                    try:
                        json_data = json.loads(resolved_body)
                        kwargs["json"] = json_data
                    except json.JSONDecodeError:
                        # If it's not valid JSON, send as content
                        kwargs["content"] = resolved_body
                else:
                    kwargs["content"] = resolved_body
            
            # Make the request (manual redirects to prevent SSRF bypass via redirect-to-private targets).
            max_hops = 10
            resp = None
            for _ in range(max_hops + 1):
                resp = await client.request(**kwargs)

                is_redirect = resp.status_code in (301, 302, 303, 307, 308) and bool(resp.headers.get("location"))
                if not (follow_redirects and is_redirect):
                    break

                next_url = urljoin(str(resp.url), str(resp.headers.get("location") or ""))
                _validate_http_tool_test_target(next_url)

                # RFC-ish behavior: 303 always becomes GET.
                if resp.status_code == 303:
                    kwargs["method"] = "GET"
                    kwargs.pop("json", None)
                    kwargs.pop("content", None)
                kwargs["url"] = next_url

            if resp is None:
                raise HTTPException(status_code=400, detail="Request failed: no response received")
            response_data.resolved_url = str(resp.url)
            
            response_data.response_time_ms = (time.time() - start_time) * 1000
            response_data.status_code = resp.status_code
            response_data.headers = dict(resp.headers)
            response_data.body_raw = resp.text[:10000]  # Limit response size
            
            # Try to parse as JSON
            try:
                json_body = resp.json()
                response_data.body = json_body
                response_data.suggested_mappings = _extract_json_paths(json_body)
            except (ValueError, httpx.DecodingError):
                # Not JSON, just use raw text
                response_data.body = resp.text[:10000]
            
            response_data.success = 200 <= resp.status_code < 300
            
            if not response_data.success:
                response_data.error = f"HTTP {resp.status_code}: {resp.reason_phrase}"
                
    except httpx.TimeoutException:
        response_data.response_time_ms = (time.time() - start_time) * 1000
        response_data.error = f"Request timed out after {request.timeout_ms}ms"
    except httpx.ConnectError as e:
        response_data.response_time_ms = (time.time() - start_time) * 1000
        response_data.error = f"Connection failed: {e!s}"
    except Exception as e:
        response_data.response_time_ms = (time.time() - start_time) * 1000
        response_data.error = f"Request failed: {e!s}"
        logger.exception("HTTP tool test failed")
    
    return response_data


@router.get("/test-values")
async def get_default_test_values():
    """
    Get the default test values for template variable substitution.
    """
    return DEFAULT_TEST_VALUES
