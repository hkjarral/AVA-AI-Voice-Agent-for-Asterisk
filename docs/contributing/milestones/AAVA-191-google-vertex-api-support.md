# Milestone AAVA-191 — Google Vertex AI Live API Support

> **Status**: ✅ Complete  
> **Author**: hkjarral  
> **Date**: 2026-02-19  
> **Version**: v4.3.0

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

| Feature | Developer API | Vertex AI |
| ------- | ------------- | --------- |
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
| ---- | ------ | ----------- |
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

- [x] Unit tests: endpoint URL construction for both modes
- [x] Unit tests: model path format (`models/` vs `publishers/google/models/`)
- [x] Unit tests: `use_vertex_ai=False` default leaves existing behavior unchanged
- [x] Manual regression: place call with Developer API config → verified no regression (2026-02-19)
- [x] Manual validation: place call with `use_vertex_ai: true` + service account → verified connection, audio, tool execution (2026-02-19)
- [x] Health check: `/health` reports `google_live` ready in both modes
- [x] Admin UI: Vertex toggle shows/hides fields correctly

## Acceptance Criteria

- [x] `use_vertex_ai: false` (default) — zero behavior change for existing users
- [x] `use_vertex_ai: true` — connects to `{location}-aiplatform.googleapis.com` with OAuth2 bearer token
- [x] Model path uses `projects/{project}/locations/{location}/publishers/google/models/{model}` format for Vertex AI
- [x] `vertex_project` validation raises clear error if missing when Vertex AI enabled
- [x] `google-auth` import failure raises clear `RuntimeError` with install instructions
- [x] Admin UI provider form shows Vertex AI toggle, project/location fields, JSON upload, verify button
- [x] `.env.example` documents all three new env vars with setup instructions
- [x] Golden config includes commented Vertex AI example
- [x] Wizard shows info banner directing Vertex AI users to Providers page
- [x] Preflight script handles secrets directory permissions for credential storage

## Dependencies

- `google-auth>=2.0.0` (new Python dependency)
- GCP project with Vertex AI API enabled (`aiplatform.googleapis.com`)
- Service account with `roles/aiplatform.user` IAM role
- Service account JSON key mounted into the `ai-engine` container

## Risks / Open Questions

- **Token refresh**: Bearer tokens expire after 1 hour. Current implementation refreshes once per `start_session()` call. For very long-running calls (>1h), the token could expire mid-session. Mitigation: most telephony calls are well under 1h; a follow-up can add proactive refresh if needed.
- **Vertex AI model availability**: `gemini-live-2.5-flash-native-audio` availability may vary by region. `us-central1` is the recommended default.
- **Container key mount**: Service account JSON must be bind-mounted into the container. This requires a `docker-compose.yml` volume entry — operators must configure this manually (documented in `.env.example`).

## Implementation Summary

**Files Changed**: 17  
**Lines Added**: 1,406  
**Lines Removed**: 88

### Key Fixes Applied

1. **Endpoint format**: Changed from `v1beta1` to `v1` for Vertex AI
2. **Model path**: Full resource path `projects/{project}/locations/{location}/publishers/google/models/{model}`
3. **Tool response format**: Removed unsupported `id` field for Vertex AI
4. **Farewell handling**: Immediate farewell prompt for both API modes (3s delay was unreliable)
5. **Admin UI auth**: Switched from `fetch` to `axios` for proper JWT auth
6. **Credential storage**: Fixed path to `/app/project/secrets/gcp-service-account.json`
7. **Preflight**: Added `check_secrets_permissions()` for credential directory handling

### Production Validation

- **Vertex AI mode**: Tested with tool calling (hangup_call) — farewell spoken correctly
- **Developer API mode**: Regression tested — farewell spoken correctly
- **Admin UI**: Upload, verify, delete credentials all functional

---

**Milestone Completed**: February 19, 2026  
**Branch**: `google-vertex-api`  
**Linear**: [AAVA-191](https://linear.app/hkjarral/issue/AAVA-191)
