# Resilience and Error Handling

This document outlines the resilience and error handling strategies for the Asterisk AI Voice Agent.

## 1. AI Provider Resilience

The system supports multiple AI providers (OpenAI Realtime, Deepgram, ElevenLabs, Google Live, Local Hybrid). Each provider implements its own connection management.

### 1.1 Connection Management

- **Per-Call Connections**: Most providers create a new WebSocket connection per call, avoiding long-lived connection issues.
- **Keep-Alive Messages**: Providers use ping/pong frames to detect dead connections.
- **Timeout Handling**: Connection and operation timeouts prevent indefinite hangs.

### 1.2 Graceful Degradation

If the AI provider is unavailable:

- **Provider Unreachable**: The call receives an error response and the channel is cleaned up.
- **Mid-call Failure**: Active calls are terminated gracefully with cleanup of ARI resources.
- **Fallback**: Consider configuring a fallback context in your dialplan for provider failures.

## 2. Asterisk ARI Connection

The connection to the Asterisk server's ARI is critical for call control.

### 2.1 Reconnect Supervisor

The `ARIClient` implements automatic reconnection with exponential backoff:

- **Auto-Reconnect**: On WebSocket disconnect, the client automatically attempts to reconnect.
- **Exponential Backoff**: Delays increase from 2s up to 60s maximum between attempts.
- **Unlimited Retries**: Reconnection continues indefinitely until successful or shutdown.
- **State Tracking**: The `is_connected` property reflects true WebSocket state.

### 2.2 Health Integration

- **`/ready` Endpoint**: Returns 503 during reconnection attempts (not ready for new calls).
- **`/live` Endpoint**: Returns 200 if the process is running (for container orchestration).
- **Logging**: Reconnect attempts are logged with attempt count and backoff duration.

## 3. Health Checks

The `ai-engine` exposes health endpoints on port 15000 (binds to `0.0.0.0` by default in `docker-compose.yml` so `admin_ui` can reach it). For production hardening, restrict access via firewall/VPN/reverse proxy, or bind it to localhost using `HEALTH_BIND_HOST=127.0.0.1`.

### 3.1 Endpoints

| Endpoint | Purpose | Success |
|----------|---------|---------|
| `/live` | Liveness probe | 200 if process running |
| `/ready` | Readiness probe | 200 if ARI + transport + provider ready |
| `/health` | Detailed status | JSON with component states |
| `/metrics` | Prometheus metrics | OpenMetrics format |

### 3.2 Health Response

```json
{
  "status": "healthy",
  "ari_connected": true,
  "rtp_server_running": true,
  "audio_transport": "externalmedia",
  "active_calls": 0,
  "providers": {"deepgram": {"ready": true}, ...}
}
```

## 4. Operational Runbook

### Scenario: Service is Unhealthy or in a Restart Loop

1. **Symptom**: `docker compose ps` shows the `ai_engine` restarting, or the `/health` endpoint returns a 503 error.
2. **Check Logs**: `docker compose logs -f ai_engine`.
3. **Potential Causes & Fixes**:
    - **Cannot connect to ARI**:
        - Verify Asterisk is running.
        - Check the ARI user, password, and host in your `.env` file.
        - Ensure network connectivity between the container and Asterisk.
    - **Cannot connect to AI Provider**:
        - Verify the API keys in your `.env` file are correct.
        - Check for network connectivity to the provider's API endpoint (e.g., `agent.deepgram.com`).
        - Check the provider's status page for outages.
    - **AudioSocket listener issues**:
        - Verify the listener is bound to the correct port (default 8090).
        - Check Asterisk dialplan and module status: `module show like audiosocket`.
        - Inspect per‑call session handling and cleanup.

## 5. AudioSocket Session Resilience

- **Handshake & Keepalive**: Implement heartbeats to detect dead TCP sessions promptly.
- **Timeouts**: Use operation timeouts to prevent hangs during provider or I/O operations.
- **Reconnection**: Exponential backoff on provider reconnects; fail fast on repeated errors.
- **Graceful Shutdown**: Ensure per‑call resources are cleaned up when the channel ends.

Note: In the current release, downstream audio is **streaming-first** where enabled, with automatic **fallback to file playback** for robustness. Many pipeline deployments still prefer `downstream_mode: file` as the most validated/robust option.
