# Milestone 19: Admin UI Implementation

Note (CLI v5.0): The public CLI surface is now `agent setup`, `agent check`, `agent rca`, and `agent version`. Legacy command names used below (`agent quickstart`, etc.) remain available as hidden aliases for compatibility.

**Version**: v4.4.1  
**Date**: November 30, 2025  
**Status**: ✅ Complete and Ready for Release  
**Duration**: 2 weeks (design, implementation, testing, documentation)  
**Impact**: Modern web interface replaces CLI-based setup and configuration workflow

---

## Achievement Summary

Implemented a production-ready web-based administration interface for the Asterisk AI Voice Agent, providing visual configuration management, real-time monitoring, and system control. The Admin UI replaces the command-line `agent quickstart` wizard while maintaining full backward compatibility with CLI tools.

## Key Deliverables

### 1. Frontend Application (React + TypeScript)
- **Technology Stack**:
  - React 18.2 with TypeScript
  - Vite build system
  - TailwindCSS for styling
  - Monaco Editor for YAML editing
  - Axios for API communication
  - React Router for navigation
  
- **Core Pages**:
  - **Dashboard**: System metrics (CPU, memory, disk), container status
  - **Setup Wizard**: 5-step provider configuration with validation
  - **Providers Management**: Add/edit/delete AI service providers
  - **Pipelines**: Visual pipeline builder (STT → LLM → TTS)
  - **Contexts**: AI personality and behavior configuration
  - **Audio Profiles**: Audio encoding and sample rate settings
  - **Advanced Settings**: VAD, streaming, LLM, transport, barge-in
  - **Environment Editor**: Visual `.env` file management
  - **Raw YAML Editor**: Direct config editing with syntax validation
  - **Logs**: Raw container logs + call-centric Troubleshoot timeline
  - **Container Management**: Start/stop/restart controls

### 2. Backend API (FastAPI + Python)
- **File**: `admin_ui/backend/main.py`, `admin_ui/backend/api/*`
- **Features**:
  - RESTful API with OpenAPI/Swagger documentation
  - JWT-based authentication system
  - Config file management (YAML, .env, users.json)
  - Docker container control via Docker socket
  - System metrics collection (psutil)
  - WebSocket endpoint for log streaming
  - Provider connection testing
  - API key validation

### 3. Authentication & Security
- **File**: `admin_ui/backend/auth.py`
- **Implementation**:
  - JWT tokens with 24-hour expiry
  - Password hashing (pbkdf2_sha256)
  - OAuth2 password flow
  - Route protection via FastAPI dependencies
  - Auto-created default admin user (admin/admin)
  - Change password functionality
  - User data persistence (config/users.json)
  - Optional custom JWT secret for production

### 4. Docker Integration
- **File**: `admin_ui/Dockerfile`
- **Multi-Stage Build**:
  - Stage 1: Node.js 20 (frontend build)
  - Stage 2: Python 3.10 (backend runtime)
  - Frontend assets copied to backend static directory
  - Single container deployment
  - Port 3003 exposed
  - Volume mounts for config access

### 5. Setup Wizard
- **Flow**:
  1. Provider Selection (OpenAI, Deepgram, Google Live, Local Hybrid)
  2. API Key Entry with real-time validation
  3. Asterisk ARI connection testing
  4. AI Personality (greeting, name, role)
  5. Configuration save to `ai-agent.yaml` and `.env`
  
- **Features**:
  - Skip wizard if config exists
  - Validation before progression
  - Error handling with clear messages
  - Auto-restart of ai-engine after setup

### 6. Provider Management
- **Supported Providers**:
  - OpenAI Realtime (full agent)
  - Deepgram Voice Agent (full agent)
  - Google Live API (full agent)
  - ElevenLabs Agent (full agent) - Added in v4.4.1
  - OpenAI Pipeline (STT/LLM/TTS components)
  - Local Provider (local STT/LLM/TTS)
  - Generic Provider (custom configurations)

- **Features**:
  - Enable/disable toggle per provider
  - Connection testing (API key validation)
  - Model selection dropdowns (provider-specific)
  - Advanced configuration (VAD, audio encoding, etc.)
  - Dynamic form rendering based on provider type

### 7. Configuration Management
- **YAML Integration**:
  - Read/write `config/ai-agent.yaml`
  - Schema validation
  - Nested structure preservation
  - Backup on save (optional)

- **Environment Variables**:
  - Read/write `.env` file
  - Sensitive data handling (API keys)
  - Variable validation

- **User Management**:
  - User credentials in `config/users.json`
  - Password hashing
  - Multiple user support (foundation for RBAC in v1.1)

### 8. System Monitoring
- **Metrics Collected**:
  - CPU usage (per core and total)
  - Memory usage (used/total/percentage)
  - Disk usage (used/total/percentage)
  - Container status (running/stopped/exited)
  - Container health checks

- **Dashboard Features**:
  - Real-time updates (5-second polling)
  - Container restart buttons
  - Quick status indicators
  - Error handling and display

### 9. Logs (Raw + Troubleshoot)

The Admin UI Logs page supports two workflows:

- **Raw** (default): quick level filtering over container logs (tail-based).
- **Troubleshoot**: a call-centric, filterable timeline built from existing ai-engine logs (no ai-engine logging changes required).

**Implementation**:
- Backend reads Docker container logs via Docker socket.
- Frontend polls at short intervals for live updates (and preserves user scroll position unless pinned to bottom).
- Container selection supported (`ai_engine`, `local_ai_server`, `admin_ui`).

### 10. Documentation
- **Files Created**:
  - `admin_ui/UI_Setup_Guide.md`: Complete setup and deployment guide
  - `archived/RELEASE_PLAN.md`: Internal planning (archived)

- **Coverage**:
  - Quick start (Docker)
  - Standalone deployment (with daemon modes)
  - Production deployment (Nginx, Traefik, HTTPS)
  - Security configuration (JWT, passwords, firewall)
  - Troubleshooting guide
  - Upgrade path from CLI
  - Port reference and file locations

---

## Production Validation

### Testing Completed

**Environment**: Development server (Ubuntu 22.04, Docker 24.0.7)

**Scenarios Tested**:
1. ✅ Fresh installation with setup wizard
2. ✅ Existing config detection (wizard skip)
3. ✅ Provider CRUD operations
4. ✅ Pipeline configuration with component selection
5. ✅ Context management with provider override
6. ✅ Audio profile editing (nested YAML)
7. ✅ Environment variable editing
8. ✅ Raw YAML editor with validation
9. ✅ Container restart from dashboard
10. ✅ Live log streaming
11. ✅ Authentication flow (login/logout)
12. ✅ Password change functionality
13. ✅ Token expiry and renewal
14. ✅ Docker build and deployment
15. ✅ Standalone deployment with nohup

**Security Testing**:
- ✅ Unauthenticated access blocked (401 responses)
- ✅ Token validation working
- ✅ Password hashing verified
- ✅ Default credentials changeable
- ✅ Route protection on all APIs
- ✅ CORS configuration correct

---

## Addendum: Logs & Troubleshoot (Implemented)

**Goal**: Make troubleshooting calls dramatically faster by turning raw container logs into a call-centric, filterable timeline, with a one-click jump from Call History.

This builds on the existing Logs and Call History pages by adding:
- a structured **Troubleshoot** view over Docker logs
- call-finding via Call History-style filters
- correlation using `call_id` plus related channel IDs observed in ai-engine logs

### Problem Statement

Today, users troubleshooting an issue must manually scroll raw logs and grep mentally for:
- which call they are looking at
- key milestones (transport start, provider session ACK, audio profile, call end)
- signal quality issues (resampling, underruns, format switches)

This is slow and error-prone, especially for new users.

### Definitions

- **call_id** (canonical): The Asterisk caller channel id for a call session. In this codebase, `call_id == caller_channel_id`.
- **Caller ID**: The phone identity (number/name). In Call History: `caller_number`, `caller_name`.

Caller ID is not guaranteed to appear in container logs, so the UI workflow is:
1) search calls by Caller ID in Call History
2) select a specific call record → use its `call_id` to filter logs

### User Experience (UX)

#### A) Logs → Raw (default)

Use when you want a quick “what’s currently broken?” view.

Raw features:
- Container selector
- **Levels** multi-select (`error`, `warning`, `info`, `debug`) with default `warning+error`
- Free-text search
- Preserves ANSI coloring from container output

#### B) Logs → Troubleshoot (call-centric)

Use when you’re investigating a specific call and need an end-to-end narrative.

Troubleshoot features:
- **Find Call** panel (same filter shape as Call History): caller number/name, provider, pipeline, context, outcome, date range
- Selecting a call launches a **timeline** for that call
- Views (server-side focused fetch so mid-call events aren’t dropped):
  - `Overview` (milestones + warnings/errors)
  - `Issues` (warnings/errors + “signal” info lines)
  - `Provider`
  - `Media` (audio + transport)
  - `Barge-in / VAD`
  - `Tools`
  - `All`
- Toggles:
  - **Include debug** (default ON)
  - **Hide transcripts / payloads** (default ON)
  - **Hide repeats** (default ON)
- Correlation:
  - Filters by `call_id` and automatically expands to related channel IDs observed in logs (ExternalMedia/Local legs).
  - If `since/until` is not provided, the backend resolves a time window from Call History (best-effort) and pads it, so the call isn’t missed due to tail truncation.

#### B) Call History → “Troubleshoot” (one click)

In Call History call details, add a **Troubleshoot** action:
- Opens Logs page in **Troubleshoot** mode
- Pre-fills:
  - `container=ai_engine`
  - `call_id=<selected call_id>`
  - `since=<start_time - padding>` and `until=<end_time + padding>` when available

### Backend API (FastAPI)

Keep existing raw logs endpoint intact:
- `GET /api/logs/{container}?tail=500` → raw text

Add a new parsed events endpoint:
- `GET /api/logs/{container}/events`

Query parameters:
- `call_id` (optional)
- `levels` (repeatable) or comma-separated
- `categories` (repeatable) or comma-separated
- `q` (optional contains match)
- `hide_payloads=true|false` (default true)
- `since` / `until` (ISO 8601; optional)
- `since_seconds_ago` (optional, convenience)
- `limit` (default 500)

Response:
- `events: [ { ts, level, msg, component, call_id, provider, context, pipeline, category, milestone, raw } ]`

Parsing requirements:
- Strip ANSI codes server-side for parsing (Raw view preserves the original ANSI lines).
- Parse structured log format best-effort:
  - timestamp, level, message, component/logger, and key=value fields (extract `call_id`, `provider`, `context`, `pipeline` when present).
- Categorize events using a small, explicit pattern table (maintainable).
- Mark `milestone=true` for known info-level milestones.

### Acceptance Criteria

- A user can troubleshoot a call in <30 seconds by:
  1) finding call in Call History
  2) clicking **Troubleshoot**
  3) seeing provider/session events, media setup, audio profile, barge-in/VAD, tools, call end, and any warnings/errors in one timeline
- Filtering by `call_id` yields stable, correct results.
- “Hide transcripts / payloads” removes transcript/control spam by default.
- Raw logs view remains available.

### Phase 2 (Deferred)

- Persist event timelines to Call History DB for long retention (outside scope).
- Multi-container correlation (ai_engine + local_ai_server) in a single merged timeline (outside scope).

**Browser Compatibility**:
- ✅ Chrome 120+ (primary)
- ✅ Firefox 121+
- ✅ Safari 17+
- ✅ Edge 120+

---

## Architecture

### System Diagram

```
┌─────────────────────────────────────────────────────────┐
│                     Browser (Port 3003)                  │
│  ┌──────────────────────────────────────────────────┐   │
│  │   React Frontend (Vite Build)                    │   │
│  │   - Setup Wizard                                 │   │
│  │   - Configuration Pages                          │   │
│  │   - System Dashboard                             │   │
│  │   - Live Logs                                    │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
                          │ HTTP/WebSocket
                          ▼
┌─────────────────────────────────────────────────────────┐
│            FastAPI Backend (Container)                   │
│  ┌──────────────────────────────────────────────────┐   │
│  │   API Endpoints                                  │   │
│  │   /api/auth/*     - Authentication               │   │
│  │   /api/config/*   - YAML/Env management         │   │
│  │   /api/wizard/*   - Setup wizard                │   │
│  │   /api/system/*   - Metrics & containers        │   │
│  │   /api/logs/*     - WebSocket log stream        │   │
│  └──────────────────────────────────────────────────┘   │
│              │              │              │             │
│              ▼              ▼              ▼             │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────┐   │
│  │ config/      │  │ Docker       │  │ psutil     │   │
│  │ - ai-agent.  │  │ Socket       │  │ (metrics)  │   │
│  │   yaml       │  │              │  │            │   │
│  │ - .env       │  │              │  │            │   │
│  │ - users.json │  │              │  │            │   │
│  └──────────────┘  └──────────────┘  └────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### Data Flow

**Configuration Save**:
```
User Edit → Frontend Validation → API Request → Backend Validation 
→ File Write (YAML/env) → Success Response → Frontend Update
```

**Live Logs**:
```
WebSocket Connect → Backend subscribes to Docker logs 
→ Stream logs to client → Frontend displays with auto-scroll
```

**Authentication**:
```
Login Form → POST /api/auth/login → Validate credentials 
→ Generate JWT → Store in localStorage → Attach to all requests
```

---

## Technical Challenges & Solutions

### Challenge 1: Nested YAML Structure Preservation
**Problem**: React forms flatten structures, losing YAML nesting  
**Solution**: Implemented recursive update functions with path-based field updates  
**Files**: `ProfilesPage.tsx`, `updateNestedField` helper

### Challenge 2: Provider-Specific Form Rendering
**Problem**: Each provider has different configuration fields  
**Solution**: Dynamic form component selection based on provider type, with GenericProviderForm as fallback  
**Files**: `providers/*ProviderForm.tsx`, `ProvidersPage.tsx`

### Challenge 3: Real-Time Log Streaming
**Problem**: Docker logs need to stream to browser in real-time  
**Solution**: WebSocket connection with backend tailing Docker logs API  
**Files**: `backend/api/logs.py`, `frontend/pages/LogsPage.tsx`

### Challenge 4: Docker Socket Access
**Problem**: Container needs to control other containers  
**Solution**: Mount `/var/run/docker.sock` as volume (documented security implications)  
**Files**: `docker-compose.yml`, security section in UI_Setup_Guide.md

### Challenge 5: JWT Secret Management
**Problem**: Production needs secure JWT, development needs simplicity  
**Solution**: Optional JWT_SECRET environment variable with secure default, clear production instructions  
**Files**: `auth.py`, UI_Setup_Guide.md security section

### Challenge 6: Wizard State Management
**Problem**: Multi-step wizard with validation and error handling  
**Solution**: React state machine with step-based navigation and validation  
**Files**: `Wizard.tsx`

---

## Migration Path

### For New Users
1. Start admin-ui container
2. Access http://localhost:3003
3. Login with admin/admin
4. Complete setup wizard
5. Change password

### For Existing Users
1. Pull latest code
2. Start admin-ui container
3. Existing config auto-detected
4. Wizard skipped
5. Configuration editable via UI
6. CLI tools continue to work

---

## API Endpoints

### Authentication
- `POST /api/auth/login` - Get JWT token
- `POST /api/auth/change-password` - Update password
- `GET /api/auth/me` - Get current user info

### Configuration
- `GET /api/config/yaml` - Get ai-agent.yaml content
- `POST /api/config/yaml` - Update ai-agent.yaml
- `GET /api/config/env` - Get .env variables
- `POST /api/config/env` - Update .env variables
- `POST /api/config/providers/test` - Test provider connection

### Wizard
- `GET /api/wizard/status` - Check if setup complete
- `POST /api/wizard/validate-key` - Validate API key
- `POST /api/wizard/test-asterisk` - Test Asterisk connection
- `POST /api/wizard/save` - Save wizard configuration
- `POST /api/wizard/skip` - Skip wizard setup

### System
- `GET /api/system/metrics` - Get system metrics
- `GET /api/system/containers` - List Docker containers
- `POST /api/system/containers/{name}/restart` - Restart container

### Logs
- `WebSocket /api/logs/stream` - Stream container logs

---

## Future Enhancements (v1.1+)

### Planned for v1.1 (Q1 2026)
1. **Call History & Analytics** - View past calls, transcripts, durations
2. **YAML Diff Preview** - Show changes before saving configuration
3. **Log Filtering** - Filter logs by level, component, timestamp
4. **Multi-User Support** - Role-based access control (Admin, Operator, Viewer)
5. **2FA Authentication** - Two-factor authentication with TOTP

### Planned for v2.0 (Q2 2026)
1. **Configuration Templates** - Quick setup presets for common scenarios
2. **A/B Testing** - Compare provider configurations
3. **Webhook Management** - Configure event notifications
4. **Advanced Analytics** - Call metrics, provider performance graphs
5. **API Documentation UI** - Interactive Swagger/OpenAPI interface

---

## Dependencies

### Frontend
- React 18.2.0
- TypeScript 5.2.2
- Vite 5.1.0
- TailwindCSS 3.4.1
- Monaco Editor 4.6.0
- Axios 1.6.7
- React Router 6.22.0

### Backend
- FastAPI 0.109.0
- Uvicorn 0.27.0
- PyYAML 6.0.1
- python-jose 3.3.0 (JWT)
- passlib 1.7.4 (password hashing)
- psutil 5.9.8 (system metrics)
- docker 7.0.0 (container control)

---

## Metrics

### Code Statistics
- **Frontend**: ~8,500 lines (TypeScript/TSX)
- **Backend**: ~2,000 lines (Python)
- **Documentation**: ~1,500 lines (Markdown)
- **Total Files**: 65 files
- **Components**: 25 React components
- **API Endpoints**: 23 endpoints

### Development Time
- **Design & Planning**: 2 days
- **Frontend Implementation**: 5 days
- **Backend Implementation**: 3 days
- **Authentication System**: 2 days
- **Testing & Bug Fixes**: 2 days
- **Documentation**: 2 days
- **Total**: 16 days (2 calendar weeks)

### Testing Coverage
- **Manual Test Scenarios**: 15 scenarios, 15 passed
- **Security Tests**: 6 scenarios, 6 passed
- **Browser Compatibility**: 4 browsers tested
- **Deployment Methods**: 3 methods tested (Docker, standalone, production)

---

## Lessons Learned

### What Went Well
1. **Multi-stage Docker build** simplified deployment significantly
2. **JWT authentication** was straightforward with FastAPI
3. **React component reusability** sped up page development
4. **Monaco Editor** provided excellent YAML editing experience
5. **Docker socket mount** made container control simple

### Challenges Faced
1. **Nested YAML editing** required careful state management
2. **Provider-specific forms** needed abstraction for maintainability
3. **WebSocket log streaming** required proper error handling
4. **Docker volume permissions** needed documentation for troubleshooting
5. **JWT secret configuration** balance between security and ease-of-use

### Improvements for Future
1. **Form validation library** would simplify error handling
2. **State management library** (Redux/Zustand) for complex state
3. **End-to-end tests** (Playwright/Cypress) for regression prevention
4. **TypeScript strict mode** for better type safety
5. **Component library** (shadcn/ui) for consistent UI

---

## Release Checklist

### Pre-Release
- [x] All features implemented
- [x] Security audit passed
- [x] Documentation complete
- [x] Docker build tested
- [x] Standalone deployment tested
- [x] Port changed to 3003
- [x] Version updated to 1.0.0
- [x] CHANGELOG updated
- [x] README updated
- [x] Milestone document created
- [x] ROADMAP updated

### Release Day (Sunday, Nov 30, 2025)
- [ ] Merge to develop branch
- [ ] Test on development server
- [ ] Monitor for issues
- [ ] Collect feedback
- [ ] Address critical bugs
- [ ] Merge to main (if stable)

### Post-Release
- [ ] Announcement (Discord, GitHub)
- [ ] Gather user feedback
- [ ] Plan v1.1 features
- [ ] Update documentation based on feedback

---

## Conclusion

Milestone 19 successfully delivers a modern, production-ready web interface for the Asterisk AI Voice Agent. The Admin UI replaces the CLI-based setup workflow while maintaining full backward compatibility, providing users with visual configuration management, real-time monitoring, and comprehensive system control. The foundation is solid for future enhancements including multi-user support, analytics, and advanced features.

**Status**: ✅ **Ready for Production Release**

---

**Document Version**: 1.1  
**Last Updated**: December 3, 2025  
**Next Milestone**: Milestone 20 - ElevenLabs Provider (v4.4.1)
