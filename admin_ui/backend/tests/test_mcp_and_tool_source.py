"""Tests for MCP non-JSON response handling (LOW-T5) and authoritative
tool-source preference (LOW-T6)."""

import httpx
import pytest
from fastapi import HTTPException

import api.mcp as mcp_module
import api.tools as tools_module


class _FakeResponse:
    def __init__(self, status_code, text, json_raises=False, json_value=None):
        self.status_code = status_code
        self.text = text
        self.content = text.encode() if text else b""
        self._json_raises = json_raises
        self._json_value = json_value

    def json(self):
        if self._json_raises:
            raise ValueError("not json")
        return self._json_value


class _FakeAsyncClient:
    """Drop-in for httpx.AsyncClient that returns a preconfigured response."""

    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, *a, **k):
        return self._response

    async def post(self, *a, **k):
        return self._response


def _patch_single_base(monkeypatch):
    # Collapse to a single reachable base URL so the loop runs exactly once.
    monkeypatch.setattr(mcp_module, "_ai_engine_base_urls", lambda: ["http://127.0.0.1:15000"])


# ---------------------------------------------------------------------------
# LOW-T5: non-JSON engine response must yield a clear error, not an opaque 500.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_status_non_json_returns_clear_error(monkeypatch):
    _patch_single_base(monkeypatch)
    resp = _FakeResponse(status_code=200, text="<html>oops</html>", json_raises=True)
    monkeypatch.setattr(mcp_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(resp))

    with pytest.raises(HTTPException) as exc:
        await mcp_module.get_mcp_status()

    assert exc.value.status_code == 502
    assert "non-JSON" in exc.value.detail
    assert "oops" in exc.value.detail


@pytest.mark.asyncio
async def test_mcp_test_non_json_returns_clear_error(monkeypatch):
    _patch_single_base(monkeypatch)
    # Engine returns a 500 with a non-JSON body (the original opaque-crash case).
    resp = _FakeResponse(status_code=500, text="boom traceback", json_raises=True)
    monkeypatch.setattr(mcp_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(resp))

    with pytest.raises(HTTPException) as exc:
        await mcp_module.test_mcp_server("server1")

    assert exc.value.status_code == 500
    assert "non-JSON" in exc.value.detail
    assert "boom traceback" in exc.value.detail


@pytest.mark.asyncio
async def test_mcp_status_valid_json_still_works(monkeypatch):
    _patch_single_base(monkeypatch)
    resp = _FakeResponse(status_code=200, text="{}", json_value={"servers": []})
    monkeypatch.setattr(mcp_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(resp))

    out = await mcp_module.get_mcp_status()
    assert out == {"servers": []}


# ---------------------------------------------------------------------------
# LOW-T6: classification heuristic + engine-provided source preference.
# ---------------------------------------------------------------------------


def test_classify_tool_source_heuristic():
    http_names = {"crm_lookup"}
    assert tools_module._classify_tool_source("mcp_aviation_atis_get_atis", http_names) == "mcp"
    assert tools_module._classify_tool_source("crm_lookup", http_names) == "http"
    assert tools_module._classify_tool_source("transfer", http_names) == "builtin"


@pytest.mark.asyncio
async def test_catalog_prefers_engine_source(monkeypatch):
    """When the engine supplies an authoritative `source`, it wins over the
    heuristic (a builtin-named tool reported as http by the engine stays http)."""
    monkeypatch.setattr(tools_module.config_api, "_read_merged_config_dict", lambda: {})
    monkeypatch.setattr(tools_module, "_ai_engine_base_urls", lambda: ["http://127.0.0.1:15000"])

    engine_payload = {
        "tools": [
            # Name has no mcp_ prefix and is not in config -> heuristic would say builtin,
            # but the engine authoritatively labels it http.
            {"name": "transfer", "description": "", "category": "", "phase": "",
             "parameters": [], "source": "http"},
        ]
    }
    resp = _FakeResponse(status_code=200, text="{}", json_value=engine_payload)

    class _Client(_FakeAsyncClient):
        pass

    monkeypatch.setattr(tools_module.httpx, "AsyncClient", lambda *a, **k: _Client(resp))

    result = await tools_module.get_tool_catalog()
    assert result.source == "ai_engine"
    assert len(result.tools) == 1
    assert result.tools[0].source == "http"


@pytest.mark.asyncio
async def test_catalog_falls_back_to_heuristic_without_engine_source(monkeypatch):
    """No engine `source` field -> heuristic applies (mcp_ prefix -> mcp)."""
    monkeypatch.setattr(tools_module.config_api, "_read_merged_config_dict", lambda: {})
    monkeypatch.setattr(tools_module, "_ai_engine_base_urls", lambda: ["http://127.0.0.1:15000"])

    engine_payload = {
        "tools": [
            {"name": "mcp_demo_do_thing", "description": "", "category": "", "phase": "",
             "parameters": []},
        ]
    }
    resp = _FakeResponse(status_code=200, text="{}", json_value=engine_payload)
    monkeypatch.setattr(tools_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(resp))

    result = await tools_module.get_tool_catalog()
    assert len(result.tools) == 1
    assert result.tools[0].source == "mcp"
