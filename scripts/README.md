# Scripts Overview

This document summarizes the utilities under `scripts/` and when to use them.

## Health, Monitoring, and Validation

- `scripts/validate_externalmedia_config.py`
  - Validates config required for ExternalMedia + RTP. Run locally before making a test call.
  - Usage: `python3 scripts/validate_externalmedia_config.py`

- `scripts/monitor_externalmedia.py`
  - Monitors ExternalMedia + RTP health continuously.
  - Usage: `python3 scripts/monitor_externalmedia.py` or `--once`

## Test Workflows

- `scripts/test_externalmedia_call.py`
  - End-to-end call flow verification using the `/health` endpoint.
  - Usage: `python3 scripts/test_externalmedia_call.py --url http://127.0.0.1:15000/health`

- `scripts/test_externalmedia_deployment.py`
  - Deployment sanity test to check ARI + RTP wiring.
  - Usage: `python3 scripts/test_externalmedia_deployment.py`

## Log Capture & Analysis

- `scripts/capture_test_logs.py`
  - Captures structured logs for a timed window during a test call.
  - Usage: `python3 scripts/capture_test_logs.py --duration 40`

- `scripts/analyze_logs.py`
  - Analyzes the most recent captured JSON logs and emits a summary.
  - Usage: `python3 scripts/analyze_logs.py logs/<timestamp>.json`

- `scripts/capture_call_window.sh`
  - Shell helper to capture a window for call logs.

- `scripts/summarize_call_capture.sh`
  - Summarizes captured logs quickly on the CLI.

- `scripts/compare_call_audio.py`
  - Compares inbound/outbound WAV recordings (RMS, DC bias, spectra, pacing).
  - Usage: `python3 scripts/compare_call_audio.py --in logs/.../in-*.wav --out logs/.../out-*.wav`

- `scripts/transcribe_call.py`
  - Offline transcription using Vosk (auto-downloads small English model).
  - Usage: `python3 scripts/transcribe_call.py logs/.../recordings/out-*.wav`

- `scripts/rca_collect.sh`
  - Remote log/recording capture. Stores wav stats and now transcripts at `logs/remote/<ts>/transcripts/`.
  - When `/tmp/ai-engine-captures/<call_id>` exists in the container, the capture bundle is copied into `logs/remote/<ts>/captures/` for offline waveform review.

## Provider & Model Management

- `scripts/switch_provider.py`
  - Switches `default_provider` in `config/ai-agent.yaml`.
  - Usage: `make provider=<name> provider-switch` or run the script directly.

- `scripts/model_setup.py`, `scripts/model_manager.py`
  - Detect/Download/Manage local model artifacts for the local provider.
  - Usage: `make model-setup`

- `scripts/download_models.sh`, `scripts/download_tts_models.py`
  - Helpers for bulk model download.

## Provider Setup

- `scripts/setup-vertex.sh`
  - One-command Google Vertex AI setup. Runs on the AAVA host (where docker compose runs). Enables the Vertex API, creates a service account with `roles/aiplatform.user`, downloads the JSON key into `secrets/`, and patches `.env`. Auto-detects headless hosts and uses gcloud's code-paste auth flow over SSH. Lists existing GCP projects to pick from, or walks you through creating a new project + linking billing if you don't have one yet.
  - Usage: `./scripts/setup-vertex.sh [project-id]`
  - Full guide: [docs/Provider-Vertex-Setup.md](../docs/Provider-Vertex-Setup.md)

## Catalog Maintenance

These scripts maintain `admin_ui/backend/api/models_catalog.py` — the curated list of STT/TTS/LLM models surfaced in the Admin UI Models page. Both are stdlib-only.

- `scripts/check_catalog_urls.py`
  - HEADs every download URL in the catalog (including Kokoro's nested `voice_files`)
    in parallel and reports any that aren't reachable. Validates URLs are HTTPS
    before opening them. Exits non-zero on any failure so it can gate CI.
  - Usage: `python3 scripts/check_catalog_urls.py [--include-cloud] [--max-workers N]`
  - Also wired up as the `Catalog URL Check` GitHub Actions workflow:
    runs on every PR that touches the catalog (blocks merge), weekly on `main`
    (opens or comments on a single `catalog-broken` issue rather than spamming
    new ones), and on manual dispatch.

- `scripts/regenerate_piper_catalog.py`
  - Walks `rhasspy/piper-voices` v1.0.0 on HuggingFace, fetches per-voice metadata
    (size, num_speakers, sample rate from each `.onnx.json`), and emits Python
    dict literals matching the existing `PIPER_TTS_MODELS` format. Filters out
    voices already in the catalog (by id AND by underlying file path) and
    backs off on HF 429 with `Retry-After` honoring. The output is a draft
    intended for human review (gender heuristic only covers common names) before
    paste into `models_catalog.py`.
  - Usage: `python3 scripts/regenerate_piper_catalog.py --out scripts/piper_draft.py`
  - Run when HuggingFace publishes a new piper-voices revision (e.g. v1.1.0):
    update `HF_REV` at the top of the script, regenerate, review, paste.

## Miscellaneous

- `scripts/llm_latency_test.py`
  - Rough latency probe for LLM responses (dev utility).

## Tips

- Most scripts assume the engine is running and `/health` is available at `http://127.0.0.1:15000/health`.
- For remote servers, use the Makefile targets (now localhost-aware) under `server-*` and `deploy-*`.
