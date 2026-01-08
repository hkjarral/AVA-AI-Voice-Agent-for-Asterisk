# Milestone 23: NAT/Hybrid Network Support (advertise_host)

## Summary

Add separate `bind_host` vs `advertise_host` configuration for AudioSocket and ExternalMedia transports to support NAT, VPN, and hybrid cloud deployments where the AI engine binds to one address but Asterisk connects to a different address.

## Status: ✅ Completed

## Problem Statement

In NAT/VPN scenarios, the AI engine may need to:
- **Bind** to `0.0.0.0` or a private IP (e.g., `172.17.0.2` inside Docker)
- **Advertise** a different IP to Asterisk (e.g., `10.10.10.3` VPN IP or public IP)

Previously, a single `host`/`rtp_host` field was used for both purposes, making hybrid deployments impossible.

## Solution

Added `advertise_host` field to both `AudioSocketConfig` and `ExternalMediaConfig`:
- If set, Asterisk receives `advertise_host` in connection strings
- If not set, falls back to `bind_host` (backward compatible)

## Implementation Details

### Config Changes

| File | Change |
|------|--------|
| `src/config.py` | Added `advertise_host: Optional[str]` to `AudioSocketConfig` and `ExternalMediaConfig` |
| `src/config/defaults.py` | Added `AUDIOSOCKET_ADVERTISE_HOST` and `EXTERNAL_MEDIA_ADVERTISE_HOST` env var overrides |

### Engine Changes

| File | Change |
|------|--------|
| `src/engine.py` | AudioSocket endpoint uses `advertise_host` for Asterisk connection |
| `src/engine.py` | ExternalMedia `external_host` parameter uses `advertise_host` |
| `src/engine.py` | `/health` endpoint exposes `bind_host` and `advertise_host` |
| `src/engine.py` | `_compute_nat_warnings()` detects common misconfigurations |

### UI Changes

| File | Change |
|------|--------|
| `admin_ui/frontend/src/pages/Advanced/TransportPage.tsx` | Added "Advertise Host" fields with pre-fill from bind host |

### Documentation

| File | Change |
|------|--------|
| `config/ai-agent.example.yaml` | Added `advertise_host` examples with NAT/VPN scenarios |
| `.env.example` | Added `AUDIOSOCKET_ADVERTISE_HOST` and `EXTERNAL_MEDIA_ADVERTISE_HOST` |

### Tests

| File | Change |
|------|--------|
| `tests/config/test_defaults.py` | Added tests for advertise_host env var overrides |

## Configuration Examples

### Docker behind NAT
```yaml
audiosocket:
  host: "0.0.0.0"           # Bind inside container
  advertise_host: "192.168.1.100"  # Host's LAN IP
  port: 8090

external_media:
  rtp_host: "0.0.0.0"
  advertise_host: "192.168.1.100"
  rtp_port: 18080
```

### VPN Deployment
```yaml
audiosocket:
  host: "0.0.0.0"
  advertise_host: "10.10.10.3"  # VPN IP
  port: 8090
```

### Environment Variables
```bash
AUDIOSOCKET_ADVERTISE_HOST=10.10.10.3
EXTERNAL_MEDIA_ADVERTISE_HOST=10.10.10.3
```

## Remote Host Limitations

> **⚠️ Important: When AI Engine and Asterisk are on different machines**

### AudioSocket Transport ✅ Recommended for Remote Deployments

AudioSocket works seamlessly with remote Asterisk because:
- Audio is streamed bidirectionally over TCP
- No file-based playback required
- Full agent providers (OpenAI Realtime, Deepgram Voice Agent) use streaming playback

### ExternalMedia (RTP) Transport ⚠️ Requires Shared Storage

ExternalMedia with pipeline providers uses **file-based playback** via ARI, which requires:
- AI engine generates audio files locally
- Asterisk must access these files for playback
- **Without shared storage, Asterisk returns "File does not exist" errors**

**Solutions for ExternalMedia with remote Asterisk:**
1. **NFS/Shared mount**: Mount AI engine's `audio/` directory on Asterisk server
2. **Same machine**: Run AI engine on the same server as Asterisk
3. **Use AudioSocket instead**: Switch to AudioSocket transport for remote deployments

### Transport Selection Guide for Remote Hosts

| Scenario | Recommended Transport |
|----------|----------------------|
| AI engine & Asterisk same machine | Either works |
| AI engine remote, full agent provider | AudioSocket ✅ |
| AI engine remote, pipeline provider | AudioSocket ✅ or ExternalMedia + shared storage |
| Lowest latency priority | ExternalMedia (if shared storage available) |

## Health Endpoint

The `/health` endpoint now exposes NAT configuration:

```json
{
  "audiosocket": {
    "listening": true,
    "bind_host": "0.0.0.0",
    "advertise_host": "10.10.10.3",
    "port": 8090
  },
  "external_media": {
    "bind_host": "0.0.0.0",
    "advertise_host": "10.10.10.3",
    "rtp_port": 18080
  },
  "config_warnings": []
}
```

### NAT Warnings

The health endpoint includes `config_warnings` for common misconfigurations:
- Binding to `0.0.0.0` without `advertise_host`
- `advertise_host` set to localhost/127.0.0.1

## Testing

### Local NAT Testing on macOS

Docker Desktop on macOS cannot bind to VPN interfaces directly. Use macOS `pf` (packet filter) to forward traffic:

```bash
# Create pf rules (replace utun10 with your VPN interface)
cat > /tmp/vpn-docker-forward.conf << 'EOF'
rdr on utun10 proto tcp from any to 10.10.10.3 port 8090 -> 127.0.0.1 port 8090
rdr on utun10 proto tcp from any to 10.10.10.3 port 15000 -> 127.0.0.1 port 15000
rdr on utun10 proto udp from any to 10.10.10.3 port 18080 -> 127.0.0.1 port 18080
EOF

# Load rules
sudo pfctl -f /tmp/vpn-docker-forward.conf -e

# Verify
sudo pfctl -sn
```

### Revert pf Rules
```bash
sudo pfctl -F nat
```

## Acceptance Criteria

- [x] `advertise_host` field added to AudioSocket and ExternalMedia configs
- [x] Environment variable overrides work
- [x] Engine uses `advertise_host` for Asterisk connection strings
- [x] `/health` endpoint exposes bind and advertise hosts
- [x] NAT warning detection implemented
- [x] Admin UI updated with new fields
- [x] Example configs documented
- [x] Unit tests added
- [x] Remote host limitations documented

## Related Issues

- Remote Asterisk file playback limitation (ExternalMedia + pipelines)
- macOS Docker Desktop VPN interface limitations
