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
