# Milestone — Streamable HTTP MCP backend

> **Status**: Backend/YAML live in-call acceptance complete; final review/CI in progress; operator UI tracked separately
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
the high-level auto-retry helper. Setup or discovery failures before a
tools/call POST attempt report that the tool was not invoked. Once that POST
is attempted, a timeout, disconnect, expired session or mismatched response
reports an unknown outcome: the request may have reached the server. AVA never
blindly replays a possibly effectful call. Subsequent independent operations
establish new sessions.

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
- [ ] Refreshed final-head root CI gate; focused cancellation/Whisper and transport tests must remain included
- [ ] Admin UI entry creation, edit, validation and save/readback — separate follow-up #703
- [x] Live allowed/denied Agent calls with an explicitly configured harmless MCP server; one allowed execution, zero denied executions, caller-confirmed output/audio, and complete cleanup
- [ ] Maintainer final CI and security checks on the frozen ready head

Live pre/post phase execution is not claimed: the maintainer's private test
Agents had boolean opt-outs where lists are required. No phase tool ran. The
fixture was corrected and both unchanged phase handlers were verified with
synthetic sessions; in-call MCP allow/deny acceptance above remains valid.

Backend merge acceptance requires the current-head automated gate, authenticated
container/runtime verification, and live Agent-call evidence above. Admin UI
editing is agreed follow-up scope in [#703](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues/703);
it does not block an otherwise accepted backend/YAML slice. The full UI-managed
feature remains incomplete until that follow-up passes its operator acceptance.
Authentication/header values, tool arguments/results, and private deployment
details must not enter public diagnostics.

## Risks and open questions

- A remote effect may complete even if AVA times out; idempotency or status
  reconciliation must be supplied by that MCP server before any manual retry.
- The official SDK adds dependencies to the AI Engine image; image-size and
  Docker build gates remain for maintainer CI.
- Admin UI editing and the remaining API/observability/documentation work are
  tracked in #703. Live MCP replacement remains separately tracked in #582.
  Admin UI editing should be completed in a separately reviewed follow-up
  before advertising Streamable HTTP as a normal UI-managed option.
