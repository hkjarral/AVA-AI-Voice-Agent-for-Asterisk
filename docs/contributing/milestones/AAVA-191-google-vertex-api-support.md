# Milestone AAVA-191 — Google Vertex AI Live API Support

> **Status**: In Progress
> **Author**: hkjarral
> **Date**: 2026-02-19

## Goal

Add opt-in Google Vertex AI Live API support to the existing `google_live` provider, enabling enterprise GCP deployments with OAuth2/ADC authentication and access to GA models that fix the known function calling 1008 bug present in Developer API preview models.

## Background

The current `google_live` provider connects to `generativelanguage.googleapis.com` using an API key. This works well for development but has two limitations:

1. **Function calling 1008 bug**: Developer API preview models (`gemini-2.5-flash-native-audio-preview-*`) have a server-side bug where function calls (hangup, transcript, email) trigger WebSocket close code 1008 ~1 in 5–10 attempts. This is a known upstream issue (googleapis/python-genai #843, open since May 2025).
2. **Enterprise requirements**: GCP/enterprise users need service account auth, VPC-SC compliance, and SLA guarantees — all of which require Vertex AI.

Google has fixed the function calling bug in the Vertex AI GA model `gemini-live-2.5-flash-native-audio`, but this model is **only available via the Vertex AI endpoint**, not the Developer API.

Related: [AAVA-191](https://linear.app/hkjarral/issue/AAVA-191)

## Design

### Approach

Opt-in via `use_vertex_ai: true` in provider config. Default is `false` — existing Developer API users see zero changes.

**Authentication difference:**

| | Developer API | Vertex AI |
|---|---|---|
| Endpoint | `generativelanguage.googleapis.com` | `{location}-aiplatform.googleapis.com` |
| Auth | `?key=API_KEY` query param | `Authorization: Bearer TOKEN` header |
| Token | Static API key | OAuth2 bearer (1h TTL, auto-refreshed via ADC) |
| Model path | `models/{model}` | `publishers/google/models/{model}` |
| Project/Location | Not required | Required |

**Token acquisition**: Uses `google.auth.default()` with `cloud-platform` scope via the `google-auth` library (lightweight, no heavy SDK). Runs in executor to avoid blocking the async event loop. Token is refreshed per-session (1h TTL is sufficient for call sessions).

### API / Interface Changes

New fields on `GoogleProviderConfig`:

```yaml
providers:
  google_live:
    # --- Vertex AI mode (enterprise) ---
    use_vertex_ai: true
    vertex_project: ${GOOGLE_CLOUD_PROJECT}      # GCP project ID
    vertex_location: ${GOOGLE_CLOUD_LOCATION}    # GCP region (default: us-central1)
    llm_model: gemini-live-2.5-flash-native-audio  # GA model with fixed function calling
    # api_key is NOT used in Vertex AI mode

    # --- Developer API mode (default, unchanged) ---
    # use_vertex_ai: false  (default)
    # api_key: ${GOOGLE_API_KEY}
```

New environment variables:

```env
GOOGLE_CLOUD_PROJECT=my-gcp-project-id
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/gcp-service-account.json
```

### Files to Create / Modify

| File | Action | Description |
|------|--------|-------------|
| `src/config.py` | Modify | Add `use_vertex_ai`, `vertex_project`, `vertex_location` to `GoogleProviderConfig` |
| `src/config/security.py` | Modify | Inject `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` env vars |
| `src/providers/google_live.py` | Modify | Vertex endpoint + OAuth2 bearer auth in `start_session()`; model path in `_send_setup()` |
| `requirements.txt` | Modify | Add `google-auth>=2.0.0` |
| `.env.example` | Modify | Add commented Vertex AI section with step-by-step instructions |
| `config/ai-agent.golden-google-live.yaml` | Modify | Add commented Vertex AI example |
| `admin_ui/frontend/src/components/config/providers/GoogleLiveProviderForm.tsx` | Modify | Vertex AI toggle + project/location fields in Authentication section |
| `admin_ui/frontend/src/pages/System/EnvPage.tsx` | Modify | Add `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` to known keys + UI inputs |
| `admin_ui/frontend/src/pages/Wizard.tsx` | Modify | Info banner when Google Live selected: "For Vertex AI, configure via Providers page" |

## Testing

- [ ] Unit tests: endpoint URL construction for both modes
- [ ] Unit tests: model path format (`models/` vs `publishers/google/models/`)
- [ ] Unit tests: `use_vertex_ai=False` default leaves existing behavior unchanged
- [ ] Manual regression: place call with Developer API config → verify no regression
- [ ] Manual validation: place call with `use_vertex_ai: true` + service account → verify connection, audio, tool execution
- [ ] Health check: `/health` reports `google_live` ready in both modes
- [ ] Admin UI: Vertex toggle shows/hides fields correctly

## Acceptance Criteria

- [ ] `use_vertex_ai: false` (default) — zero behavior change for existing users
- [ ] `use_vertex_ai: true` — connects to `{location}-aiplatform.googleapis.com` with OAuth2 bearer token
- [ ] Model path uses `publishers/google/models/{model}` format for Vertex AI
- [ ] `vertex_project` validation raises clear error if missing when Vertex AI enabled
- [ ] `google-auth` import failure raises clear `RuntimeError` with install instructions
- [ ] Admin UI provider form shows Vertex AI toggle and project/location fields
- [ ] `.env.example` documents all three new env vars with setup instructions
- [ ] Golden config includes commented Vertex AI example
- [ ] Wizard shows info banner directing Vertex AI users to Providers page

## Dependencies

- `google-auth>=2.0.0` (new Python dependency)
- GCP project with Vertex AI API enabled (`aiplatform.googleapis.com`)
- Service account with `roles/aiplatform.user` IAM role
- Service account JSON key mounted into the `ai-engine` container

## Risks / Open Questions

- **Token refresh**: Bearer tokens expire after 1 hour. Current implementation refreshes once per `start_session()` call. For very long-running calls (>1h), the token could expire mid-session. Mitigation: most telephony calls are well under 1h; a follow-up can add proactive refresh if needed.
- **Vertex AI model availability**: `gemini-live-2.5-flash-native-audio` availability may vary by region. `us-central1` is the recommended default.
- **Container key mount**: Service account JSON must be bind-mounted into the container. This requires a `docker-compose.yml` volume entry — operators must configure this manually (documented in `.env.example`).

---

*To propose this milestone: submit it as a Draft PR and reference it in a [GitHub Discussion](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/discussions). See [GOVERNANCE.md](../../../GOVERNANCE.md) for the feature proposal process.*
