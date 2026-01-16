# Hardware Requirements

System specifications and performance characteristics for Asterisk AI Voice Agent v5.1.4.

## Overview

Hardware requirements vary significantly based on your chosen configuration. This guide provides guidance for the **5 golden baseline configurations**, plus the **Fully Local** option.

> **Note**: For fully local deployment (no cloud APIs), see [LOCAL_ONLY_SETUP.md](LOCAL_ONLY_SETUP.md) for detailed requirements including Sherpa-ONNX STT, Kokoro TTS, and local LLM options.

## Quick Reference

| Configuration | CPU | RAM | Disk | Network | Cost/min |
|---------------|-----|-----|------|---------|----------|
| **OpenAI Realtime** | 2+ cores | 4GB | 1GB | Stable internet | ~$0.06 |
| **Deepgram Voice Agent** | 2+ cores | 4GB | 1GB | Stable internet | ~$0.03 |
| **Google Live** | 2+ cores | 4GB | 1GB | Stable internet | Varies |
| **ElevenLabs Agent** | 2+ cores | 4GB | 1GB | Stable internet | Varies |
| **Local Hybrid** | 4+ cores (2020+) | 8-16GB | 2GB | Stable internet | ~$0.002 |
| **Fully Local** (optional) | 8+ cores recommended (or GPU) | 16GB+ recommended | 10GB+ | No internet required | $0 |

## Configuration-Specific Requirements

### 1. OpenAI Realtime (Cloud Monolithic)

**Minimum Specifications**:
- **CPU**: 2 cores @ 2.0GHz+
- **RAM**: 4GB
- **Disk**: 1GB (Docker images + logs)
- **Network**: 5 Mbps stable internet, <100ms latency to OpenAI

**Recommended Specifications**:
- **CPU**: 4 cores @ 2.5GHz+
- **RAM**: 8GB
- **Disk**: 5GB (includes monitoring stack)
- **Network**: 10+ Mbps, <50ms latency

**Performance Characteristics**:
- **Response Time**: 0.5-1.5 seconds (typical)
- **Concurrent Calls**: 20-50 per server (limited by network bandwidth)
- **CPU Usage**: ~5-10% per active call
- **Memory Usage**: ~100-200MB per active call
- **Network**: ~100-150 kbps per call (audio + API)

**Scaling**:
- **Bottleneck**: Network bandwidth and latency to OpenAI
- **Vertical**: Add CPU cores for more concurrent calls
- **Horizontal**: Run multiple ai-engine instances behind load balancer

---

### 2. Deepgram Voice Agent (Cloud Monolithic)

**Minimum Specifications**:
- **CPU**: 2 cores @ 2.0GHz+
- **RAM**: 4GB
- **Disk**: 2GB (includes audio file cache for file mode)
- **Network**: 5 Mbps stable internet, <100ms latency to Deepgram

**Recommended Specifications**:
- **CPU**: 4 cores @ 2.5GHz+
- **RAM**: 8GB
- **Disk**: 10GB (audio cache grows over time)
- **Network**: 10+ Mbps, <50ms latency

**Performance Characteristics**:
- **Response Time**: 1-2 seconds (typical)
- **Concurrent Calls**: 20-50 per server
- **CPU Usage**: ~8-12% per active call (file mode overhead)
- **Memory Usage**: ~150-250MB per active call
- **Disk I/O**: Moderate (writes TTS files, reads for playback)
- **Network**: ~80-120 kbps per call

**Scaling**:
- **Bottleneck**: Disk I/O for file-based playback
- **Storage**: Use SSD for the media volume (default: `./asterisk_media` on host, mounted as `/mnt/asterisk_media` in container)
- **Horizontal**: Multiple instances sharing NFS for audio files

---

### 3. Google Live (Cloud Monolithic)

**Minimum Specifications**:
- **CPU**: 2 cores @ 2.0GHz+
- **RAM**: 4GB
- **Disk**: 1GB
- **Network**: Stable internet (bidirectional streaming)

**Notes**:
- Typically the lowest-latency cloud baseline (<1s), but depends on network path and model selection.

---

### 4. ElevenLabs Agent (Cloud Monolithic)

**Minimum Specifications**:
- **CPU**: 2 cores @ 2.0GHz+
- **RAM**: 4GB
- **Disk**: 1GB
- **Network**: Stable internet (bidirectional streaming)

**Notes**:
- Premium voice quality; model/agent configuration impacts latency and cost.

---

### 5. Local Hybrid (Privacy-Focused Pipeline)

**Minimum Specifications**:
- **CPU**: 4 cores @ 2.5GHz+ (Intel i5-10400, AMD Ryzen 5 5600X or newer)
- **RAM**: 8GB
- **Disk**: 2GB (models + workspace)
- **Network**: 2 Mbps stable internet (LLM API only)

**Recommended Specifications**:
- **CPU**: 8 cores @ 3.0GHz+ (Intel i7-11700, AMD Ryzen 7 5800X or newer)
- **RAM**: 16GB
- **Disk**: 5GB SSD (fast model loading)
- **Network**: 5+ Mbps

**Performance Characteristics**:
- **Response Time**: 3-7 seconds (hardware-dependent)
- **Concurrent Calls**: 5-15 per server (CPU-bound)
- **CPU Usage**: ~50-80% per active call (STT/TTS processing)
- **Memory Usage**: ~500MB base + ~200MB per call
- **Disk I/O**: High during model loading, moderate during calls
- **Network**: ~10 kbps per call (text-only LLM API)

**Component Breakdown**:
- **Vosk STT**: ~100-200ms latency, ~20% CPU per call
- **OpenAI LLM**: 500-1500ms latency (cloud API)
- **Piper TTS**: ~200-400ms latency, ~30% CPU per call

**Model Storage**:
```
vosk-model-en-us-0.22/     ~500MB  (STT)
en_US-lessac-medium.onnx   ~100MB  (TTS)
Total:                     ~600MB
```

**CPU Generation Impact**:
| CPU Generation | Response Time | Concurrent Calls |
|----------------|---------------|------------------|
| 2014-2019 (Xeon E5 v3) | 10-30s | 1-2 (not practical) |
| 2020-2022 (Ryzen 5 5600X, i5-11400) | 3-7s | 5-10 |
| 2023+ (Ryzen 7 7800X, i7-13700) | 2-5s | 10-15 |

**Scaling**:
- **Bottleneck**: Local-ai-server CPU processing (STT/TTS)
- **Vertical**: Upgrade to modern CPU (2020+ architecture)
- **Horizontal**: Run multiple local-ai-server instances (complex)

---

## Fully Local (100% On-Premises) (Optional)

Fully Local mode runs **STT + LLM + TTS** on your own hardware with **no cloud APIs**.

- The **local LLM** is the bottleneck: CPU-only inference requires a modern CPU and enough RAM; for best UX and higher concurrency, use a GPU-backed local LLM where possible.
- Setup guide: `docs/LOCAL_ONLY_SETUP.md`

## GPU Acceleration (Optional)

### Local Hybrid with GPU

**GPU Requirements**:
- **Minimum**: NVIDIA RTX 3060 (12GB VRAM)
- **Recommended**: NVIDIA RTX 4060 Ti (16GB VRAM) or better
- **CUDA**: 11.8+
- **Docker**: NVIDIA Container Toolkit installed

**Performance Improvements**:
- **LLM Inference**: 10-30x faster (if using local LLM instead of OpenAI)
- **Response Time**: 1-3 seconds (with local LLM)
- **Concurrent Calls**: 20-40 (GPU-accelerated)

**Note**: The default local-ai-server image runs CPU-only. GPU acceleration is possible for local LLM inference via llama.cpp when you build/run with NVIDIA support (enable the Docker GPU config and tune `LOCAL_LLM_GPU_LAYERS`).

---

## Storage Requirements

### Disk Space

**Base Installation**:
- Docker images: ~1GB
- Python dependencies: ~200MB
- Total: ~1.5GB

**Per Configuration**:
- **OpenAI Realtime**: +500MB (logs, minimal cache)
- **Deepgram**: +2GB (audio file cache grows over time)
- **Local Hybrid**: +1GB (models + audio cache)

**Monitoring Stack** (optional):
- Prometheus: +500MB (30-day retention)
- Grafana: +200MB
- Total: ~700MB

**Production Recommendation**: 20GB+ disk space (allows for growth)

### Disk Performance

| Configuration | IOPS Requirement | Storage Type |
|---------------|------------------|--------------|
| OpenAI Realtime | Low (~10 IOPS) | HDD acceptable |
| Deepgram | Moderate (~100 IOPS) | SSD recommended |
| Local Hybrid | Moderate (~50 IOPS) | SSD recommended |

**Shared Storage** (for distributed deployments):
- **NFS**: Works but adds latency (5-20ms)
- **Local SSD**: Fastest, requires file sync
- **Network SSD**: Good balance (Ceph, GlusterFS)

---

## Network Requirements

### Bandwidth

**Per Active Call**:
- **OpenAI Realtime**: 100-150 kbps (bidirectional audio + API)
- **Deepgram**: 80-120 kbps (bidirectional audio + API)
- **Local Hybrid**: 10-20 kbps (LLM API text only, audio stays local)

**Example**: 20 concurrent calls
- OpenAI: ~3 Mbps
- Deepgram: ~2.4 Mbps
- Local Hybrid: ~0.4 Mbps (negligible)

### Latency

**Cloud Configurations** (OpenAI, Deepgram):
- **Target**: <50ms to provider API
- **Acceptable**: <100ms
- **Poor**: >150ms (increases response time)

**Local Hybrid**:
- **Target**: <100ms to OpenAI (LLM only)
- **Acceptable**: <200ms
- **Still Functional**: <500ms (only LLM affected, audio remains local)

### Ports Required

| Port | Protocol | Direction | Purpose |
|------|----------|-----------|---------|
| 8088 | TCP | ai-engine → Asterisk | ARI/WebSocket |
| 8090 | TCP | Asterisk → ai-engine | AudioSocket |
| 18080 | UDP | Bidirectional | ExternalMedia RTP |
| 15000 | TCP | Monitoring → ai-engine | Health/Metrics |
| 8765 | TCP | ai-engine → local-ai-server | WebSocket (Local Hybrid only) |
| 9090 | TCP | Browser → Prometheus | Monitoring (optional) |
| 3000 | TCP | Browser → Grafana | Monitoring (optional) |

---

## Virtualization & Cloud

### VMware / Proxmox / KVM

**Works Well**:
- All configurations supported
- CPU passthrough recommended for Local Hybrid
- Dedicated vCPUs (not shared)

**Configuration Tips**:
- **CPU**: Use "host" CPU type, not generic
- **Memory**: No ballooning, dedicated allocation
- **Disk**: virtio-scsi with discard support
- **Network**: virtio-net with multi-queue

### Cloud Providers

#### AWS EC2

| Configuration | Instance Type | Monthly Cost (estimate) |
|---------------|---------------|-------------------------|
| OpenAI Realtime | t3.medium (2 vCPU, 4GB) | ~$30 |
| Deepgram | t3.large (2 vCPU, 8GB) | ~$60 |
| Local Hybrid | c5.2xlarge (8 vCPU, 16GB) | ~$250 |

**Note**: Add data transfer costs (~$0.09/GB egress)

#### Google Cloud (GCE)

| Configuration | Machine Type | Monthly Cost (estimate) |
|---------------|--------------|-------------------------|
| OpenAI Realtime | e2-medium (2 vCPU, 4GB) | ~$25 |
| Deepgram | e2-standard-2 (2 vCPU, 8GB) | ~$50 |
| Local Hybrid | c2-standard-8 (8 vCPU, 32GB) | ~$240 |

#### Azure

| Configuration | VM Size | Monthly Cost (estimate) |
|---------------|---------|-------------------------|
| OpenAI Realtime | Standard_B2s (2 vCPU, 4GB) | ~$30 |
| Deepgram | Standard_B2ms (2 vCPU, 8GB) | ~$60 |
| Local Hybrid | Standard_F8s_v2 (8 vCPU, 16GB) | ~$280 |

**Cloud Considerations**:
- Local Hybrid is expensive in cloud (CPU-intensive)
- Cloud configs benefit from proximity to provider APIs
- Use cloud provider's region closest to AI provider

---

## Capacity Planning

### Calls per Server

**Formula**: `max_concurrent_calls = (CPU_cores * 0.8) / cpu_per_call`

**Examples**:

**8-core server**:
- OpenAI Realtime: ~32 concurrent calls (0.2 CPU per call)
- Deepgram: ~26 concurrent calls (0.25 CPU per call)
- Local Hybrid: ~6 concurrent calls (1.0 CPU per call)

**16-core server**:
- OpenAI Realtime: ~64 concurrent calls
- Deepgram: ~51 concurrent calls
- Local Hybrid: ~12 concurrent calls

### Growth Planning

**Traffic Growth Scenarios**:

**Scenario 1: Small Business** (10 peak concurrent calls)
- Start: 4-core server
- 6 months: Same server (plenty of headroom)
- 1 year: Consider 8-core for growth

**Scenario 2: Enterprise** (100 peak concurrent calls)
- Start: 3x 16-core servers (cloud configs) or 10x 8-core (Local Hybrid)
- Load balancer required
- Monitoring essential

**Scenario 3: Call Center** (500+ concurrent calls)
- Kubernetes deployment recommended
- Horizontal scaling (20-50 pods)
- Multi-region for redundancy

---

## Monitoring Resource Usage

### Prometheus Metrics

Key metrics to watch:
- `system_cpu_usage_percent` - CPU utilization
- `system_memory_usage_bytes` - RAM usage
- `active_calls_total` - Current call load
- `turn_response_latency_seconds` - Performance indicator

### Alert Thresholds

**CPU**:
- Warning: >70% sustained
- Critical: >85% sustained
- Action: Scale horizontally or upgrade

**Memory**:
- Warning: >80% used
- Critical: >90% used
- Action: Add RAM or reduce concurrent calls

**Disk**:
- Warning: >80% full
- Critical: >90% full
- Action: Increase storage or clean old audio files

---

## Validation Tests

Before production deployment, run these tests:

### Load Test

```bash
# Simulate 10 concurrent calls for 5 minutes
for i in {1..10}; do
  asterisk -rx "channel originate Local/s@from-ai-agent application Wait 300" &
done

# Monitor CPU/RAM during test
docker stats ai_engine local_ai_server
```

### Performance Baseline

```bash
# Make test call and measure response time
# Check logs for turn_response_latency

docker compose logs -f ai_engine | grep "turn_response_latency"
```

Expected response times:
- OpenAI: 0.5-1.5s
- Deepgram: 1-2s
- Local Hybrid: 3-7s

### Stress Test

Push to 150% of expected capacity:
- Monitor for degradation
- Check error rates
- Verify graceful handling

---

## Optimization Tips

### CPU Optimization

**For Local Hybrid**:
1. Use CPU with AVX-512 instructions (2020+ Intel, 2022+ AMD)
2. Disable CPU throttling/power saving
3. Set CPU governor to "performance"
   ```bash
   sudo cpupower frequency-set -g performance
   ```

### Memory Optimization

**All Configurations**:
1. Disable swap if using SSD (reduces latency)
2. Use huge pages for better memory performance
3. Set Docker memory limits to prevent OOM

### Disk Optimization

**For Deepgram (file mode)**:
1. Use SSD for `./asterisk_media/ai-generated` (mounted as `/mnt/asterisk_media/ai-generated` in container)
2. Enable discard/TRIM
3. Periodic cleanup of old audio files:
   ```bash
   find ./asterisk_media/ai-generated -type f -mtime +7 -delete
   ```

### Network Optimization

1. Use quality ISP with low latency to cloud providers
2. Enable QoS for RTP traffic (UDP 18080)
3. Monitor packet loss: target <0.1%

---

## Summary

### Configuration Comparison

| Aspect | OpenAI Realtime | Deepgram | Local Hybrid |
|--------|----------------|----------|--------------|
| **Ease of Deployment** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| **Hardware Cost** | Low | Low | Medium-High |
| **Operating Cost** | High | Medium | Very Low |
| **Scalability** | Excellent | Excellent | Limited |
| **Privacy** | Low | Low | High |
| **Response Time** | Fastest | Fast | Moderate |
| **Concurrent Calls** | High | High | Low-Medium |

### Recommended Use Cases

**OpenAI Realtime**:
- Quick deployment, budget for API costs
- Modern conversational AI needed
- 5-50 concurrent calls

**Deepgram Voice Agent**:
- Already using Deepgram ecosystem
- Need Think stage for complex reasoning
- 5-50 concurrent calls

**Local Hybrid**:
- Audio privacy critical (HIPAA, GDPR)
- Cost control priority (90% cheaper)
- Modern hardware available (2020+ CPUs)
- 5-20 concurrent calls

---

For deployment guidance, see [docs/PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md).

For monitoring setup, see [docs/MONITORING_GUIDE.md](MONITORING_GUIDE.md).
