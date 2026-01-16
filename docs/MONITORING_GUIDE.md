# Monitoring Guide

Production observability guide for Asterisk AI Voice Agent `v5.1.4+` using Prometheus and Grafana.

> **Important (v4.5.3+, including v5.1.4)**: Prometheus metrics are intentionally **low-cardinality** and **do not include per-call labels** (e.g., no `call_id`).  
> Use **Admin UI → Call History** for per-call debugging, and use Prometheus/Grafana for aggregate health/latency/quality trends and alerting.

## Overview

The monitoring stack provides real-time observability into call quality, system health, and performance metrics essential for production deployments.

**Stack Components**:
- **Prometheus**: Time-series metrics collection and alerting (port 9090)
- **Grafana**: Visualization dashboards and analytics (port 3000)
- **ai-engine**: Metrics source via `/metrics` endpoint (port 15000)

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

Add a scrape target for `ai-engine`:

```yaml
scrape_configs:
  - job_name: 'ai-engine'
    metrics_path: '/metrics'
    static_configs:
      - targets: ['127.0.0.1:15000']
```

### 2. Verify Metrics Collection

```bash
# Check ai-engine health endpoint is responding
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
│  ai-engine  │────(every 1 second)──▶│ Prometheus │
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
**Action**: Verify ai-engine is running, check Asterisk connectivity

#### HealthEndpointDown
```yaml
alert: HealthEndpointDown
expr: up{job="ai-engine"} == 0
for: 30s
labels:
  severity: critical
annotations:
  summary: "ai-engine health endpoint unreachable"
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

### Call Quality Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `ai_agent_turn_response_seconds` | Histogram | Time from user speech end to agent response start |
| `ai_agent_stt_latency_seconds` | Histogram | Speech-to-text processing time |
| `ai_agent_llm_latency_seconds` | Histogram | LLM inference time |
| `ai_agent_tts_latency_seconds` | Histogram | Text-to-speech synthesis time |
| `ai_agent_barge_in_reaction_seconds` | Histogram | Time to react to user interruption |
| `ai_agent_stream_underflow_events_total` | Counter | Audio underflow events |
| `ai_agent_call_quality_score` | Gauge | Composite quality score (0-100) |

### System Health Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `ai_agent_active_calls` | Gauge | Current concurrent calls |
| `ai_agent_calls_started_total` | Counter | Total calls initiated |
| `ai_agent_calls_completed_total` | Counter | Total calls completed successfully |
| `ai_agent_calls_failed_total` | Counter | Total call failures |
| `ai_agent_audiosocket_connections` | Gauge | Active AudioSocket connections |
| `ai_agent_memory_usage_bytes` | Gauge | Memory consumption |
| `ai_agent_cpu_usage_percent` | Gauge | CPU utilization |

### Audio Quality Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `ai_agent_audio_rms_level` | Gauge | RMS audio level |
| `ai_agent_audio_dc_offset` | Gauge | DC offset in audio signal |
| `ai_agent_audio_bytes_total` | Counter | Audio bytes transmitted |
| `ai_agent_codec_mismatch_total` | Counter | Codec format mismatches |
| `ai_agent_sample_rate_hz` | Gauge | Current sample rate |

### Provider-Specific Metrics

**Deepgram**:
| Metric | Type | Description |
|--------|------|-------------|
| `ai_agent_deepgram_ack_latency_seconds` | Histogram | Time to first audio ACK |
| `ai_agent_deepgram_think_duration_seconds` | Histogram | Think stage processing time |

**OpenAI Realtime**:
| Metric | Type | Description |
|--------|------|-------------|
| `ai_agent_openai_rate_alignment_ratio` | Gauge | Measured/expected rate ratio |
| `ai_agent_openai_vad_toggle_total` | Counter | Server VAD state changes |

---

## PromQL Query Examples

### Performance Analysis

**Average turn response time by provider**:
```promql
avg by (provider) (rate(ai_agent_turn_response_seconds_sum[5m]) / rate(ai_agent_turn_response_seconds_count[5m]))
```

**Calls per minute**:
```promql
rate(ai_agent_calls_started_total[1m]) * 60
```

**Error rate percentage**:
```promql
(rate(ai_agent_calls_failed_total[5m]) / rate(ai_agent_calls_started_total[5m])) * 100
```

### Capacity Planning

**Peak concurrent calls (last 24h)**:
```promql
max_over_time(ai_agent_active_calls[24h])
```

**Average call duration**:
```promql
avg(ai_agent_call_duration_seconds)
```

**CPU usage during calls**:
```promql
ai_agent_cpu_usage_percent{active_calls > 0}
```

### Troubleshooting

**Calls with high latency (>3s)**:
```promql
count(ai_agent_turn_response_seconds_bucket{le="3"} == 0)
```

**Underflows per call (last hour)**:
```promql
sum(increase(ai_agent_stream_underflow_events_total[1h])) / sum(increase(ai_agent_calls_completed_total[1h]))
```

**Provider errors by type**:
```promql
sum by (provider, error_type) (rate(ai_agent_provider_errors_total[5m]))
```

---

## Troubleshooting

### Issue: No Metrics in Grafana

**Symptoms**: Dashboards show "No data" or empty panels

**Diagnosis**:
```bash
# 1. Check Prometheus is scraping
curl http://localhost:9090/api/v1/targets

# 2. Check ai-engine metrics endpoint
curl http://localhost:15000/metrics

# 3. Query Prometheus for any metric
curl 'http://localhost:9090/api/v1/query?query=up{job="ai-engine"}'
```

**Solutions**:
1. **ai-engine not running**: `docker ps | grep ai_engine`
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
1. **Rules file not loaded**: Check `prometheus.yml` includes `alerts/*.yml`
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

For distributed deployments with multiple ai-engine instances:

**Option 1: Centralized Prometheus**

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'ai-engine-cluster'
    static_configs:
      - targets:
          - 'ai-engine-1:15000'
          - 'ai-engine-2:15000'
          - 'ai-engine-3:15000'
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
        - '{job="ai-engine"}'
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

**Automated Dashboard Backup**:
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
# Backup first
./backup-grafana.sh
docker run --rm --volumes-from prometheus -v $(pwd)/backups:/backup alpine tar czf /backup/prometheus.tar.gz /prometheus

# Upgrade your Prometheus/Grafana stack per your deployment approach.

# Verify
curl http://localhost:9090/-/healthy
curl http://localhost:3000/api/health
```

---

## Further Reading

- **Monitoring/README.md**: Quick start and configuration reference
- **Prometheus Documentation**: https://prometheus.io/docs/
- **Grafana Documentation**: https://grafana.com/docs/
- **PromQL Tutorial**: https://prometheus.io/docs/prometheus/latest/querying/basics/
- **Alert Best Practices**: https://prometheus.io/docs/practices/alerting/

---

For deployment considerations, see [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md).

For hardware sizing, see [HARDWARE_REQUIREMENTS.md](HARDWARE_REQUIREMENTS.md).
