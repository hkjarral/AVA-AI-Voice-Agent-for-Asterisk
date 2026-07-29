# Monitoring Guide

Production observability guide for Asterisk AI Voice Agent `v6.0+`.

> **Note**: Prometheus and Grafana are **not shipped** with AAVA. This guide provides reference configurations for operators who bring their own monitoring stack. For per-call debugging, use **Admin UI → Call History**.

> **Important (v4.5.3+)**: Prometheus metrics are intentionally **low-cardinality** and **do not include per-call labels** (e.g., no `call_id`).  
> Use **Admin UI → Call History** for per-call debugging, and use Prometheus/Grafana for aggregate health/latency/quality trends and alerting.

## Overview

The monitoring stack provides real-time observability into call quality, system health, and performance metrics essential for production deployments.

**Stack Components**:
- **Prometheus**: Time-series metrics collection and alerting (port 9090)
- **Grafana**: Visualization dashboards and analytics (port 3000)
- **ai_engine**: Metrics source via `/metrics` endpoint (port 15000)

**Key Benefits**:
- **Aggregate health + quality signals**: latency histograms, underruns, bytes, and session counts
- **Alerting**: catch systemic regressions quickly (provider outages, underruns, timeouts)
- **Operational trends**: capacity planning and tuning over time

**Not a goal** (by design):
- **Per-call correlation in Prometheus** (no `call_id` label)

---

## Quick Start

### 1. Configure Prometheus (Bring Your Own)

```bash
cd /path/to/Asterisk-AI-Voice-Agent
```

Add a scrape target for `ai_engine`:

```yaml
scrape_configs:
  - job_name: 'ai_engine'
    metrics_path: '/metrics'
    static_configs:
      - targets: ['127.0.0.1:15000']
```

### 2. Verify Metrics Collection

```bash
# Check ai_engine health endpoint is responding
curl http://localhost:15000/health

# View sample metrics
curl http://localhost:15000/metrics | head -30
```

**Healthy output includes**:
- `ai_agent_streaming_active` - Active streaming sessions (not a global “active calls” gauge)
- `ai_agent_turn_response_seconds` - Latency metrics
- `ai_agent_stream_underflow_events_total` - Audio quality

---

## Per-call Debugging (recommended)

For “what happened on *this* call?” debugging, use **Call History**:

- **Admin UI**: navigate to `/history` (Call History) and search/filter by time/provider/outcome.
- **Database**: stored under the mounted `./data` volume by default (`CALL_HISTORY_DB_PATH`, default `/app/data/call_history.db`).
- **Logs correlation**: Call History entries include the `call_id`; search structured logs for that `call_id`.

---

## Architecture

### Data Flow

```
┌─────────────┐     HTTP scrape      ┌────────────┐
│  ai_engine  │────(every 1 second)──▶│ Prometheus │
│ :15000      │                       │ :9090      │
└─────────────┘                       └──────┬─────┘
                                             │
                                    PromQL queries
                                             │
                                       ┌─────▼──────┐
                                       │  Grafana   │
                                       │  :3000     │
                                       └────────────┘
```

### Metrics Collection

**Scrape Interval**: 1 second (high resolution for call quality)
**Retention**: 30 days default (configurable)
**Storage**: Prometheus TSDB (time-series database)

### Metric Types

1. **Counter**: Monotonically increasing (e.g., `underflow_events_total`)
2. **Gauge**: Current value (e.g., `active_calls`)
3. **Histogram**: Distribution (e.g., `turn_response_seconds`)
4. **Summary**: Percentiles pre-calculated

---

## Dashboards

### Dashboard 1: System Overview

**Purpose**: High-level system health at a glance

**Key Panels**:
- **Active Calls**: Current concurrent calls (gauge)
- **Call Rate**: Calls per minute (graph)
- **Provider Distribution**: Pie chart of provider usage
- **System Health**: CPU, memory, container status
- **Error Rate**: Errors per minute

**When to Use**: Daily operations monitoring, capacity tracking

**Screenshots**: Not shipped (bring-your-own Grafana dashboards)

---

### Dashboard 2: Call Quality

**Purpose**: Detailed call quality metrics and performance

**Key Panels**:
- **Turn Response Latency**: p50/p95/p99 histograms
- **STT→TTS Processing Time**: Pipeline latency breakdown
- **Underflow Events**: Audio quality issues
- **Jitter Buffer Depth**: Streaming buffer status
- **Quality Score**: Composite quality metric

**Key Metrics**:
```promql
# Turn response latency (p95, last 5 minutes)
histogram_quantile(0.95, rate(ai_agent_turn_response_seconds_bucket[5m]))

# Underflow rate (per call)
rate(ai_agent_stream_underflow_events_total[5m]) / rate(ai_agent_streaming_sessions_total[5m])

# Quality score (0-100, higher = better)
ai_agent_call_quality_score
```

**Alert Thresholds**:
- 🟢 **Good**: p95 latency < 1.5s, 0 underflows
- 🟡 **Warning**: p95 latency 1.5-2s, <2 underflows/call
- 🔴 **Critical**: p95 latency > 2s, >5 underflows/call

**When to Use**: Performance tuning, quality assurance, SLA validation

---

### Dashboard 3: Provider Performance

**Purpose**: Compare provider-specific metrics and health

**Key Panels**:
- **Provider Latency Comparison**: Side-by-side histograms
- **Provider Health**: Connection status, error rates
- **Deepgram Metrics**: ACK latency, sample rates, Think stage timing
- **OpenAI Realtime Metrics**: Rate alignment, server VAD performance
- **Provider Costs**: Estimated API usage

**Provider-Specific Metrics**:

**Deepgram**:
```promql
# ACK latency (time until first audio acknowledgment)
ai_agent_deepgram_ack_latency_seconds

# Think stage duration
ai_agent_deepgram_think_duration_seconds
```

**OpenAI Realtime**:
```promql
# Rate alignment (should be close to 1.0)
ai_agent_openai_rate_alignment_ratio

# VAD toggle frequency
rate(ai_agent_openai_vad_toggle_total[5m])
```

**When to Use**: Provider selection, cost optimization, debugging provider-specific issues

---

### Dashboard 4: Audio Quality

**Purpose**: Low-level audio transport and codec metrics

**Key Panels**:
- **RMS Levels**: Pre/post companding audio levels
- **DC Offset**: Audio signal balance
- **Codec Alignment**: Format match verification
- **Bytes TX/RX**: Audio data transfer rates
- **Sample Rate Verification**: Expected vs actual rates
- **VAD Performance**: Voice activity detection accuracy

**Critical Audio Metrics**:
```promql
# RMS levels (should be in 1000-8000 range for telephony)
ai_agent_audio_rms_level

# Codec mismatches (should be 0)
sum(rate(ai_agent_codec_mismatch_total[5m]))

# Audio bytes per second (should match expected rate)
rate(ai_agent_audio_bytes_total[5m])
```

**When to Use**: Audio quality debugging, codec troubleshooting, transport validation

---

### Dashboard 5: Conversation Flow

**Purpose**: Call state machine and conversation flow analysis

**Key Panels**:
- **State Transitions**: Call lifecycle visualization
- **Gating Events**: Audio gate open/close frequency
- **Barge-In Activity**: User interruptions count
- **Turn Count Distribution**: Conversation lengths
- **Config Exposure**: Runtime configuration visibility

**Conversation Metrics**:
```promql
# Average turns per call
sum(ai_agent_conversation_turns_total) / sum(ai_agent_calls_completed_total)

# Barge-in rate (interruptions per call)
sum(rate(ai_agent_barge_in_triggered_total[5m])) / sum(rate(ai_agent_calls_started_total[5m]))

# Gating toggle frequency (higher = potential echo issues)
rate(ai_agent_gating_toggle_total[5m])
```

**When to Use**: Conversation UX optimization, barge-in tuning, echo debugging

---

## Alerting

### Alert Configuration

This project no longer ships a bundled Prometheus/Grafana alert stack. Define alert rules in your own Prometheus config, and keep label cardinality low (no `call_id`, caller number/name, etc.).

### Critical Alerts (Immediate Action Required)

#### CriticalTurnResponseLatency
```yaml
alert: CriticalTurnResponseLatency
expr: histogram_quantile(0.95, rate(ai_agent_turn_response_seconds_bucket[5m])) > 5
for: 2m
labels:
  severity: critical
annotations:
  summary: "Turn response latency critically high"
  description: "p95 latency is {{ $value }}s (threshold: 5s)"
```
**Action**: Check provider connectivity, CPU usage, system load

#### NoAudioSocketConnections
```yaml
alert: NoAudioSocketConnections
expr: ai_agent_audiosocket_connections == 0
for: 1m
labels:
  severity: critical
annotations:
  summary: "No active AudioSocket connections"
```
**Action**: Verify `ai_engine` is running, check Asterisk connectivity

#### HealthEndpointDown
```yaml
alert: HealthEndpointDown
expr: up{job="ai_engine"} == 0
for: 30s
labels:
  severity: critical
annotations:
  summary: "ai_engine health endpoint unreachable"
```
**Action**: Check container status, review logs, restart if needed

### Warning Alerts (Investigate Soon)

#### HighTurnResponseLatency
- **Threshold**: p95 > 2s
- **Action**: Monitor trends, consider scaling if sustained

#### HighUnderflowRate
- **Threshold**: > 5 underflows/second
- **Action**: Check network jitter, review buffer settings

#### CodecMismatch
- **Threshold**: Any codec mismatches detected
- **Action**: Review audio configuration, check provider formats

#### SlowBargeInReaction
- **Threshold**: p95 > 1s
- **Action**: Tune barge-in settings, check VAD configuration

### Viewing Active Alerts

**In Prometheus**: http://localhost:9090/alerts

**In Grafana**: Navigate to Alerting → Alert Rules

---

## Metric Reference

> **Source-of-truth note:** the tables below were verified against the metric definitions in `src/engine.py`, `src/core/streaming_playback_manager.py`, `src/core/vad_manager.py`, `src/core/conversation_coordinator.py`, and `src/providers/*.py` as of 2026-04-27. If you hit `/metrics` and see a name not listed here, file a docs bug.

### Call lifecycle & latency

| Metric | Type | Description | Defined in |
|--------|------|-------------|------------|
| `ai_agent_call_duration_seconds` | Histogram | End-to-end call duration | `src/engine.py` |
| `ai_agent_turn_response_seconds` | Histogram | Time from user speech end to agent response start | `src/engine.py` |
| `ai_agent_stt_to_tts_seconds` | Histogram | STT-finalize → TTS-first-byte (pipeline-mode) | `src/engine.py` |
| `ai_agent_barge_in_reaction_seconds` | Histogram | Time to react to user interruption | `src/engine.py` |
| `ai_agent_barge_in_events_total` | Counter | Total barge-in events | `src/core/conversation_coordinator.py` |
| `ai_agent_conversation_state` | Gauge | Current state (encoded; see source for enum) | `src/core/conversation_coordinator.py` |
| `ai_agent_tts_gating_active` | Gauge | 1 when TTS-output is gating microphone capture | `src/core/conversation_coordinator.py` |
| `ai_agent_audio_capture_enabled` | Gauge | 1 when inbound capture is unmuted | `src/core/conversation_coordinator.py` |

### Streaming health (downstream audio path)

| Metric | Type | Description |
|--------|------|-------------|
| `ai_agent_streaming_active` | Gauge | Calls with streaming playback active (operator's "concurrent calls" proxy) |
| `ai_agent_stream_started_total` | Counter | Streaming segments started (label: `playback_type`) |
| `ai_agent_stream_first_frame_seconds` | Histogram | Stream-start → first outbound frame (label: `playback_type`) |
| `ai_agent_stream_segment_duration_seconds` | Histogram | Streaming segment duration (label: `playback_type`) |
| `ai_agent_stream_end_reason_total` | Counter | Stream end reasons (label: `reason`) |
| `ai_agent_stream_underflow_events_total` | Counter | 20ms-filler underflow events |
| `ai_agent_stream_filler_bytes_total` | Counter | Filler bytes injected on underflow |
| `ai_agent_stream_frames_sent_total` | Counter | Frames (20ms) actually sent |
| `ai_agent_stream_tx_bytes_total` | Counter | Outbound audio bytes sent to caller |
| `ai_agent_stream_rx_bytes_total` | Counter | Inbound audio bytes received |
| `ai_agent_streaming_bytes_total` | Counter | Bytes queued to streaming playback (pre-conversion) |
| `ai_agent_streaming_fallbacks_total` | Counter | Times streaming fell back to file playback |
| `ai_agent_streaming_jitter_buffer_depth` | Gauge | Max jitter buffer depth across active streams |
| `ai_agent_streaming_last_chunk_age_seconds` | Gauge | Max seconds since last streaming chunk |
| `ai_agent_streaming_keepalives_sent_total` | Counter | Keepalive ticks sent while streaming |
| `ai_agent_streaming_keepalive_timeouts_total` | Counter | Keepalive-detected streaming timeouts |
| `ai_agent_stream_endian_corrections_total` | Counter | Auto-corrected PCM16 byte-order issues (label: `mode`) |

### Audio & VAD quality

| Metric | Type | Description |
|--------|------|-------------|
| `ai_agent_audio_rms` | Gauge | RMS audio level |
| `ai_agent_audio_dc_offset` | Gauge | DC offset in inbound audio |
| `ai_agent_codec_alignment` | Gauge | Codec/sample-rate alignment indicator (1 = aligned) |
| `ai_agent_vad_frames_total` | Counter | VAD frames processed |
| `ai_agent_vad_confidence` | Histogram | Per-frame VAD confidence |
| `ai_agent_vad_adaptive_threshold` | Gauge | Current adaptive VAD threshold |

### Config snapshot gauges

These echo the running config values so dashboards can correlate behavior with settings without a separate config feed:

| Metric | Type | Description |
|--------|------|-------------|
| `ai_agent_config_barge_in_ms` | Gauge | Configured `barge_in.min_ms` |
| `ai_agent_config_barge_in_threshold` | Gauge | Configured `barge_in.energy_threshold` |
| `ai_agent_config_streaming_ms` | Gauge | Configured streaming start/jitter window |
| `ai_agent_config_turn_detection_ms` | Gauge | Configured turn-detection silence window |
| `ai_agent_config_turn_detection_threshold` | Gauge | Configured turn-detection threshold |

### Provider-specific

**Google Live** (`src/providers/google_live.py`):
| Metric | Type | Description |
|--------|------|-------------|
| `ai_agent_google_live_active_sessions` | Gauge | Active Google Live WebSocket sessions |
| `ai_agent_google_live_audio_bytes_sent` | Counter | Audio bytes sent to Gemini Live |
| `ai_agent_google_live_audio_bytes_received` | Counter | Audio bytes received from Gemini Live |

**Deepgram** (`src/providers/deepgram.py`):
| Metric | Type | Description |
|--------|------|-------------|
| `ai_agent_deepgram_input_sample_rate_hz` | Gauge | Input sample rate negotiated with Deepgram |
| `ai_agent_deepgram_output_sample_rate_hz` | Gauge | Output sample rate from Deepgram |
| `ai_agent_deepgram_settings_ack_latency_ms` | Gauge | Settings-ack latency (ms) |

**OpenAI Realtime** (`src/providers/openai_realtime.py`):
| Metric | Type | Description |
|--------|------|-------------|
| `ai_agent_openai_assumed_output_rate` | Gauge | Output rate the engine assumes |
| `ai_agent_openai_provider_output_rate` | Gauge | Output rate provider declared |
| `ai_agent_openai_measured_output_rate` | Gauge | Output rate measured at runtime |

> **Removed from earlier docs (don't exist in code):** `ai_agent_calls_started_total`, `ai_agent_calls_completed_total`, `ai_agent_calls_failed_total`, `ai_agent_audiosocket_connections`, `ai_agent_memory_usage_bytes`, `ai_agent_cpu_usage_percent`, `ai_agent_call_quality_score`, `ai_agent_stt_latency_seconds`, `ai_agent_llm_latency_seconds`, `ai_agent_tts_latency_seconds`, `ai_agent_audio_bytes_total`, `ai_agent_codec_mismatch_total`, `ai_agent_sample_rate_hz`, `ai_agent_deepgram_ack_latency_seconds`, `ai_agent_deepgram_think_duration_seconds`, `ai_agent_openai_rate_alignment_ratio`, `ai_agent_openai_vad_toggle_total`. Use the documented alternatives above (e.g., for "calls started" use `ai_agent_stream_started_total`; for memory/CPU use the standard `process_*` and `python_*` metrics that `prometheus_client` exposes by default).

---

## PromQL Query Examples

### Performance Analysis

**Average turn response time by provider**:
```promql
avg by (provider) (rate(ai_agent_turn_response_seconds_sum[5m]) / rate(ai_agent_turn_response_seconds_count[5m]))
```

**Streaming segments per minute** (closest signal to "calls per minute"):
```promql
rate(ai_agent_stream_started_total[1m]) * 60
```

**Stream end-reason mix** (use to detect failure modes):
```promql
sum by (reason) (rate(ai_agent_stream_end_reason_total[5m]))
```

### Capacity Planning

**Peak concurrent active streams (last 24h)**:
```promql
max_over_time(ai_agent_streaming_active[24h])
```

**Average call duration**:
```promql
sum(rate(ai_agent_call_duration_seconds_sum[5m])) / sum(rate(ai_agent_call_duration_seconds_count[5m]))
```

**Process CPU & memory** (from `prometheus_client`'s default exports — no custom metric needed):
```promql
rate(process_cpu_seconds_total[5m])
process_resident_memory_bytes
```

### Troubleshooting

**Calls with high latency (>3s)**:
```promql
count(ai_agent_turn_response_seconds_bucket{le="3"} == 0)
```

**Underflows per stream segment (last hour)**:
```promql
sum(increase(ai_agent_stream_underflow_events_total[1h])) / sum(increase(ai_agent_stream_started_total[1h]))
```

**Streaming-fallback rate (provider audio gaps causing fallback to file playback)**:
```promql
rate(ai_agent_streaming_fallbacks_total[5m])
```

---

## Troubleshooting

### Issue: No Metrics in Grafana

**Symptoms**: Dashboards show "No data" or empty panels

**Diagnosis**:
```bash
# 1. Check Prometheus is scraping
curl http://localhost:9090/api/v1/targets

# 2. Check `ai_engine` metrics endpoint
curl http://localhost:15000/metrics

# 3. Query Prometheus for any metric
curl 'http://localhost:9090/api/v1/query?query=up{job="ai_engine"}'
```

**Solutions**:
1. **`ai_engine` not running**: `docker ps | grep ai_engine`
2. **Metrics endpoint unreachable**: Check port 15000 not blocked
3. **Prometheus configuration error**: `docker logs prometheus`
4. **Wrong data source in Grafana**: Check Grafana → Configuration → Data Sources

---

### Issue: Dashboards Not Loading

**Symptoms**: Grafana shows blank or missing dashboards

**Diagnosis**:
```bash
# Check Grafana provisioning logs
docker logs grafana | grep -i provision
```

**Solutions**:
1. **Dashboards not provisioned**: Ensure your Grafana provisioning mounts are correct
2. **Data source missing**: Ensure Prometheus data source URL is correct and reachable
3. **Grafana not provisioned**: Restart Grafana container and re-check provisioning logs

---

### Issue: Alerts Not Firing

**Symptoms**: Expected alerts don't trigger

**Diagnosis**:
```bash
# Check alert rules loaded
curl http://localhost:9090/api/v1/rules

# Check current alert status
curl http://localhost:9090/api/v1/alerts

# Verify alert evaluation
docker logs prometheus | grep -i alert
```

**Solutions**:
1. **Rules file not loaded**: Ensure your Prometheus config loads your rules files (example below)
2. **Threshold not met**: Lower threshold temporarily to test
3. **'for' duration not elapsed**: Wait for specified duration
4. **Alertmanager not configured**: Alerts fire but have no destination

---

### Issue: High Memory Usage in Prometheus

**Symptoms**: Prometheus container using excessive RAM

**Diagnosis**:
```bash
# Check Prometheus memory usage
docker stats prometheus

# Check TSDB size
docker exec prometheus du -sh /prometheus
```

**Solutions**:
1. **Long retention period**: Reduce your Prometheus retention window
2. **High cardinality metrics**: Review metric labels
3. **Too frequent scraping**: Increase scrape_interval (not recommended)
4. **Increase memory**: Allocate more RAM to Prometheus (container limit or host)

---

## Production Deployment

### Multi-Server Setup

For distributed deployments with multiple `ai_engine` instances:

**Option 1: Centralized Prometheus**

```yaml
# prometheus.yml (example)
rule_files:
  - "alerts/*.yml"

scrape_configs:
  - job_name: 'ai_engine_cluster'
    static_configs:
      - targets:
          - 'engine-1:15000'
          - 'engine-2:15000'
          - 'engine-3:15000'
    relabel_configs:
      - source_labels: [__address__]
        target_label: instance
```

**Option 2: Prometheus Federation**

```yaml
# Central Prometheus scrapes regional Prometheus instances
scrape_configs:
  - job_name: 'federate'
    scrape_interval: 15s
    honor_labels: true
    metrics_path: '/federate'
    params:
      'match[]':
        - '{job="ai_engine"}'
    static_configs:
      - targets:
          - 'prometheus-us-east:9090'
          - 'prometheus-us-west:9090'
          - 'prometheus-eu:9090'
```

### Security Hardening

**1. Enable Authentication**:
```yaml
# In your Grafana configuration
environment:
  - GF_AUTH_BASIC_ENABLED=true
  - GF_AUTH_ANONYMOUS_ENABLED=false
  - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD}
```

**2. Use HTTPS**:
```yaml
# Add reverse proxy (nginx, Caddy) in front of Grafana
  nginx:
    image: nginx:alpine
    ports:
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
      - ./ssl:/etc/nginx/ssl
```

**3. Network Isolation**:
```yaml
# Isolate monitoring network
networks:
  monitoring:
    driver: bridge
    internal: false  # Only expose Grafana externally
```

### Backup Strategy

**Automated Dashboard Backup (example)**:
```bash
#!/bin/bash
# backup-grafana.sh
BACKUP_DIR="/backups/grafana/$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"

# Backup dashboards via API
curl -H "Authorization: Bearer ${GRAFANA_API_KEY}" \
  http://localhost:3000/api/search | \
  jq -r '.[] | .uri' | \
  while read uri; do
    curl -H "Authorization: Bearer ${GRAFANA_API_KEY}" \
      "http://localhost:3000/api${uri}" > "$BACKUP_DIR/$(basename $uri).json"
  done
```

**Prometheus Data Backup**:
```bash
# Prometheus stores data in Docker volume
docker run --rm \
  --volumes-from prometheus \
  -v $(pwd)/backups:/backup \
  alpine tar czf /backup/prometheus-$(date +%Y%m%d).tar.gz /prometheus
```

---

## Best Practices

### 1. Alert Tuning

- **Start Conservative**: Set thresholds loose initially, tighten based on actual performance
- **Use 'for' Duration**: Avoid alert fatigue with transient issues
- **Group Related Alerts**: Send batches to reduce noise
- **Document Actions**: Each alert should have clear remediation steps

### 2. Dashboard Organization

- **Keep System Overview Simple**: 5-7 key metrics maximum
- **Drill-Down Pattern**: Overview → Category → Detailed
- **Use Consistent Colors**: Green/yellow/red for status, consistent provider colors
- **Add Annotations**: Mark deployments, incidents on graphs

### 3. Performance Optimization

- **Use Recording Rules**: Pre-calculate complex queries
  ```yaml
  # Example recording rule
  - record: job:ai_agent_latency:p95
    expr: histogram_quantile(0.95, sum by (job, le) (rate(ai_agent_turn_response_seconds_bucket[5m])))
  ```
- **Limit Retention**: 30-90 days typically sufficient
- **Monitor Prometheus**: Track Prometheus's own metrics
- **Use Downsampling**: For long-term storage, use Thanos or Cortex

### 4. Operational Workflow

- **Daily Review**: Check system overview dashboard each morning
- **Weekly Analysis**: Review trends, tune alerts
- **Monthly Capacity Planning**: Analyze growth trends
- **Post-Incident**: Review metrics during incident timeline

---

## Integration with Other Tools

### Log Aggregation (Loki)

```yaml
# Add Loki to your monitoring stack
  loki:
    image: grafana/loki:latest
    ports:
      - "3100:3100"
    volumes:
      - ./loki-config.yaml:/etc/loki/local-config.yaml

# Configure Grafana to use Loki as data source
# Correlate logs with Call History using call_id (Prometheus metrics intentionally do not use per-call labels)
```

### Tracing (Tempo)

```yaml
# Add distributed tracing for multi-component calls
  tempo:
    image: grafana/tempo:latest
    ports:
      - "3200:3200"
```

### PagerDuty / Slack / Email

```yaml
# alertmanager.yml
receivers:
  - name: 'pagerduty'
    pagerduty_configs:
      - service_key: '<pagerduty-key>'
        
  - name: 'slack'
    slack_configs:
      - api_url: '<slack-webhook>'
        channel: '#ai-voice-alerts'
```

---

## Maintenance

### Regular Tasks

**Daily**:
- Check dashboard for anomalies
- Review active alerts
- Verify scrape targets healthy

**Weekly**:
- Review alert trends
- Check disk space usage
- Validate backup success

**Monthly**:
- Update Prometheus/Grafana images
- Review and tune alert thresholds
- Analyze capacity trends
- Prune old data if needed

### Version Upgrades

```bash
# Backup first (use your own backup procedure; example script above)
docker run --rm --volumes-from prometheus -v $(pwd)/backups:/backup alpine tar czf /backup/prometheus.tar.gz /prometheus

# Upgrade your Prometheus/Grafana stack per your deployment approach.

# Verify
curl http://localhost:9090/-/healthy
curl http://localhost:3000/api/health
```

---

## Further Reading

- **Prometheus Documentation**: https://prometheus.io/docs/
- **Grafana Documentation**: https://grafana.com/docs/
- **PromQL Tutorial**: https://prometheus.io/docs/prometheus/latest/querying/basics/
- **Alert Best Practices**: https://prometheus.io/docs/practices/alerting/

---

For deployment considerations, see [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md).

For hardware sizing, see [HARDWARE_REQUIREMENTS.md](HARDWARE_REQUIREMENTS.md).
