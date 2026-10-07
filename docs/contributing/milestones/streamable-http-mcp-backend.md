# Milestone — Streamable HTTP MCP backend

> **Status**: In Progress (backend draft; not operator-ready)
> **Author**: pi0n00r
> **Date**: 2026-10-07

## Goal

Connect an explicitly configured remote MCP server to AVA's existing in-call
tool registry without requiring a separate stdio bridge. This is ordinary
authenticated MCP connectivity, not a new tool or Agent policy.

## Background

The existing MCP manager accepts stdio only. Discussion
[#693](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/discussions/693)
records the upstream proposal and the maintainer's invitation to submit a
coherent backend slice as a draft.

## Design

The AI Engine reads a Streamable HTTP URL and optional environment-referenced
headers from its MCP server YAML. The official Python MCP SDK negotiates a modern
or supported legacy protocol, sends version/session headers, handles JSON and
request-scoped SSE replies, and pages through tool listings. The existing
registry, provider-safe names, per-Agent tool exposure and execution checks stay
in place. Stdio behavior is unchanged.

The adapter establishes a fresh session for each operation. It sends one
tools/call request per invocation using the SDK's low-level session method, not
the high-level auto-retry helper. A timeout, disconnect, or mismatched response
therefore reports an unknown outcome; AVA never blindly replays a possibly
effectful call. Subsequent independent operations establish new sessions.

The only new runtime dependency is the pinned official mcp Python SDK. No new
database, API route, background worker, or persistence is introduced.

## Interface

The YAML server entry adds transport: streamable_http, url, and headers. Auth
headers must reference environment variables. Admin UI creation/editing of HTTP
entries is not part of this draft; the operator must edit YAML and restart the
AI Engine. MCP status omits the URL and resolved headers.

## Files

- src/mcp/streamable_http_client.py — negotiated HTTP adapter
- src/mcp/manager.py and src/config.py — transport selection and configuration
- src/tools/mcp_tool.py — propagate MCP isError as an error
- tests/mcp/test_streamable_http_upstream.py — local wire and failure regressions
- docs/MCP_INTEGRATION.md, AVA.mdc, CHANGELOG.md — operator and project context

## Verification and acceptance

- [x] Local JSON and SSE discovery and tool call
- [x] Modern and legacy negotiation, auth failure, pagination, and session expiry
- [x] Concurrent calls, tool error propagation, and lost/mismatched response no-replay
- [x] Existing stdio, naming, and Agent filtering tests
- [x] Root Python suite with one independently known baseline test deselected
- [ ] Admin UI entry creation, edit, validation and save/readback
- [ ] Live PBX call with an explicitly configured test MCP server
- [ ] Maintainer CI and security checks on the frozen draft head

The unchecked items prevent this draft from being called operator-ready or
merge-ready. Authentication/header values, tool arguments/results, and private
deployment details must not enter public diagnostics.

## Risks and open questions

- A remote effect may complete even if AVA times out; idempotency or status
  reconciliation must be supplied by that MCP server before any manual retry.
- The official SDK adds dependencies to the AI Engine image; image-size and
  Docker build gates remain for maintainer CI.
- Admin UI editing should be completed in a separately reviewed follow-up
  before advertising Streamable HTTP as a normal UI-managed option.
