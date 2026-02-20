# Milestone 25: Google Vertex AI Live API Support

**Status**: ✅ Complete  
**Date**: February 19, 2026  
**Version**: v6.2.1  
**Branch**: `google-vertex-api`  
**Linear**: [AAVA-191](https://linear.app/hkjarral/issue/AAVA-191)

## Summary

Added opt-in Google Vertex AI Live API support to the existing `google_live` provider, enabling enterprise GCP deployments with OAuth2/ADC authentication and access to GA models that fix the known function calling 1008 bug present in Developer API preview models.

## Motivation

The existing `google_live` provider connects to `generativelanguage.googleapis.com` using an API key. This works well for development but has two limitations:

1. **Function calling 1008 bug**: Developer API preview models (`gemini-2.5-flash-native-audio-preview-*`) have a server-side bug where function calls (hangup, transcript, email) trigger WebSocket close code 1008 ~1 in 5–10 attempts. This is a known upstream issue (googleapis/python-genai #843, open since May 2025).

2. **Enterprise requirements**: GCP/enterprise users need service account auth, VPC-SC compliance, and SLA guarantees — all of which require Vertex AI.

Google has fixed the function calling bug in the Vertex AI GA model `gemini-live-2.5-flash-native-audio`, but this model is **only available via the Vertex AI endpoint**, not the Developer API.

## Design

### Approach

Opt-in via `use_vertex_ai: true` in provider config. Default is `false` — existing Developer API users see zero changes.

### Authentication Differences

| Feature | Developer API | Vertex AI |
| ------- | ------------- | --------- |
| Endpoint | `generativelanguage.googleapis.com` | `{location}-aiplatform.googleapis.com` |
| Auth | `?key=API_KEY` query param | `Authorization: Bearer TOKEN` header |
| Token | Static API key | OAuth2 bearer (1h TTL, auto-refreshed via ADC) |
| Model path | `models/{model}` | `projects/{project}/locations/{location}/publishers/google/models/{model}` |
| Project/Location | Not required | Required |

### Token Acquisition

Uses `google.auth.default()` with `cloud-platform` scope via the `google-auth` library (lightweight, no heavy SDK). Runs in executor to avoid blocking the async event loop. Token is refreshed per-session (1h TTL is sufficient for call sessions).

## Implementation

### Backend Changes

#### 1. Configuration Model (`src/config.py`)

Added new fields to `GoogleProviderConfig`:

```python
use_vertex_ai: bool = False
vertex_project: Optional[str] = None
vertex_location: str = "us-central1"
```

#### 2. Environment Injection (`src/config/security.py`)

Added secure environment variable injection:

```python
"GOOGLE_CLOUD_PROJECT": os.getenv("GOOGLE_CLOUD_PROJECT", ""),
"GOOGLE_CLOUD_LOCATION": os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
```

#### 3. Provider Implementation (`src/providers/google_live.py`)

Key changes:
- **OAuth2 token acquisition**: Uses `google.auth.default()` with async executor
- **Endpoint construction**: `wss://{location}-aiplatform.googleapis.com/ws/google.cloud.aiplatform.v1.LlmBidiService/BidiGenerateContent`
- **Model path**: Full resource path format for Vertex AI
- **Tool response format**: Removed `id` field (not supported by Vertex AI)
- **Farewell handling**: Immediate prompt for both API modes (fixes unreliable 3s delay)

#### 4. Dependencies (`requirements.txt`)

```txt
google-auth>=2.0.0
```

#### 5. Credential Management Endpoints (`admin_ui/backend/api/config.py`)

New API endpoints:

| Endpoint | Method | Description |
| -------- | ------ | ----------- |
| `/api/config/vertex-ai/regions` | GET | List available GCP regions |
| `/api/config/vertex-ai/credentials` | GET | Get uploaded credential status |
| `/api/config/vertex-ai/credentials` | POST | Upload service account JSON |
| `/api/config/vertex-ai/credentials` | DELETE | Delete uploaded credentials |
| `/api/config/vertex-ai/credentials/verify` | POST | Verify credentials are valid |

Credential storage: `/app/project/secrets/gcp-service-account.json`

### Frontend Changes

#### 1. Provider Form (`admin_ui/frontend/.../GoogleLiveProviderForm.tsx`)

New UI components:
- **Vertex AI toggle**: Checkbox to enable/disable Vertex AI mode
- **GCP Project ID input**: Auto-filled from uploaded JSON
- **Region dropdown**: 10+ GCP regions with friendly labels
- **Service Account JSON upload**: Drag-and-drop file upload
- **Verify Credentials button**: Tests OAuth2 token acquisition
- **Credential display**: Shows uploaded file metadata with delete option
- **Model filtering**: Greys out incompatible models based on API mode
- **Auto-switch**: Automatically selects appropriate model when toggling modes

#### 2. Environment Page (`admin_ui/frontend/.../EnvPage.tsx`)

Added known environment variables:
- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CLOUD_LOCATION`
- `GOOGLE_APPLICATION_CREDENTIALS`

#### 3. Setup Wizard (`admin_ui/frontend/.../Wizard.tsx`)

Info banner when Google Live selected directing users to Providers page for Vertex AI configuration.

#### 4. Sidebar (`admin_ui/frontend/.../Sidebar.tsx`)

Updated navigation to include Vertex AI configuration path.

### Infrastructure Changes

#### 1. Preflight Script (`preflight.sh`)

Added `check_secrets_permissions()` function:
- Creates `secrets/` directory if missing
- Sets ownership to container UID (1000)
- Sets permissions to 2770 for directory, 660 for files
- Called in main execution and re-validation

#### 2. Golden Config (`config/ai-agent.golden-google-live.yaml`)

Added commented Vertex AI configuration example.

#### 3. Environment Example (`.env.example`)

Added documented Vertex AI section with step-by-step setup instructions.

## Files Changed

| File | Lines | Description |
| ---- | ----- | ----------- |
| `src/providers/google_live.py` | +173/-15 | Vertex AI support, farewell fix |
| `admin_ui/backend/api/config.py` | +194 | Credential endpoints |
| `admin_ui/frontend/.../GoogleLiveProviderForm.tsx` | +338/-13 | UI components |
| `docs/contributing/api-reference.md` | +265/-3 | API documentation |
| `tests/test_google_live_vertex_ai.py` | +157 | Unit tests |
| `preflight.sh` | +60 | Secrets permissions |
| `admin_ui/backend/main.py` | +56/-1 | Router registration |
| `admin_ui/frontend/.../Wizard.tsx` | +44/-11 | Info banner |
| `.env.example` | +25 | Vertex AI section |
| `admin_ui/frontend/.../EnvPage.tsx` | +17/-1 | Env vars |
| `config/ai-agent.golden-google-live.yaml` | +15 | Vertex example |
| `admin_ui/frontend/.../Sidebar.tsx` | +10 | Navigation |
| `src/config.py` | +9 | Config fields |
| `src/config/security.py` | +7 | Env injection |
| `requirements.txt` | +4 | google-auth |
| `README.md` | +2/-1 | Documentation |

**Total**: 17 files, +1,406 / -88 lines

## Critical Fixes

### 1. Vertex AI Endpoint Format

**Problem**: Used `v1beta1` endpoint  
**Solution**: Changed to `v1` per Google's official examples  
**Impact**: Successful WebSocket connection

### 2. Model Resource Path

**Problem**: Used `publishers/google/models/{model}`  
**Solution**: Full path `projects/{project}/locations/{location}/publishers/google/models/{model}`  
**Impact**: Model correctly resolved by Vertex AI

### 3. Tool Response Format

**Problem**: Included `id` field in `functionResponses`  
**Solution**: Removed `id` field for Vertex AI (only `name` and `response`)  
**Impact**: Tool responses accepted without "Invalid JSON payload" errors

### 4. Farewell Handling

**Problem**: 3-second delay before farewell prompt was unreliable  
**Solution**: Send farewell prompt immediately for both API modes  
**Impact**: Farewell always spoken before disconnect

### 5. Admin UI Authentication

**Problem**: Used `fetch` API which bypassed JWT auth interceptors  
**Solution**: Switched to `axios` for all Vertex AI endpoints  
**Impact**: 401 errors resolved, uploads work correctly

### 6. Credential Storage Path

**Problem**: Path `/opt/asterisk-ai/secrets/` not writable by container  
**Solution**: Changed to `/app/project/secrets/` (bind-mounted)  
**Impact**: Credentials persist correctly

### 7. Delete Confirmation UI

**Problem**: Native browser `confirm()` dialog inconsistent with app  
**Solution**: Use `useConfirmDialog` hook with `toast` notifications  
**Impact**: Consistent UI experience

## Testing

### Unit Tests

- [x] Endpoint URL construction for both modes
- [x] Model path format (`models/` vs full resource path)
- [x] `use_vertex_ai=False` default leaves existing behavior unchanged

### Integration Tests

- [x] Manual regression: Developer API call → farewell spoken correctly
- [x] Manual validation: Vertex AI call → connection, audio, tool execution all working
- [x] Health check: `/health` reports `google_live` ready in both modes

### Admin UI Tests

- [x] Vertex AI toggle shows/hides fields correctly
- [x] Model dropdown filters and auto-switches
- [x] JSON upload with drag-and-drop
- [x] Verify credentials button
- [x] Delete credentials with confirmation dialog
- [x] Region dropdown populated

## Configuration Example

### Developer API (Default)

```yaml
providers:
  google_live:
    enabled: true
    api_key: ${GOOGLE_API_KEY}
    llm_model: gemini-2.5-flash-native-audio-preview-12-2025
    # use_vertex_ai: false  (default)
```

### Vertex AI (Enterprise)

```yaml
providers:
  google_live:
    enabled: true
    use_vertex_ai: true
    vertex_project: ${GOOGLE_CLOUD_PROJECT}
    vertex_location: ${GOOGLE_CLOUD_LOCATION}
    llm_model: gemini-live-2.5-flash-native-audio
    # api_key is NOT used in Vertex AI mode
```

### Environment Variables

```env
# Vertex AI configuration
GOOGLE_CLOUD_PROJECT=my-gcp-project-id
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=/app/project/secrets/gcp-service-account.json
```

## Dependencies

- `google-auth>=2.0.0` (new Python dependency)
- GCP project with Vertex AI API enabled (`aiplatform.googleapis.com`)
- Service account with `roles/aiplatform.user` IAM role
- Service account JSON key uploaded via Admin UI or mounted manually

## Known Limitations

1. **Token refresh**: Bearer tokens expire after 1 hour. Current implementation refreshes once per `start_session()` call. For calls exceeding 1 hour, the token could expire mid-session. Mitigation: most telephony calls are well under 1 hour.

2. **Region availability**: `gemini-live-2.5-flash-native-audio` availability may vary by region. `us-central1` is the recommended default.

3. **TTS voices**: All 8 voices (Aoede, Kore, Leda, Puck, Charon, Fenrir, Orus, Zephyr) work with both API modes — no filtering needed.

## Production Validation

| Test | Result | Date |
| ---- | ------ | ---- |
| Vertex AI mode + tool calling | ✅ PASSED | 2026-02-19 |
| Developer API regression | ✅ PASSED | 2026-02-19 |
| Admin UI credential flow | ✅ PASSED | 2026-02-19 |
| Preflight permissions | ✅ Added | 2026-02-19 |

---

**Milestone Completed**: February 19, 2026  
**Related**: Milestone 17 (Google Live Provider initial implementation)
