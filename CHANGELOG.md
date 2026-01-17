# Changelog

All notable changes to the Asterisk AI Voice Agent project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned

- Additional provider integrations
- Enhanced monitoring features

### Added

- Admin UI Dashboard: show current project version in the **System Ready** card (best-effort detection via `git describe` with fallback to README parsing; supports `AAVA_PROJECT_VERSION` override).

### Changed

- Docs/CLI: align operator-facing version references to `v5.1.4` (avoid hardcoded `v5.0` strings in CLI help/output where possible).

### Fixed

- Docker builds: pin base OS/runtime to avoid upstream tag drift (`admin_ui`/`ai_engine`: Debian 12 `bookworm` + Python 3.11; `local_ai_server`: Debian 13 `trixie` + Python 3.11 for Kroko compatibility).
- Ollama pipelines: prevent overly-eager `hangup_call` tool calls from ending calls when the caller did not indicate end-of-call intent; support `num_ctx` pass-through and honor `tools_enabled` for tool calling.
- Email transcripts: improve Outlook compatibility by rendering transcript newlines as `<br/>` (HTML-escaped) so caller/AI lines display correctly across clients.

## [5.0.1] - 2026-01-14

### Fixed

- **CLI Command Names**: v5.0.0 binaries were built with old command names; this release includes the updated CLI:
  - `agent check` - Standard diagnostics report (renamed from `agent doctor`)
  - `agent rca` - Post-call root cause analysis (renamed from `agent troubleshoot`)
  - `agent setup` - Interactive setup wizard (renamed from `agent init`)
  - Legacy aliases (`doctor`, `troubleshoot`, `init`, `demo`) remain available as hidden commands for backward compatibility

- **Preflight Script - Remote Asterisk Support** (AAVA-150):
  - New `check_data_permissions()` function runs regardless of Asterisk location
  - Fixes `call_history.db` read-only error for users with remote/containerized Asterisk
  - Detects and fixes root-owned database files that container user cannot write to

- **Preflight Script - Symlink Handling** (AAVA-150):
  - Non-empty directories at `/var/lib/asterisk/sounds/ai-generated` now auto-backed up with `--apply-fixes`
  - Fixes the "ai-generated/ai-generated/" double-path issue
  - Prevents silent symlink creation failures

## [5.0.0] - 2026-01-07

### Added

- Outbound Campaign Dialer (**Alpha**) (Admin UI → Call Scheduling):
  - Campaign scheduler (single-node) with pacing + concurrency controls
  - CSV lead import (skip-existing), lead actions (recycle/reset/ignore/delete)
  - Dialplan-assisted voicemail detection via Asterisk `AMD()` and voicemail drop
  - Optional consent gate (DTMF `1` accept / `2` deny / timeout tracking)
  - Recording library (upload once, reuse across campaigns) + shipped default consent/voicemail prompts
- Groq Speech (STT + TTS) adapters for modular pipeline deployments
- Attended (warm) transfer tool with DTMF acceptance flow
- Preflight hardening and operator guidance (cross-platform checks, docker socket guidance, local-ai “minimal vs full” mode)

### Changed

- Standardized Docker Compose service/container names: `admin_ui`, `ai_engine`, `local_ai_server`
- Container base OS alignment:
  - `admin_ui`: Python 3.11 on Debian 12 (`bookworm`)
  - `ai_engine`: Python 3.11 on Debian 12 (`bookworm`)
  - `local_ai_server`: Python 3.11 on Debian 13 (`trixie`) (intentional for embedded Kroko/glibc compatibility)

### Fixed

- Outbound dialer reliability fixes (caller-id inheritance, outcome tracking, SQLite write/lock edge cases, duplicate side-effects)
- Admin UI stability improvements (tooltips, recording preview UX, safer compose/health behaviors)
- Ollama pipeline robustness (provider config resolution + tool-result handling improvements)

### Docs

- Added `docs/OUTBOUND_CALLING.md` and updated FreePBX + installation docs for v5.0.0

## [4.6.0] - 2025-12-29

### Added

- ARI connectivity enhancements:
  - `ASTERISK_ARI_PORT` support
  - `ASTERISK_ARI_SCHEME` (`http|https`) with `ws://` vs `wss://` alignment
  - `ASTERISK_ARI_SSL_VERIFY` toggle for self-signed or hostname mismatch environments
- Pipeline robustness: invalid pipelines are detected and fall back deterministically instead of silently using placeholder adapters
- Admin UI logging improvements: structured event support and improved Logs viewing UX

### Changed

- Admin UI config management: safer `.env` parsing/writes and clearer apply guidance (“save vs apply” determinism)
- Admin UI health checks: Tier 3/best-effort probe fallbacks with explicit warnings when configured overrides are unreachable
- Admin UI container actions: safer `admin_ui` restart behavior from within the UI
- Compose env semantics: `.env` is authoritative; avoid `${VAR:-default}` fallbacks in compose that prevent UI env changes from taking effect

### Fixed

- Preflight: Debian-family best-effort detection improvements and Debian 12 Docker repo codename fallback (`bookworm`) when `VERSION_CODENAME` is missing
- Admin UI Docker management hardening: restrict compose operations to AAVA services and reduce information exposure in error messages

### Docs

- Upgrade guidance: `v4.6.0 → v5.0.0` checklist in `docs/INSTALLATION.md`
- IPv6 policy: warn/recommend disabling IPv6 for GA stability and document mitigation steps
- Supported platforms: explicit Tier 3 best-effort guidance for openSUSE and Podman

## [4.5.3] - 2025-12-22

### Added

- ExternalMedia RTP hardening: remote endpoint pinning (`external_media.lock_remote_endpoint`) and allowlist support (`external_media.allowed_remote_hosts`)
- Tests for RTP routing/security and Prometheus label cardinality
- Admin UI backend: model switching mappings for `faster_whisper` STT and `melotts` TTS

### Changed

- Default provider now `local_hybrid` (pipeline-first GA default)
- Readiness probe is pipeline-aware when `default_provider` references a pipeline (e.g., `local_hybrid`)
- Prometheus metrics are low-cardinality only (removed per-call labels like `call_id`; per-call detail lives in Call History)

### Fixed

- ExternalMedia RTP SSRC routing: prevent cross-call audio mixing by using authoritative `call_id` in engine callback
- Admin UI HealthWidget rebuild payload: stop mis-parsing model/voice identifiers
- Local provider readiness badge: report “configured” vs “connected” semantics correctly

### Removed

- Legacy bundled Prometheus/Grafana monitoring stack and `monitoring/` assets from the main repo path (Call History-first debugging; bring-your-own monitoring)

### Improved (Onboarding & DX)

- Preflight is now **required** (not recommended) in all documentation
- Admin UI Dockerfile default bind aligned to `0.0.0.0` (matches docker-compose.yml for out-of-box accessibility)
- Prominent ASCII security warning box in `preflight.sh` and `install.sh` post-install output
- Timezone (`TZ`) now configurable via `.env` with `America/Phoenix` default
- README Quick Start includes verification step with health check command
- INSTALLATION.md Path A corrected: preflight required, proper service ordering
- Remote server access (`http://<server-ip>:3003`) now primary instruction alongside localhost

## [4.5.2] - 2025-12-16

### Added

- **Kokoro API mode**: OpenAI-compatible TTS endpoint (`KOKORO_MODE=api`)
- **Kroko Embedded models**: Downloadable from Admin UI Models Page
- **Model hot-swap**: Switch STT/TTS/LLM via WebSocket without container restart
- **MCP tool integration**: External tool framework with Admin UI config
- **Aviation ATIS tool**: Live METAR data from aviationweather.gov

### Changed

- Websockets connection logs moved to DEBUG level
- Local provider auto-reconnects on disconnect (12 min retry)

### Fixed

- Wizard: Kroko embedded detection, Kokoro voice selector alignment
- Compatibility: websockets 15.x, resend 2.x, sherpa-onnx 1.12.19

## [4.5.0] - 2025-12-11

### Fixed - Admin UI Stability 🔧

#### ConfigEditor Critical Fixes (A1-A3, A9)

- **Missing State Hooks**: Added `loading`, `saving`, `error`, `success`, `restartRequired` useState declarations
- **Duplicate Import**: Removed duplicate `AudioSocketConfig` import
- **Provider Type Persistence**: New providers now correctly save their `type` field
- **Provider Name Validation**: Empty provider names are now rejected with error message
- **Unused Code Cleanup**: Removed 15+ unused icon imports, reducing bundle size

#### Save Flow Improvements (A6)

- **Restart Required Banner**: UI now displays amber banner when config changes require engine restart
- **Toast Notifications**: Replaced browser `alert()` with inline dismissible notifications
- **Loading Spinner**: Added visual feedback during config fetch

#### Docker Operations (A4-A5)

- **Dynamic Path Resolution**: Uses `shutil.which()` to find docker-compose instead of hardcoded paths
- **Cleaner Restarts**: Uses `container.restart()` via Docker SDK instead of destructive stop/rm/up flow
- **Fallback Support**: Gracefully falls back to docker-compose if Docker SDK fails

#### Config File Safety (A8, A11, A12)

- **Atomic Writes**: Config and .env files written via temp file + atomic rename (prevents corruption on crash)
- **Backup Rotation**: Only keeps last 5 backups per file (prevents disk exhaustion)
- **Env Validation**: Rejects empty keys, newlines in values, and `=` in keys before writing .env

### Added - Stability Improvements 🛡️

#### Enhanced Timer Logging (L2)

- **Structured Timer Logs**: All timer operations now log with `[TIMER]` prefix for easy filtering
- **Timer Lifecycle Tracking**: Logs show scheduled, executed, and cancelled timer events
- **Pending Timer Count**: `get_pending_timer_count()` method exposes timer queue depth

#### Health Check Improvements (L4)

- **Uptime Tracking**: `/health` endpoint now returns `uptime_seconds`
- **Pending Timers**: Health response includes `pending_timers` count
- **Active Sessions**: Added `active_sessions` field (alias for `active_calls`)
- **Real Conversation Metrics**: `conversation` object now pulls live data from ConversationCoordinator

#### Graceful Shutdown Handler (M4)

- **SIGTERM Handling**: `docker stop` now waits up to 30 seconds for active calls to complete
- **Shutdown Logging**: `[SHUTDOWN]` prefixed logs track graceful shutdown progress
- **Configurable Timeout**: `engine.stop(graceful_timeout=30)` parameter for custom drain time

### Changed

#### Code Cleanup (H1)

- **Removed Legacy Code**: Deleted commented `active_calls` legacy code block from engine.py
- **SessionStore is Single Source**: All session state now uses SessionStore exclusively

## [4.4.3] - 2025-12-10

### Fixed - Admin UI Bug Fixes 🔧

#### Models Page
- **Installed Models Display**: Fixed parsing of nested API response structure
- **Model Delete**: Added `DELETE /api/local-ai/models` endpoint with path mapping
- **Error Messages**: Properly extract and display API error details (not generic "Request failed")

#### Dashboard
- **STT/TTS Dropdowns**: Show individual model names in optgroups instead of counts (e.g., "vosk-model-en" instead of "Vosk (2)")
- **Metrics Display**: Added null guards to prevent "NaN%" when backend is unavailable

#### Providers Page
- **Local Provider Form**: Fixed form visibility for full agent mode local providers
- **Currently Loaded Section**: Added live display of STT/LLM/TTS model status
- **Test Connection - Local**: Now tests actual local_ai_server WebSocket connection and verifies all 3 models are loaded
- **Test Connection - ElevenLabs**: Fixed validation using `/v1/voices` endpoint (was using `/v1/user` which requires special permissions)

#### Health Widget
- **Kroko/Kokoro Mode Detection**: Correctly parses embedded/local mode from health response paths

#### Model Switching
- **Container Restart**: Uses `docker-compose down/up` instead of Docker SDK to properly reload environment variables

### Added - Cross-Platform Support (AAVA-126) 🌍

#### Pre-flight Script (`preflight.sh`)
- **Comprehensive System Checks**: OS detection, Docker version, Compose version, architecture verification
- **Auto-fix Mode**: Run with `--apply-fixes` to automatically resolve fixable issues
- **Multi-distro Support**: Ubuntu, Debian, CentOS, RHEL, Rocky, Alma, Fedora, Sangoma/FreePBX
- **Rootless Docker Detection**: Proper handling for rootless Docker installations
- **SELinux Handling**: Automatic context fix commands for RHEL-family systems
- **Asterisk Detection**: Finds Asterisk config directory and FreePBX installations
- **Port Availability Check**: Verifies Admin UI port (3003) is available
- **Environment Setup**: Creates `.env` from `.env.example` if missing

#### Admin UI Integration
- **System Status Widget**: Dashboard displays preflight check results
- **Platform API**: `GET /api/system/platform` returns system compatibility info
- **Preflight API**: `POST /api/system/preflight` triggers fresh system check

### Added - Developer Experience 🛠️

- **React.lazy Code Splitting**: Heavy pages (Wizard, RawYaml, Terminal, Logs, Models) now lazy-loaded for faster initial bundle
- **ESLint + Prettier**: Added configuration with lint/format/audit npm scripts
- **Frontend README**: Documentation for setup, build, and available scripts

## [4.4.2] - 2025-12-08

### Added - Local AI Server Enhancements 🎯

#### New STT Backends
- **Kroko ASR Integration (AAVA-92)**: High-quality streaming ASR with 12+ languages
  - Hosted API support (`wss://app.kroko.ai`)
  - On-premise server support
  - No hallucination - factual transcripts only
  - Configure via `LOCAL_STT_BACKEND=kroko`
- **Sherpa-ONNX STT (AAVA-95)**: Local streaming ASR using sherpa-onnx
  - Low-latency streaming recognition
  - Multiple model support (Zipformer, etc.)
  - Configure via `LOCAL_STT_BACKEND=sherpa`

#### New TTS Backends
- **Kokoro TTS (AAVA-95)**: High-quality neural TTS
  - Multiple voices: `af_heart`, `af_bella`, `am_michael`
  - Natural prosody and intonation
  - Configure via `LOCAL_TTS_BACKEND=kokoro`
- **ElevenLabs TTS Adapter (AAVA-114)**: Cloud TTS for modular pipelines
  - Factory pattern integration
  - Premium voice quality

#### Model Management System (AAVA-99, 101, 102, 103, 104)
- **Dashboard Quick-Switch**: Change STT/TTS/LLM models directly from dashboard
- **Model Enumeration API**: `GET /api/local-ai/models/available`
- **Model Switch API**: `POST /api/local-ai/models/switch` with hot-reload
- **2-Step UI Flow (AAVA-111)**: "Pending" badge + "Apply & Restart" button
- **Error Handling (AAVA-108)**: Rollback on switch failure

### Added - Admin UI Improvements

- **Pipeline UI Backend Display (AAVA-116)**: Shows active STT/TTS backend for local components
- **Directory Health Card (AAVA-93)**: Dashboard shows media directory permissions
- **Pipeline Orchestrator Logging (AAVA-106)**: Logs active backends on startup
- **YAML Config Sync (AAVA-107)**: Model selection synced to `ai-agent.yaml`

### Added - DevOps & CI

- **Optional Build Args (AAVA-112)**: Exclude unused backends from Docker build
  - `INCLUDE_VOSK`, `INCLUDE_SHERPA`, `INCLUDE_PIPER`, `INCLUDE_KOKORO`, `INCLUDE_LLAMA`
  - Default: all enabled (backward compatible)
  - Reduces image size for specialized deployments
- **CI Image Size Checks (AAVA-113)**: Size budgets in GitHub Actions
  - ai-engine: 1.5GB budget
  - local-ai-server: 4GB budget
- **Enhanced Trivy Scanning (AAVA-113)**: Both images scanned for vulnerabilities
- **Outdated Dependency Reporting**: Warning in CI for outdated packages

### Added - Documentation

- **LOCAL_ONLY_SETUP.md**: Comprehensive guide for fully local deployment
  - Vosk, Sherpa-ONNX, Kroko STT options
  - Piper, Kokoro TTS options
  - Phi-3 LLM configuration
  - Hardware recommendations
- **Docker Build Troubleshooting (AAVA-119)**: DNS resolution, BuildKit issues
  - Solutions for `docker-compose` vs `docker compose`
  - Network configuration guides

### Fixed

- **Local Pipeline Validation (AAVA-118)**: Local components validate against websocket URLs
  - Pipeline no longer disabled on validation failure
  - Fixes "call drops after greeting" for local setups
- **TTS Response Contract (AAVA-105)**: JSON with base64 audio instead of binary frames
- **Docker Image Debloat (AAVA-109, 110)**: Removed unused dependencies
- **Config Validation (AAVA-115)**: Capability/suffix mismatch detection
- **Sherpa-ONNX API Handling**: Handle string return type from `get_result()`
- **Container Restart Logic**: Fixed docker-compose commands in Admin UI

### Changed

- **Wizard Model Detection (AAVA-98)**: Detects Sherpa STT and Kokoro TTS models
- **Status API (AAVA-96)**: Correctly reports kroko/sherpa/kokoro backends
- **Friendly Model Names**: Status shows basename instead of full path

### Technical Details

- **Files Added**: 
  - `docs/LOCAL_ONLY_SETUP.md`
  - `local_ai_server/requirements-base.txt`
- **Files Modified**:
  - `local_ai_server/Dockerfile` (conditional backend installs)
  - `local_ai_server/main.py` (Kroko, Sherpa, Kokoro backends)
  - `.github/workflows/ci.yml` (image size checks)
  - `.github/workflows/trivy.yml` (dual image scanning)
  - `admin_ui/frontend/src/components/config/PipelineForm.tsx`
  - `src/pipelines/base.py`, `src/pipelines/orchestrator.py`

## [4.4.1] - 2025-11-30

### Added - Admin UI v1.0 🎉
- **Web-Based Administration Interface**: Modern React + TypeScript UI replacing CLI setup workflow
  - **Setup Wizard**: Visual provider configuration with API key validation (replaces `agent quickstart`)
  - **Configuration Management**: Full CRUD for providers, pipelines, contexts, and audio profiles
  - **System Dashboard**: Real-time monitoring (CPU, memory, disk usage, container status)
  - **Live Logs**: WebSocket-based log streaming from ai-engine
  - **Raw YAML Editor**: Monaco-based editor with syntax validation
  - **Environment Manager**: Visual editor for `.env` variables
  - **Container Control**: Start/stop/restart containers from UI
- **JWT Authentication System**: Production-ready security
  - Token-based authentication with 24-hour expiry
  - Password hashing (pbkdf2_sha256)
  - Default credentials: admin/admin (must be changed on first login)
  - Change password functionality
  - Auto-created default admin user
  - Optional JWT secret configuration (development default provided)
- **Docker Integration**: Multi-stage build and deployment
  - Single container with frontend + backend
  - Port 3003 (configurable)
  - Volume mounts for config/users.json access
  - Health check endpoint
  - Restart policies
- **Comprehensive Documentation**:
  - `admin_ui/UI_Setup_Guide.md`: Complete setup and troubleshooting guide
  - Docker deployment (recommended)
  - Standalone deployment with daemon mode (nohup, screen, systemd)
  - Production deployment with reverse proxy (Nginx, Traefik)
  - Security best practices and JWT configuration
  - Upgrade path from CLI setup

### Added - Provider System Enhancements
- **Provider Registration System**: Explicit validation of supported provider types
  - `REGISTERED_PROVIDER_TYPES` defines engine-supported providers
  - Unregistered providers show warning but can be saved
  - Pipeline dropdowns only show registered providers
- **Local Full Agent**: 100% on-premises deployment option
  - New `local` provider with `type: full` for monolithic Local AI Server mode
  - Wizard option "Local (Full)" - no API keys required
  - Health check verification before setup completion
- **Provider Classification**: Clear distinction between Full Agent and Modular providers
  - `isFullAgentProvider()` logic based on type and capabilities
  - Full agents blocked from modular pipeline slots
  - Modular providers require explicit `capabilities` arrays

### Added - ElevenLabs Conversational AI Provider (AAVA-90)
- **Full Agent Provider**: ElevenLabs Conversational AI integration
  - WebSocket-based real-time voice conversations (STT + LLM + TTS)
  - Premium voice quality with natural conversation flow
  - Tool calling support (tools defined in ElevenLabs dashboard, executed locally)
  - Audio format: PCM16 16kHz, automatic resampling from telephony (μ-law 8kHz)
- **Configuration**: 
  - `ELEVENLABS_API_KEY` and `ELEVENLABS_AGENT_ID` environment variables
  - Provider config in `ai-agent.yaml` under `providers.elevenlabs_agent`
  - Admin UI support: Wizard option, provider form, card badges
- **Documentation**: `docs/contributing/references/Provider-ElevenLabs-Implementation.md` (578 lines)
- **Files**: `src/providers/elevenlabs_agent.py`, `src/providers/elevenlabs_config.py`, `src/tools/adapters/elevenlabs.py`

### Added - Background Music Support (AAVA-89)
- **In-Call Background Music**: Play music during AI conversations
  - Uses Asterisk Music On Hold (MOH) via snoop channel
  - Configurable per-context via `background_music` field
  - Admin UI toggle in Context configuration
- **Implementation**: Snoop channel with MOH starts when call begins, stops on hangup
- **Configuration**: Set MOH class name (default: "default") in context settings
- **Note**: Music is heard by AI (affects VAD); use low-volume ambient music for best results

### Changed
- **Port Configuration**: Admin UI runs on port 3003 (updated from 3000)
- **Version Numbers**: Admin UI frontend package.json updated to 1.0.0
- **docker-compose.yml**: Added admin-ui service with proper volume mounts
- **LocalProviderConfig**: Added `base_url` field for consistency with other full agents

### Technical Details
- **Frontend**: React 18, TypeScript, Vite, TailwindCSS, Monaco Editor
- **Backend**: FastAPI, Python 3.10, JWT auth, YAML/JSON config management
- **Build**: Multi-stage Dockerfile (Node.js build → Python runtime)
- **Authentication**: JWT tokens, OAuth2 password flow, session management
- **API**: RESTful endpoints with OpenAPI/Swagger documentation
- **Real-time**: WebSocket support for log streaming

### Security
- JWT-based authentication (optional custom secret for production)
- Password hashing with pbkdf2_sha256
- Route protection on all API endpoints
- CORS configuration (restrict in production)
- HTTPS support via reverse proxy
- Default credentials documented with change instructions

### Migration
- **New Installations**: Use setup wizard on first access
- **Existing Users**: Config auto-detected, wizard skipped
- **CLI Coexistence**: `agent` CLI tools continue to work
- **Backward Compatible**: No breaking changes to ai-engine

## [4.3.0] - 2025-11-19

### Added
- **Holistic Tool Support for Pipelines (AAVA-85)**: Complete tool execution system across all pipeline types
  - Enabled all 6 tools (hangup, transfer, email, transcript, voicemail) for `local_hybrid` pipeline
  - Session history persistence for tool context
  - Explicit ARI hangup implementation
- **Comprehensive Documentation Structure**: Complete reorganization of project documentation
  - New `docs/contributing/` structure for developer documentation
  - Provider setup guides: `Provider-Deepgram-Setup.md`, `Provider-OpenAI-Setup.md`, `Provider-Google-Setup.md`
  - Developer guides: quickstart, architecture overview, architecture deep dive, common pitfalls
  - Technical references for all provider implementations
- **Community Integration**: Discord server integration (https://discord.gg/ysg8fphxUe)
- **Milestone 18**: Hybrid Pipelines Tool Implementation documentation

### Fixed
- **OpenAI Realtime Tool Schema Regression**: Corrected tool schema format for chat completions
- **Tool Execution Flow**: Resolved AttributeError and execution blocking issues
- **Playback Race Conditions**: Fixed audio cutoff during tool execution
- **Hangup Method**: Corrected method name (`hangup_channel()` vs `delete_channel()`)
- **Pydantic Compatibility**: Fixed v1/v2 compatibility (`model_dump` → `dict`)
- **Milestone Numbering**: Corrected duplicate milestone-8, renumbered monitoring stack to milestone-14

### Changed
- **Documentation Structure**: Reorganized docs into User, Provider, Operations, Developer, and Project sections
- **Merged Documentation**: Combined Deepgram API reference into implementation guide (single comprehensive doc)
- **Consolidated Guides**: CLI tools → `cli/README.md`, Queue setup → FreePBX Integration Guide
- **Renamed Files**: Clearer naming for pipeline implementations and architecture docs
- **Link Format**: All documentation links now use relative paths (GitHub-clickable)

### Removed
- **Obsolete Documentation**: 8 outdated docs removed (2,763 lines)
  - `call-framework.md`, `AudioSocket-Provider-Alignment.md`, `CLI_TOOLS_GUIDE.md`
  - `LOCAL_AI_SERVER_LOGGING_OPTIMIZATION.md`, `ASTERISK_QUEUE_SETUP.md`
  - `ExternalMedia_Deployment_Guide.md`, `deepgram-agent-api.md`
- **Broken References**: Replaced `linear-issues-community-features.md` with Discord server

## [4.2.1] - 2025-11-18

### Added

#### Streamlined Onboarding Experience
- **🚀 Interactive Setup Wizard**: New `agent quickstart` command guides first-time users through complete setup
  - Step-by-step provider selection (OpenAI, Deepgram, Google, Local Hybrid)
  - Real-time API key validation before saving
  - Asterisk ARI connection testing
  - Automatic dialplan snippet generation
  - Clear next steps and FreePBX integration instructions
- **🔧 Enhanced install.sh**: Improved installer with CLI integration
  - ARI connection validation after credentials input
  - Shows Asterisk version on successful connection
  - Offers CLI tool installation with platform auto-detection
  - Launches `agent dialplan` helper if CLI installed
  - Graceful fallbacks for unsupported platforms or download failures
- **📝 Dialplan Generation Helper**: New `agent dialplan` command
  - Generates provider-specific dialplan snippets
  - Supports all providers: OpenAI Realtime, Deepgram, Google Live, Local Hybrid
  - Shows FreePBX Custom Destination setup steps
  - Includes context override examples (AI_PROVIDER, AI_CONTEXT variables)
  - Print-only approach (no auto-write to files)
- **✅ Configuration Validation**: New `agent config validate` command
  - Validates YAML syntax and structure
  - Checks required fields and provider configurations
  - Verifies sample rate alignment across providers
  - Validates transport compatibility
  - Checks barge-in configuration
  - `--fix` flag for interactive auto-fix
  - `--strict` mode for CI/CD (treats warnings as errors)
  - Exit codes: 0 (valid), 1 (warnings), 2 (errors)
- **🩺 Doctor Auto-Fix**: Enhanced `agent doctor` with `--fix` flag
  - Focuses on YAML config validation issues
  - Guides users to `agent config validate --fix` for detailed repairs
  - Re-runs health checks after fixes applied

#### API and Connection Validation
- **API Key Validation**: Real-time validation before saving credentials
  - OpenAI: Validates against `/v1/models` endpoint, checks for GPT models
  - Deepgram: Validates against `/v1/projects` endpoint
  - Google: Format validation (length check)
  - Clear error messages with troubleshooting guidance
  - Network timeout handling (10 second limit)
- **ARI Connection Testing**: Validates Asterisk connectivity during setup
  - Tests connection to `/ari/asterisk/info`
  - Extracts and displays Asterisk version
  - Shows troubleshooting steps on failure
  - Continues with warning if validation fails

#### Documentation
- **CLI Tools Guide**: Updated with all new v4.2 commands
  - Comprehensive `agent quickstart` reference with example session
  - `agent dialplan` usage and output examples
  - `agent config validate` with validation checks and flags
  - Version bumped to v4.2
- **README.md**: Updated Quick Start section
  - Two-path approach: Interactive Quickstart vs Manual Setup
  - Highlights new `agent quickstart` wizard
  - Shows new CLI commands (`dialplan`, `config validate --fix`)
  - Updated version references to v4.2
- **Developer Experience**: Enhanced setup documentation
  - Clear separation between first-time and advanced user paths
  - Better CLI tool discovery and installation guidance

### Fixed

#### OpenAI Realtime Provider
- **Hangup Tool Reliability**: Fixed issue where calls wouldn't hang up when OpenAI failed to generate farewell audio
  - Now emits `HangupReady` immediately when `response.done` arrives without audio
  - Eliminated reliance on timeout-only fallback mechanism
  - Ensures consistent call termination regardless of OpenAI audio generation
- **Self-Interruption Prevention**: Resolved agent overhearing itself and interrupting mid-response
  - Increased `post_tts_end_protection_ms` from 100ms to 800ms (8x longer guard window)
  - Tuned `turn_detection.threshold` from 0.5 to 0.6 (less sensitive to agent's own voice)
  - Increased `turn_detection.silence_duration_ms` from 600ms to 1000ms (more patient turn-taking)
  - Result: Clean, natural conversation flow without choppy interruptions
- **Greeting Timing**: Attempted optimization of `session.updated` ACK timeout (reverted due to audio issues)

#### Local Hybrid Pipeline
- **Critical Sample Rate Fix**: Resolved Vosk STT recognition failure
  - Changed `external_media.format` from `slin` to `slin16` and `sample_rate` from 8000 to 16000
  - Enabled RTP server resampling to match Vosk's native 16kHz requirement
  - Audio now correctly resampled: 8kHz μ-law → decode → 16kHz PCM16 → Vosk
  - Result: Clear two-way conversation with accurate transcription
- **Audio Flow Debugging**: Added comprehensive debug logging for troubleshooting
  - Traces audio bytes, RMS levels, sample counts through full pipeline
  - Helps diagnose future audio routing or quality issues

#### Logging Optimization
- **Production Log Volume**: Reduced local-ai-server log noise
  - Implemented `LOCAL_DEBUG` environment flag to gate verbose audio flow logs
  - Moved detailed audio processing logs (`FEEDING VOSK`, RMS calculation) behind debug flag
  - Preserved essential logs (STT finals, LLM results, TTS output, connection events)
  - Result: ~90% log volume reduction in production with `LOCAL_DEBUG=0`
- **Configuration Clarity**: Improved `.env.example` documentation
  - Clear section headers distinguishing ai-engine vs local-ai-server settings
  - Explicit warnings about log volume impact of debug flags
  - Better guidance on production vs development logging levels

### Added

#### Documentation
- **Local Hybrid Golden Baseline**: Complete production-validated configuration reference
  - Performance metrics, architecture, sample rate fix details
  - Call quality assessment and tuning recommendations
  - See `docs/case-studies/Local-Hybrid-Golden-Baseline.md`
- **Logging Optimization Guide**: Comprehensive logging strategy documentation
  - Debug flag usage, log volume comparison, configuration examples
  - See `docs/LOCAL_AI_SERVER_LOGGING_OPTIMIZATION.md`

#### Unified Transfer Tool (AAVA-63, AAVA-74)
- **Unified Transfer System**: Single `transfer` tool replaces separate `transfer_call` and `transfer_to_queue` tools
  - **Extension Transfers**: Direct dial to specific agents (ARI `redirect`, channel stays in Stasis)
  - **Queue Transfers**: Transfer to ACD queues for next available agent (ARI `continue` to `ext-queues`)
  - **Ring Group Transfers**: Transfer to ring groups that ring multiple agents simultaneously (ARI `continue` to `ext-group`)
- **Smart Routing**: Automatic routing based on destination type configuration
- **Proper Cleanup Handling**: `transfer_active` flag prevents premature caller hangup for queue/ring group transfers
- **Production Verified**: All three transfer types validated on live production server
- **Configuration**: Unified `tools.transfer.destinations` structure with type-based routing

#### Voicemail Tool (AAVA-51)
- **Voicemail Routing**: New `leave_voicemail` tool sends callers to voicemail
  - Routes to FreePBX voicemail via `ext-local,vmu{extension},1` dialplan pattern
  - Uses ARI `continue()` pattern consistent with queue/ring group transfers
  - `transfer_active` flag prevents premature caller hangup
  - Configurable voicemail box extension number
- **Interactive Prompt Strategy**: Tool asks "Are you ready to leave a message now?" to work around FreePBX VoiceMail app behavior
  - VoiceMail app requires bidirectional RTP and voice activity before playing greeting
  - Without caller interaction, 5-8 second delay occurs before greeting plays
  - Caller response establishes RTP path and triggers greeting immediately
- **Comprehensive Documentation**: Detailed behavioral analysis and timeline evidence in module docstring
- **Production Verified**: Tested and deployed on live production server

### Changed
- **Breaking**: Removed `transfer_call` and `transfer_to_queue` tools in favor of unified `transfer` tool
- **Configuration Migration**: Update from separate tool configs to unified `transfer.destinations` structure

## [4.2.0] - 2025-11-14

### 🚀 Major Feature: Google Live Provider (Real-Time Agent)

Version 4.2 introduces the **Google Live provider** - a real-time bidirectional streaming agent powered by Gemini 2.5 Flash with native audio capabilities. This provider delivers ultra-low latency (<1 second) and true duplex communication, making it the fastest option in the Asterisk AI Voice Agent.

### Added

#### Google Live Provider (AAVA-75)
- **Real-Time Bidirectional Streaming**: Full-duplex communication with Gemini 2.5 Flash
  - Native audio processing (no separate STT/TTS pipeline)
  - Ultra-low latency: <1 second response time
  - True duplex: Natural interruptions and barge-in
  - WebSocket-based streaming communication
- **Provider Implementation**: `src/providers/google_live.py`
  - WebSocket connection to Gemini Live API
  - Bidirectional audio streaming with automatic resampling
  - Native tool execution via Google function declarations
  - Session management with context retention
- **Tool Adapter**: `src/tools/adapters/google.py`
  - Converts tools to Google function declaration format
  - Handles async tool execution in streaming mode
  - Sends tool responses back to Gemini
- **Audio Processing**: Automatic resampling for telephony compatibility
  - Input: 8kHz μ-law → 16kHz PCM16 → Gemini
  - Output: 24kHz PCM16 from Gemini → 8kHz μ-law → Asterisk
- **Configurable Parameters**: Full YAML configuration support
  - LLM generation parameters (temperature, max_output_tokens, top_p, top_k)
  - Response modalities (audio, text, audio_text)
  - Transcription toggles (enable_input_transcription, enable_output_transcription)
  - Voice selection (Aoede, Kore, Leda, Puck, Charon, etc.)
- **Golden Baseline**: Validated production-ready configuration
  - See `docs/GOOGLE_LIVE_GOLDEN_BASELINE.md` for complete reference
  - Call quality: Excellent, clean two-way conversation
  - Response latency: <1 second (fastest available)
  - All features validated: duplex, barge-in, tools, transcriptions

#### Transcription System (AAVA-75)
- **Dual Transcription Support**: User and AI speech transcription
  - `inputTranscription`: Captures user speech
  - `outputTranscription`: Captures AI speech
  - Turn-complete based: Saves only final utterances
  - Incremental fragment concatenation for complete transcripts
- **Email Summary Integration**: Complete conversation history in emails
  - Auto-triggered email summaries at call end
  - Manual transcript requests via `request_transcript` tool
  - Transcripts include both user and AI speech
- **Conversation History**: Full conversation tracking
  - Stored in session for context retention
  - Available for email summaries and transcript requests
  - Proper turn management with `turnComplete` flag

### Fixed

#### Transcript Email Timing (CRITICAL)
- **Issue**: `request_transcript` tool sent email immediately (mid-call), missing final conversation
- **Fix**: Defer transcript sending until call end
  - Store email address in session during call
  - Send complete transcript at call cleanup with full conversation history
  - Prevents incomplete transcripts missing final exchanges
- **Impact**: Transcripts now include complete conversation including goodbye

#### Call Ending Protocol
- **Issue**: AI didn't hang up calls after completing tasks, leaving silence
- **Fix**: Explicit call ending protocol in system prompts
  - Step-by-step protocol for detecting conversation end
  - "Is there anything else?" prompt after completing tasks
  - Immediate `hangup_call` tool execution on confirmation
  - Never leave calls hanging in silence
- **Impact**: Professional call termination, no manual hangup needed

#### Greeting Implementation
- **Issue**: Cannot pre-fill model responses in Gemini Live API
- **Fix**: Send user turn requesting AI to speak greeting
  - Changed from pre-filled model response to user request
  - AI generates and speaks personalized greeting naturally
  - Properly uses caller name in greeting
- **Impact**: Greetings now work correctly with caller personalization

#### Incremental Transcription Handling
- **Issue**: API sends word-by-word fragments, not cumulative text
- **Fix**: Concatenate fragments instead of replacing buffer
  - Buffer accumulates fragments until `turnComplete`
  - Prevents fragmented/incomplete transcriptions
  - Matches actual API behavior (differs from documentation)
- **Impact**: Complete, clean transcriptions of all speech

### Changed
- **Documentation**: Renamed `docs/GOOGLE_CLOUD_SETUP.md` → `docs/GOOGLE_PROVIDER_SETUP.md`
  - Updated to cover both Google Live and Cloud Pipeline modes
  - Added comprehensive setup instructions for both
  - Separate dialplan examples for each mode
- **Configuration Examples**: Updated `config/ai-agent.yaml`
  - Added `demo_google_live` context with full configuration
  - Includes all new configurable parameters with inline docs
  - Clear call ending protocol in system prompts

### Performance
- **Latency**: <1 second response time (fastest provider)
- **Audio Quality**: Excellent, natural conversation flow
- **Duplex Communication**: True full-duplex with seamless interruptions
- **Reliability**: Production-tested with clean call termination

### Lessons Learned
- Trust API turn completion signals over custom heuristics
- API behavior may differ from documentation - always validate with testing
- Defer email sending until call end for complete transcripts
- Be explicit about call ending protocols in system prompts
- Provide maximum user flexibility via YAML configuration

## [4.0.0] - 2025-10-29

### 🎉 Major Release: Modular Pipeline Architecture

Version 4.0 introduces a **production-ready modular pipeline architecture** that enables flexible combinations of Speech-to-Text (STT), Large Language Models (LLM), and Text-to-Speech (TTS) providers. This release represents a complete architectural evolution while maintaining backward compatibility with existing deployments.

### Added

#### Core Architecture
- **Modular Pipeline System**: Mix and match STT, LLM, and TTS providers
  - Local STT (Vosk) + Cloud LLM (OpenAI) + Local TTS (Piper)
  - Cloud STT (Deepgram) + Cloud LLM (OpenAI) + Cloud TTS (Deepgram)
  - Fully local pipeline (Vosk + Phi-3/Llama + Piper)
- **Unified Configuration Format**: Single YAML file for all pipeline and provider settings
- **Golden Baseline Configurations**: Three validated, production-ready configurations:
  - **OpenAI Realtime**: Cloud monolithic agent (fastest, <2s response)
  - **Deepgram Voice Agent**: Enterprise cloud agent with Think stage
  - **Local Hybrid**: Privacy-focused with local STT/TTS + cloud LLM

#### Audio Transport
- **Dual Transport Support**: AudioSocket (TCP) and ExternalMedia RTP (UDP)
- **Automatic Transport Selection**: Optimal transport chosen per configuration
- **Enhanced Audio Processing**: Improved resampling, echo cancellation, and codec handling
- **Pipeline Audio Routing**: Fixed audio path for pipeline configurations
- **Transport Compatibility Matrix**: Documented all configuration + transport combinations

#### Monitoring & Observability
- **Production Monitoring Stack**: Prometheus + Grafana with 5 pre-built dashboards
  - System Overview: Active calls, provider distribution
  - Call Quality: Turn latency (p50/p95/p99), processing time
  - Audio Quality: RMS levels, underflows, jitter buffer depth
  - Provider Performance: Provider-specific metrics and health
  - Barge-In Analysis: Interrupt behavior and timing
- **50+ Metrics**: Comprehensive call quality, audio quality, and system health metrics
- **Alert Rules**: Critical and warning alerts for production monitoring
- **Health Endpoint**: `/metrics` endpoint on port 15000 for Prometheus scraping

#### Installation & Setup
- **Interactive Installer**: `install.sh` with guided pipeline selection
  - Choose from 3 golden baseline configurations
  - Automatic dependency setup per configuration
  - Model downloads for local pipelines
  - Environment validation and configuration
- **Two-File Configuration Model**: 
  - `.env` for secrets and credentials (gitignored)
  - `config/ai-agent.yaml` for pipeline definitions (committed)
- **Streamlined User Journey**: From clone to first call in <15 minutes

#### Documentation
- **FreePBX Integration Guide**: Complete v4.0 guide with channel variables
  - `AI_CONTEXT`: Department/call-type specific routing
  - `AI_GREETING`: Per-call greeting customization
  - `AI_PERSONA`: Dynamic persona switching
  - Remote deployment configurations (NFS, Docker, Kubernetes)
  - Network and shared storage setup for distributed deployments
- **Configuration Reference**: Comprehensive YAML parameter documentation
- **Transport Compatibility Guide**: Validated configuration + transport combinations
- **Golden Baseline Case Studies**: Detailed performance analysis and tuning guides
- **Inline YAML Documentation**: Comprehensive comments with ranges and impacts

#### Developer Experience
- **CLI Tools**: Go-based `agent` command with 5 subcommands
  - `agent init`: Interactive setup wizard
  - `agent doctor`: Health diagnostics and validation
  - `agent demo`: Demo call functionality
  - `agent troubleshoot`: Interactive troubleshooting assistant
  - `agent version`: Version and build information
- **Enhanced Logging**: Structured logging with context and call tracking
- **RCA Tools**: Root cause analysis scripts for audio quality debugging
- **Test Infrastructure**: Baseline validation and regression testing
- **IDE Integration**: Full development context preserved in develop branch

### Changed

#### Configuration
- **YAML Structure**: Streamlined provider configuration format
- **Settings Consolidation**: Removed unused/duplicate settings (`llm.model`, `external_media.jitter_buffer_ms`)
- **downstream_mode Enforcement**: Now properly gates streaming vs file playback
- **Security Model**: Credentials **ONLY** in `.env`, never in YAML files

#### Audio Processing
- **VAD Configuration**: Optimized Voice Activity Detection for each provider
  - OpenAI Realtime: `webrtc_aggressiveness: 1` (balanced mode)
  - Server-side VAD support for providers that offer it
- **Barge-In System**: Enhanced interrupt detection with configurable thresholds
- **Audio Routing**: Fixed pipeline audio routing for AudioSocket and RTP transports

#### Performance
- **Response Times**: Validated response times for all golden baselines:
  - OpenAI Realtime: 0.5-1.5s typical
  - Deepgram Hybrid: <3s typical
  - Local Hybrid: 3-7s depending on hardware
- **Echo Cancellation**: Improved echo filtering with SSRC-based detection
- **Jitter Buffer**: Optimized buffer management for streaming playback

### Fixed

- **AudioSocket Pipeline Audio**: Fixed audio routing to STT adapters in pipeline mode
- **RTP Echo Loop**: Added SSRC-based filtering to prevent echo feedback
- **Provider Bytes Tracking**: Corrected audio chunk accounting for accurate pacing
- **Normalizer Consistency**: Fixed audio normalization for consistent output
- **Configuration Loading**: Ensured all config values properly honored at runtime
- **Sample Rate Handling**: Fixed provider-specific sample rate overrides

### Deprecated

- **Legacy YAML Templates**: Replaced with 3 golden baseline configurations
  - `ai-agent.openai-agent.yaml` → `ai-agent.golden-openai.yaml`
  - `ai-agent.deepgram-agent.yaml` → `ai-agent.golden-deepgram.yaml`
  - `ai-agent.hybrid.yaml` → `ai-agent.golden-local-hybrid.yaml`
- **Development Artifacts**: Moved to `archived/` folder (not tracked in git)

### Technical Details

#### System Requirements
- **Minimum**: 4 CPU cores, 8GB RAM (cloud configurations)
- **Recommended**: 8+ CPU cores, 16GB RAM (local pipelines)
- **GPU**: Optional for local-ai-server (improves LLM performance)

#### Compatibility
- **Asterisk**: 18+ required (for AudioSocket support)
- **FreePBX**: 15+ recommended
- **Python**: 3.10+
- **Docker**: 20.10+
- **Docker Compose**: 2.0+

#### Breaking Changes
**None** - This release maintains backward compatibility with existing deployments. Users can continue using existing configurations while adopting new features incrementally.

### Migration Guide

**No migration needed** - This is the first production release. There are no users on v3.0 requiring migration.

For new deployments:
1. Clone repository
2. Run `./install.sh` and select a golden baseline
3. Configure `.env` with your credentials
4. Deploy with `docker compose up -d`
5. Follow the FreePBX Integration Guide to configure Asterisk

### Contributors

- Haider Jarral (@hkjarral) - Architecture, implementation, documentation

### Links

- **Repository**: https://github.com/hkjarral/Asterisk-AI-Voice-Agent
- **Documentation**: [docs/README.md](docs/README.md)
- **FreePBX Guide**: [docs/FreePBX-Integration-Guide.md](docs/FreePBX-Integration-Guide.md)
- **Metrics/Observability**: [docs/MONITORING_GUIDE.md](docs/MONITORING_GUIDE.md)

---

## [4.1.0] - 2025-11-10

### 🎉 Tool Calling & Agent CLI Release

Version 4.1 introduces **unified tool calling architecture** enabling AI agents to perform actions like call transfers and email management, plus production-ready **Agent CLI tools** for operations.

### Added

#### Tool Calling System
- **Unified Tool Architecture**: Write tools once, use with any provider
  - Base classes: `Tool`, `ToolDefinition`, `ToolRegistry` (`src/tools/base.py`, 231 lines)
  - Execution context with session and ARI access (`src/tools/context.py`, 108 lines)
  - Singleton registry for tool management (`src/tools/registry.py`, 198 lines)
  - Provider adapters for Deepgram (202 lines) and OpenAI Realtime (215 lines)

#### Telephony Tools
- **Transfer Call Tool**: Warm and blind transfers with direct SIP origination
  - `src/tools/telephony/transfer.py` (504 lines)
  - Department name resolution (e.g., "support" → extension 6000)
  - Warm transfer: AI stays on line until agent answers
  - Blind transfer: Immediate redirect
  - Production validated: <150ms execution time
  - Call IDs: `1762731796.4233` (Deepgram), `1762734947.4251` (OpenAI)
- **Cancel Transfer Tool**: Cancel in-progress transfer before agent answers
  - `src/tools/telephony/cancel_transfer.py`
  - Allows caller to change mind during ring
- **Hangup Call Tool**: Graceful call termination with farewell message
  - `src/tools/telephony/hangup.py`
  - Customizable farewell message
  - Works with all providers

#### Email Tools
- **Request Transcript Tool**: Caller-initiated transcript delivery
  - `src/tools/business/request_transcript.py` (475 lines)
  - Email parsing from speech ("john dot smith at gmail dot com")
  - Domain validation via DNS MX record lookup
  - Confirmation flow (AI reads back email for verification)
  - Deduplication (prevents sending same email multiple times)
  - Admin receives BCC on all transcript requests
  - Resend API integration
- **Send Email Summary Tool**: Auto-send call summaries to admin
  - `src/tools/business/email_summary.py` (347 lines)
  - Triggered automatically after every call
  - Full conversation transcript with timestamps
  - Call metadata (duration, caller ID, date/time)
  - Professional HTML formatting
  - Admin email configuration in YAML

#### Agent CLI Tools
- **Binary Distribution System**:
  - Makefile build system for 5 platforms (Linux, macOS, Windows)
  - GitHub Actions CI/CD for automated releases (`.github/workflows/release-cli.yml`)
  - One-line installer: `curl -sSL ... | bash` (`scripts/install-cli.sh`, 223 lines)
  - SHA256 checksums for security verification
  - Automated binary uploads to GitHub releases
- **CLI Commands**:
  - `agent doctor`: System health checks and validation
  - `agent troubleshoot`: Call analysis and debugging
  - `agent demo`: Feature demonstrations
  - `agent init`: Interactive setup wizard
  - `agent version`: Build and version information
- **Platform Support**:
  - Linux AMD64/ARM64 (servers, Raspberry Pi, AWS Graviton)
  - macOS AMD64/ARM64 (Intel Macs and Apple Silicon M1/M2/M3)
  - Windows AMD64
  - Pre-built binaries with automatic platform detection

#### Conversation Tracking
- **Real-time Tracking**: Both Deepgram and OpenAI Realtime track conversation turns
  - `conversation_history` field in `CallSession` model
  - Tracks role (user/assistant), content, and timestamps
  - Enables email tools to include full transcripts
  - Pattern identical across providers (46-51 lines each)

### Improved

#### Warm Transfer Implementation
- **Direct SIP Endpoint Origination**: Eliminates Local channel complexity
  - Previous: Used `Local/{ext}@{context}/n` → caused unidirectional audio
  - Current: Direct SIP origination (e.g., `SIP/6000`)
  - Result: Perfect bidirectional audio confirmed
  - No Local channels created (verified in production)
- **4-Step Cleanup Sequence**:
  1. Remove AI channel from bridge (<50ms)
  2. Stop provider session gracefully (<30ms)
  3. Add agent SIP channel to bridge (<20ms)
  4. Update session metadata (<10ms)
  - Total: <150ms for complete transfer execution
- **Production Validation**:
  - Call duration: 38+ seconds after transfer (stable)
  - Bridge type: simple_bridge (optimal, 2 channels only)
  - Audio path: Direct (1 hop, minimal latency)
  - Files: `src/tools/telephony/transfer.py`, `src/engine.py` (transfer handler)

#### OpenAI Realtime Stability
- **VAD Re-enable Timing Fix**: Correct event for greeting protection
  - Issue: Used `response.audio.done` (fires per segment) → VAD enabled too early
  - Fix: Changed to `response.done` (fires when complete response generated)
  - Impact: Greeting now plays completely before accepting interruptions
  - Lines: `src/providers/openai_realtime.py` (1206-1219)
- **API Modality Constraints**: Documented OpenAI requirements
  - Supported: `["text"]` or `["audio", "text"]`
  - Not supported: `["audio"]` alone (API rejects)
  - Known limitation: May occasionally generate text-only responses
  - Mitigation: System handles gracefully with keepalive messages
- **Race Condition Handling**: Handles variable event arrival order
  - Sometimes: `response.done` arrives before audio deltas
  - Other times: Audio deltas arrive first
  - Solution: Check `had_audio_burst` flag, re-enable VAD regardless

### Fixed

- **AAVA-57**: Direct SIP endpoint origination for warm transfers
  - Root cause: Local channels caused audio direction mismatch
  - Solution: Direct `SIP/extension` origination
  - Evidence: Call logs show no Local channels, perfect audio
- **AAVA-58**: Local channel audio direction issue (RCA documented)
  - Symptom: Caller heard agent, but agent couldn't hear caller
  - Root cause: Audio path `caller → Local;2 → Local;1 → nowhere`
  - Solution: Eliminated Local channels entirely
- **AAVA-59**: AI provider cleanup during transfer
  - Issue: AI stayed in bridge after agent answered
  - Solution: Remove external media channel before adding agent
  - Result: Clean 2-channel bridge (caller + agent only)
- **AAVA-62**: OpenAI Realtime audio generation analysis and constraints
  - Issue #1: Greeting interrupted (VAD enabled too early)
  - Issue #2: 45-second silence (OpenAI generated text-only)
  - Solution: Correct VAD timing + documented API limitation
  - Commits: `85c4235`, `80efdcd`, `6dbd51e`
- **AAVA-52**: Email tools race conditions and missing await
  - Bug #1: `context.get_session()` called without `await`
  - Bug #2: Auto-summary triggered async, session removed first
  - Bug #3: Undefined `caller_id` variable
  - Bug #4: No email confirmation flow
  - Bug #5: Duplicate emails when caller corrected address
  - Commits: `1deed05`, `700993f`, `5579ddd`, `a2d9409`, `835ac05`

### Documentation

- **New Guides**:
  - `docs/TOOL_CALLING_GUIDE.md` - Comprehensive tool calling documentation
    - Overview and supported providers
    - All 5 tools with example conversations
    - Configuration details with option explanations
    - Dialplan setup requirements
    - Testing procedures
    - Production examples with evidence
    - Troubleshooting section
    - Architecture diagrams
  - Tool sections added to `docs/FreePBX-Integration-Guide.md`
  - Enhanced `docs/CLI_TOOLS_GUIDE.md` with binary installation
- **Updated**:
  - README with v4.1 features and tool examples
  - Architecture.md with tool calling section
  - Configuration comments in `config/ai-agent.yaml`
  - SECURITY.md with v4.1 support

### Known Limitations

- **OpenAI Realtime**: May occasionally generate text-only responses
  - Root cause: API limitation with `["audio", "text"]` modalities
  - Frequency: Varies (test call had 2/4 responses without audio)
  - Impact: Caller experiences brief silence
  - Mitigation: System handles gracefully with keepalive messages
  - Not fixable: Cannot force OpenAI to always generate audio
- **Tool Calling**: Currently Deepgram and OpenAI Realtime only
  - Custom Pipeline support planned for v4.3 (AAVA-56)
  - Other providers: Anthropic Claude, Google Gemini (v4.3+)

### Architecture Validation

**Provider-Agnostic Design Confirmed**:
- Same tool code (504 lines for transfer) works with both providers
- Only adapters differ (202 lines Deepgram, 215 lines OpenAI)
- Zero code duplication in tool logic
- Adding new providers requires <250 lines of adapter code

**Line Counts**:
- Tool calling framework: 537 lines (base + context + registry)
- Transfer call tool: 504 lines (shared by all providers)
- Email summary tool: 347 lines (shared)
- Request transcript tool: 475 lines (shared)
- Deepgram adapter: 202 lines
- OpenAI adapter: 215 lines
- **Total duplication**: 0 lines ✅

### Performance Metrics

**Transfer Tool**:
- Transfer execution: <150ms
- AI cleanup time: <100ms
- Bridge technology: simple_bridge (optimal)
- Audio path: Direct (1 hop)
- Call stability: 38+ seconds validated

**Email Tools**:
- Email validation: <100ms (DNS MX lookup)
- Email delivery: ~200ms (Resend API)
- Conversation tracking: Real-time (no performance impact)

### Contributors

- Haider Jarral (@hkjarral) - Tool architecture, transfers, email tools, CLI tools, documentation

### Links

- **Repository**: https://github.com/hkjarral/Asterisk-AI-Voice-Agent
- **Tool Calling Guide**: [docs/TOOL_CALLING_GUIDE.md](docs/TOOL_CALLING_GUIDE.md)
- **FreePBX Guide**: [docs/FreePBX-Integration-Guide.md](docs/FreePBX-Integration-Guide.md)
- **CLI Tools Guide**: [cli/README.md](cli/README.md)

---

## Version History

- **v5.0.0** (2026-01-07) - Outbound Campaign Dialer (Alpha), Groq Speech, Ollama improvements, attended transfer
- **v4.6.0** (2025-12-29) - Admin UI config/health improvements, preflight enhancements
- **v4.5.2** (2025-12-16) - Local AI Server UX, MCP tools, Aviation ATIS
- **v4.5.1** (2025-12-13) - Admin UI improvements, wizard fixes, preflight enhancements
- **v4.5.0** (2025-12-11) - Admin UI stability, graceful shutdown, timer logging
- **v4.0.0** (2025-10-29) - Modular pipeline architecture, production monitoring, golden baselines
- **v3.0.0** (2025-09-16) - Modular pipeline architecture, file based playback

[Unreleased]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/compare/v5.0.0...HEAD
[5.0.0]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v5.0.0
[4.6.0]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v4.6.0
[4.5.2]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v4.5.2
[4.5.1]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v4.5.1
[4.5.0]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v4.5.0
[4.0.0]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v4.0.0
