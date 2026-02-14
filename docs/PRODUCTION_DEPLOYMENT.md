# Production Deployment Guide

Best practices and recommendations for deploying Asterisk AI Voice Agent `v6.0+` in production environments.

## Overview

This guide covers production deployment considerations, security hardening, scaling strategies, and operational best practices for running Asterisk AI Voice Agent at scale.

**Topics Covered**:
- Deployment architecture patterns
- Security hardening and compliance
- High availability considerations
- Backup and disaster recovery
- Operational procedures
- Upgrade strategies

---

## Pre-Deployment Checklist

### Infrastructure Requirements

- [ ] **Hardware**: Sized per [HARDWARE_REQUIREMENTS.md](HARDWARE_REQUIREMENTS.md)
- [ ] **Operating System**: Linux (Ubuntu 20.04+, CentOS 8+, Debian 11+)
- [ ] **Docker**: Version 20.10+
- [ ] **Docker Compose**: Version 2.0+
- [ ] **Asterisk**: Version 18+ with ARI and AudioSocket modules
- [ ] **Network**: Ports 8088, 8090, 15000, 18080 accessible
- [ ] **Storage**: 20GB+ available, SSD recommended

### API Keys and Credentials

- [ ] **OpenAI API Key**: For OpenAI Realtime or Local Hybrid configurations
- [ ] **Deepgram API Key**: For Deepgram Voice Agent configuration
- [ ] **Asterisk ARI Credentials**: Non-readonly user configured
- [ ] **SSH Keys**: For secure server access
- [ ] **Backup Credentials**: For automated backups

### Configuration Files

- [ ] **`.env` file**: All required environment variables set
- [ ] **`config/ai-agent.yaml`**: Production baseline configuration
- [ ] **`config/ai-agent.local.yaml`**: Operator overrides (created by Admin UI / CLI wizard; git-ignored)
- [ ] **Shared Storage**: `./asterisk_media/ai-generated` configured (mounted into `ai_engine` as `/mnt/asterisk_media/ai-generated`) (for Local Hybrid)
- [ ] **Call History**: `./data` volume persisted (default DB: `./data/call_history.db`)
- [ ] **Monitoring (optional)**: Prometheus scraping `/metrics` (aggregate metrics only)

### Testing Completed

- [ ] **Test Calls**: Successful test calls with chosen configuration
- [ ] **Load Testing**: Verified capacity with expected concurrent calls
- [ ] **Failover Testing**: Tested restart and recovery procedures
- [ ] **Call History**: Records persisted and export works (Admin UI → Call History)
- [ ] **Monitoring (optional)**: Prometheus and Grafana collecting aggregate metrics

---

## Deployment Architectures

### Architecture 1: Single-Server Deployment

**Best for**: Small to medium deployments (5-50 concurrent calls)

```
┌────────────────────────────────────────┐
│  Single Server (8 cores, 16GB RAM)    │
│                                        │
│  ┌──────────┐    ┌─────────────────┐ │
│  │ Asterisk │────│   ai_engine     │ │
│  │  +       │    │                 │ │
│  │ FreePBX  │    │  (Docker)       │ │
│  └──────────┘    └─────────────────┘ │
│                   ┌─────────────────┐ │
│                   │ local_ai_server │ │
│                   │  (Optional)     │ │
│                   └─────────────────┘ │
└────────────────────────────────────────┘
```

**Advantages**:
- Simple to deploy and manage
- Low operational overhead
- Cost-effective

**Limitations**:
- Single point of failure
- Limited scaling capacity

**Recommended For**: OpenAI Realtime, Deepgram Voice Agent

---

### Architecture 2: Separated Application and Database

**Best for**: Medium deployments (50-200 concurrent calls)

```
┌─────────────────┐      ┌─────────────────┐
│ Asterisk Server │──────│  AI Engine      │
│   (FreePBX)     │ ARI  │   Server        │
│                 │      │                 │
│  8 cores        │      │  ┌──────────┐   │
│  16GB RAM       │      │  │ai_engine │   │
│                 │      │  └──────────┘   │
└─────────────────┘      │  ┌──────────┐   │
                         │  │local-ai  │   │
                         │  │ -server  │   │
                         │  └──────────┘   │
                         │  16 cores        │
                         │  32GB RAM        │
                         └─────────────────┘
```

**Advantages**:
- Separates telephony from AI processing
- Independent scaling of components
- Easier troubleshooting

**Limitations**:
- Requires network between servers
- Slightly more complex configuration

**Recommended For**: Local Hybrid with high call volumes

---

### Architecture 3: Load-Balanced Cluster

**Best for**: Large deployments (200+ concurrent calls, high availability)

```
                    ┌──────────────┐
                    │ Load Balancer│
                    │  (HAProxy)   │
                    └───────┬──────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
   ┌────▼────┐         ┌────▼────┐        ┌────▼────┐
   │ai_engine│         │ai_engine│        │ai_engine│
   │ node 1  │         │ node 2  │        │ node 3  │
   └────┬────┘         └────┬────┘        └────┬────┘
        │                   │                   │
        └───────────────────┼───────────────────┘
                            │
                  ┌─────────▼──────────┐
                  │  Shared Storage    │
                  │  (NFS / Ceph)      │
                  └────────────────────┘
```

**Advantages**:
- High availability
- Horizontal scaling
- No single point of failure

**Limitations**:
- Complex setup and management
- Requires shared storage (NFS, Ceph, etc.)
- Higher operational costs

**Recommended For**: Enterprise deployments, 24/7 operations

---

## Security Hardening

### 1. Environment Variables Protection

**Never commit `.env` to git**:
```bash
# Verify .env is gitignored
cat .gitignore | grep "^\.env$"

# Check for accidental commits
git log --all --full-history --source --oneline -- .env

# If found, remove from history
git filter-branch --force --index-filter \
  "git rm --cached --ignore-unmatch .env" \
  --prune-empty --tag-name-filter cat -- --all
```

**Restrict file permissions**:
```bash
# .env should be readable only by owner
chmod 600 .env

# Verify
ls -l .env
# Expected: -rw------- 1 root root
```

---

### 2. API Key Rotation

**Establish rotation schedule**:
- OpenAI: Rotate every 90 days
- Deepgram: Rotate every 90 days
- Asterisk ARI: Rotate every 180 days

**Rotation procedure**:
```bash
# 1. Generate new key in provider dashboard
# 2. Test with new key in non-production environment
# 3. Update .env file
vi .env  # Update OPENAI_API_KEY or DEEPGRAM_API_KEY

# 4. Restart `ai_engine`
docker compose restart ai_engine

# 5. Verify with test call
# 6. Revoke old key in provider dashboard after 24 hours
```

---

### 3. Audio Transport Configuration

When using **ExternalMedia + RTP** transport (primarily for modular pipelines like `local_hybrid`), keep these settings in mind:

**Configuration in `config/ai-agent.yaml`:**
```yaml
external_media:
  rtp_host: "0.0.0.0"        # Bind inside container
  rtp_port: 18080            # Fixed port for simplicity
  port_range: "18080:18099"  # Optional range for per-call allocation
  codec: "ulaw"              # ulaw (8k) or slin16 (16k)
  direction: "both"          # sendrecv | sendonly | recvonly
  jitter_buffer_ms: 20       # Target frame size
```

**Key Considerations:**

1. **Port Accessibility**: UDP port 18080 must be accessible from Asterisk to `ai_engine`
   ```bash
   # Verify port is listening
   netstat -tuln | grep 18080
   ```

2. **Firewall Rules**: Ensure UDP traffic allowed
   ```bash
   ufw allow from <asterisk-ip> to any port 18080 proto udp
   ```

3. **Codec Alignment**: Match codec with Asterisk configuration
   - `ulaw` (G.711 μ-law): 8kHz, telephony standard
   - `slin16`: 16kHz, higher quality (if supported by provider)

4. **Network Latency**: RTP is sensitive to network jitter
   - Same host: <1ms latency ideal
   - Different hosts: <10ms recommended
   - Monitor network quality with `ping` and `mtr`

5. **Provider Compatibility**:
   - **Local Hybrid / pipelines**: ExternalMedia RTP is a strong production choice for modular pipelines. File playback is the most robust option; streaming-first is supported with automatic fallback to file.
   - **Full agents (OpenAI Realtime / Deepgram / Google Live / ElevenLabs)**: AudioSocket + streaming playback is validated for low-latency, real-time UX.

**When to Use ExternalMedia:**
- Modular pipelines (local_hybrid, custom pipelines)
- File-based TTS playback required
- Legacy integration compatibility

**When to Use AudioSocket Instead:**
- Monolithic providers (OpenAI Realtime, Deepgram Agent)
- Real-time streaming preferred
- Lower latency requirements

**See Also**: `docs/Transport-Mode-Compatibility.md` for detailed transport matrix

---

### 4. Network Security

**Firewall Configuration**:
```bash
# Allow only necessary ports
ufw default deny incoming
ufw default allow outgoing

# Asterisk ARI (from `ai_engine` only)
ufw allow from <ai_engine-ip> to any port 8088 proto tcp

# AudioSocket (from Asterisk only)
ufw allow from <asterisk-ip> to any port 8090 proto tcp

# ExternalMedia RTP (from Asterisk, bidirectional)
ufw allow from <asterisk-ip> to any port 18080 proto udp

# Health/Metrics (from monitoring server only)
ufw allow from <monitoring-ip> to any port 15000 proto tcp

# SSH (from management network only)
ufw allow from <management-network> to any port 22 proto tcp

ufw enable
```

**TLS for ARI** (optional but recommended):
```bash
# Generate certificate
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes

# Configure Asterisk http.conf
[general]
enabled=yes
bindaddr=0.0.0.0
bindport=8088
tlsenable=yes
tlsbindaddr=0.0.0.0:8089
tlscertfile=/etc/asterisk/keys/cert.pem
tlsprivatekey=/etc/asterisk/keys/key.pem

# Update .env
ASTERISK_ARI_URL=https://asterisk-host:8089
```

---

### 3.1. Docker Networking Modes

**Understanding Network Modes**

Docker offers two primary networking modes with different security and performance trade-offs:

| Mode | Security | Performance | Use Case |
|------|----------|-------------|----------|
| **Host** (Default in `docker-compose.yml`) | ⚠️ Lower | Best | Telephony-first deployments, same-host Asterisk |
| **Bridge** (Optional) | ✅ Higher | Good | Hardened deployments with explicit port isolation |

**Default Configuration: Host Network** (Telephony-first)

The default `docker-compose.yml` uses host networking:

```yaml
# docker-compose.yml (default)
services:
  ai_engine:
    network_mode: host
```

**Benefits**:
- No port mapping required (simpler for Asterisk integrations)
- Lowest latency for RTP/telephony paths
- Fewer “Docker networking surprises” on PBX distros

**Trade-offs**:
- Reduced container isolation (shared host network namespace)
- Requires strict firewall rules and access controls
- Not recommended for multi-tenant environments

---

**Bridge Network Mode** (Opt-In / Advanced)

For deployments that require explicit port isolation and tighter security posture, use bridge networking (custom compose required):

```yaml
# Example (bridge mode)
services:
  ai_engine:
    ports:
      - "8090:8090"        # AudioSocket
      - "18080:18080/udp"  # ExternalMedia RTP
      - "15000:15000"      # Health
```

**When to Use**:
- Environments requiring explicit port isolation
- Multi-tenant hosts
- Strict firewall policy requirements

**Security Considerations**:
- Ensure only the required ports are exposed
- Restrict exposure to the Asterisk IP(s) only

**If Using Host Mode**:
```bash
# CRITICAL: Firewall rules required
sudo ufw default deny incoming
sudo ufw allow from 127.0.0.1  # Localhost only
sudo ufw allow from <asterisk-ip> to any port 8090
sudo ufw allow from <asterisk-ip> to any port 18080
```

---

**Binding to 0.0.0.0** (Explicit Network Access)

To allow remote Asterisk servers:

```bash
# .env file
EXTERNAL_MEDIA_RTP_HOST=0.0.0.0
EXTERNAL_MEDIA_ADVERTISE_HOST=<routable-ip>
AUDIOSOCKET_ADVERTISE_HOST=<routable-ip>
HEALTH_BIND_HOST=0.0.0.0
```

**Port Mapping** (Bridge mode):
```yaml
# docker-compose.yml
services:
  ai_engine:
    ports:
      - "8090:8090"    # AudioSocket
      - "18080:18080"  # RTP
      - "15000:15000"  # Health
```

**Security Checklist for 0.0.0.0**:
- [ ] Firewall configured (allow only Asterisk IP)
- [ ] Network segmentation in place
- [ ] Monitoring enabled for port access
- [ ] Regular security audits

---

**Recommended Deployment Patterns**

**Pattern 1: Same Host (Default)**
```
┌─────────────────────────────┐
│  Single Server              │
│  ┌────────┐   ┌──────────┐ │
│  │Asterisk├───┤ai_engine │ │
│  │        │   │ (host)   │ │
│  └────────┘   └──────────┘ │
│  Via: 127.0.0.1             │
└─────────────────────────────┘
```
- **Network**: Host (default)
- **Ports**: No port mapping required
- **Security**: Requires firewall discipline (see section 3.1)

**Pattern 2: Separate Hosts (Remote Asterisk)**
```
┌──────────────┐    Network    ┌──────────────┐
│ Asterisk     │◄─────────────►│  AI Engine   │
│ Server       │   Firewall    │  Server      │
│              │               │  (host)      │
└──────────────┘               └──────────────┘
```
- **Network**: Host
- **Ports**: Open only what you need (8090/TCP, ExternalMedia UDP ports, health/admin UI as appropriate)
- **Firewall**: Required

**Pattern 3: Hardened Port Isolation (Advanced)**
```
┌──────────────┐    Network    ┌──────────────┐
│ Asterisk     │◄─────────────►│  AI Engine   │
│ Server       │   Firewall    │  Server      │
│              │               │ (bridge)     │
└──────────────┘               └──────────────┘
```
- **Network**: Bridge (opt-in)
- **Ports**: Explicitly mapped (tightest port control)
- **Security**: Strongest isolation when configured correctly

---

### 4. Container Security

**Run as non-root user**:
```yaml
# In docker-compose.yml
services:
  ai_engine:
    user: "1000:1000"  # Non-root UID:GID
    security_opt:
      - no-new-privileges:true
```

**Limit resources**:
```yaml
services:
  ai_engine:
    deploy:
      resources:
        limits:
          cpus: '8'
          memory: 16G
        reservations:
          cpus: '4'
          memory: 8G
```

**Read-only filesystem** (where possible):
```yaml
services:
  ai_engine:
    read_only: true
    tmpfs:
      - /tmp
      - /var/run
```

---

### 5. Secrets Management

**Recommended Approach: .env File** (Simple & Effective)

All secrets are managed through the `.env` file for simplicity and ease of adoption:

```bash
# .env file (gitignored by default)
ASTERISK_HOST=127.0.0.1
ASTERISK_ARI_USERNAME=AIAgent
ASTERISK_ARI_PASSWORD=your_secure_random_password_here

OPENAI_API_KEY=sk-proj-...
DEEPGRAM_API_KEY=...

LOG_LEVEL=info
HEALTH_BIND_HOST=127.0.0.1
```

**Security Best Practices for .env**:

1. **Never commit to git**: Already in `.gitignore`
2. **File permissions**: `chmod 600 .env` (owner read/write only)
3. **Separate per environment**: `.env.dev`, `.env.staging`, `.env.prod`
4. **Rotate regularly**: API keys every 90 days
5. **Use strong passwords**: Minimum 16 characters, random
6. **Backup securely**: Encrypted backup location

**Advanced Options** (For future consideration):

If you need enterprise-grade secrets management:
- **Docker Secrets** (Swarm mode): For orchestrated deployments
- **Hashicorp Vault**: Enterprise-grade secrets management
- **AWS Secrets Manager**: For AWS deployments
- **Azure Key Vault**: For Azure deployments
- **Google Secret Manager**: For GCP deployments

*Note: External secrets managers can be integrated in the future if requirements evolve. The .env approach provides a solid foundation for most deployments.*

---

## Backup and Disaster Recovery

### What to Backup

**Configuration Files** (critical):
```
.env
config/ai-agent.yaml
config/ai-agent.local.yaml   # operator overrides (if exists)
docker-compose.yml
```

**Monitoring Data** (optional):
If you run Prometheus/Grafana, back up their persistent storage per your monitoring stack.

**Call History** (important for debugging):
```
./data/call_history.db (and SQLite WAL files)
```

**Generated Audio Files** (optional):
```
./asterisk_media/ai-generated/  # default host path (mounted as /mnt/asterisk_media/ai-generated in `ai_engine`)
```

**Logs** (for compliance):
```
Docker container logs
System logs
```

---

### Backup Strategies

#### Strategy 1: Simple File Backup

```bash
#!/bin/bash
# backup.sh - Simple backup script

BACKUP_DIR="/backups/ai-voice-agent"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="$BACKUP_DIR/backup_$DATE"

mkdir -p "$BACKUP_PATH"

# Backup configuration
cp .env "$BACKUP_PATH/"
cp config/ai-agent.yaml "$BACKUP_PATH/"
cp config/ai-agent.local.yaml "$BACKUP_PATH/" 2>/dev/null || true
cp docker-compose*.yml "$BACKUP_PATH/"

# Backup Prometheus data
# Backup Call History DB (SQLite uses WAL by default; include -wal/-shm if present)
cp -a ./data/call_history.db* "$BACKUP_PATH/" 2>/dev/null || true

# Monitoring backup (optional): back up Prometheus/Grafana per your monitoring stack.

echo "Backup completed: $BACKUP_PATH"

# Keep only last 7 days
find "$BACKUP_DIR" -type d -mtime +7 -exec rm -rf {} +
```

**Schedule with cron**:
```bash
# Daily backup at 2 AM
0 2 * * * /root/Asterisk-AI-Voice-Agent/backup.sh >> /var/log/ai-voice-backup.log 2>&1
```

#### Strategy 2: Remote Backup

```bash
#!/bin/bash
# remote-backup.sh - Backup to remote server

REMOTE_HOST="backup-server.example.com"
REMOTE_USER="backup"
REMOTE_PATH="/backups/ai-voice-agent"

# Run local backup first
./backup.sh

# Sync to remote server
rsync -avz --delete \
  /backups/ai-voice-agent/ \
  "$REMOTE_USER@$REMOTE_HOST:$REMOTE_PATH/"
```

#### Strategy 3: Cloud Backup (S3)

```bash
#!/bin/bash
# s3-backup.sh - Backup to AWS S3

BUCKET="s3://my-company-backups/ai-voice-agent"

# Run local backup
./backup.sh

# Upload to S3
aws s3 sync /backups/ai-voice-agent/ "$BUCKET/" \
  --exclude "*" --include "backup_*/*" \
  --storage-class STANDARD_IA
```

---

### Disaster Recovery Procedure

**Scenario: Complete server failure**

**Recovery Time Objective (RTO)**: 1-2 hours
**Recovery Point Objective (RPO)**: 24 hours (daily backups)

**Steps**:

1. **Provision new server** with same specifications
2. **Install prerequisites**:
   ```bash
   # Install Docker
   curl -fsSL https://get.docker.com | sh
   
   # Install Docker Compose
   apt-get install docker-compose-plugin
   ```

3. **Clone repository**:
   ```bash
   git clone https://github.com/hkjarral/Asterisk-AI-Voice-Agent.git
   cd Asterisk-AI-Voice-Agent
   ```

4. **Restore configuration**:
   ```bash
   LATEST_BACKUP="/backups/ai-voice-agent/backup_20251029_020000"
   cp "$LATEST_BACKUP/.env" .
   cp "$LATEST_BACKUP/config/ai-agent.yaml" config/
   cp "$LATEST_BACKUP/config/ai-agent.local.yaml" config/ 2>/dev/null || true
   ```

5. **Start services**:
   ```bash
   docker compose up -d
   ```

6. **Restore monitoring data** (optional): restore Prometheus/Grafana per your monitoring stack.

7. **Verify operation**:
   ```bash
   curl http://localhost:15000/health
   # Make test call
   ```

8. **Update DNS/Load Balancer** to point to new server

---

## Monitoring and Alerting

### Essential Metrics to Monitor

**System Health**:
- CPU usage > 80% for 5 minutes
- Memory usage > 90%
- Disk usage > 80%
- Docker container restarts

**Application Health**:
- `ai_engine` health endpoint down
- No active calls for > 1 hour during business hours
- Turn response latency p95 > 2s
- Audio underflow rate > 2 per call

**Provider Health**:
- API error rate > 5%
- API latency > 1s
- API quota approaching limit

### Alert Destinations

**Critical Alerts** (immediate response):
- PagerDuty
- SMS
- Phone call

**Warning Alerts** (review within hours):
- Email
- Slack
- Microsoft Teams

**Configuration Example** (Alertmanager):
```yaml
route:
  group_by: ['alertname', 'cluster']
  receiver: 'team-pager'
  routes:
    - match:
        severity: critical
      receiver: team-pager
      
    - match:
        severity: warning
      receiver: team-slack

receivers:
  - name: 'team-pager'
    pagerduty_configs:
      - service_key: '<key>'
        
  - name: 'team-slack'
    slack_configs:
      - api_url: '<webhook>'
        channel: '#ai-voice-alerts'
```

---

## Operational Procedures

### Daily Operations

**Morning Checklist** (5 minutes):
- [ ] Check Grafana system overview dashboard
- [ ] Review overnight alerts
- [ ] Verify all containers running: `docker ps`
- [ ] Check disk space: `df -h`
- [ ] Review error logs: `docker logs ai_engine --since 24h | grep -i error`

**Health Check Script**:
```bash
#!/bin/bash
# health-check.sh - Daily health verification

echo "=== AI Voice Agent Health Check ==="
echo "Date: $(date)"
echo

# Container status
echo "Container Status:"
docker ps --format "table {{.Names}}\t{{.Status}}" | grep -E "ai_engine|local_ai_server|admin_ui"
echo

# Health endpoint
echo "Health Endpoint:"
curl -s http://localhost:15000/health | jq .
echo

# Active calls
echo "Active Calls:"
curl -s http://localhost:9090/api/v1/query?query=ai_agent_active_calls | \
  jq -r '.data.result[0].value[1] // "0"'
echo

# Last 24h call count
echo "Calls (last 24h):"
curl -s 'http://localhost:9090/api/v1/query?query=increase(ai_agent_calls_completed_total[24h])' | \
  jq -r '.data.result[0].value[1] // "0"'
echo

# Disk usage
echo "Disk Usage:"
df -h . ./asterisk_media
echo

echo "=== End Health Check ==="
```

---

### Upgrade Procedures

**Minor Version Upgrade** (e.g., v6.1.0 → v6.1.1):

```bash
# 1. Backup current state
./backup.sh

# 2. Pull latest code
git fetch origin
git checkout v6.1.1  # Or: git pull origin main

# 3. Compare configuration changes
git diff v6.1.0..v6.1.1 config/ai-agent.example.yaml

# 4. Update if needed
# Review and update config/ai-agent.yaml

# 5. Rebuild and restart
docker compose build --no-cache ai_engine
docker compose up -d --force-recreate ai_engine

# 6. Monitor logs for 5 minutes
docker compose logs -f ai_engine

# 7. Make test call

# 8. Rollback if issues
# git checkout v6.1.0
# docker compose up -d --force-recreate ai_engine
```

**Major Version Upgrade** (e.g., v5.x → v6.x):

1. **Review CHANGELOG.md** for breaking changes
2. **Test in staging environment** first
3. **Schedule maintenance window**
4. **Follow upgrade guide** in release notes
5. **Monitor closely** for first 24 hours

---

### Log Management

**Log Rotation** (prevent disk fill):
```json
// /etc/docker/daemon.json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
```

**Centralized Logging** (optional):
```yaml
# docker-compose.yml
services:
  ai_engine:
    logging:
      driver: "syslog"
      options:
        syslog-address: "tcp://log-server:514"
        tag: "ai_engine"
```

**Log Analysis**:
```bash
# Find errors in last hour
docker logs ai_engine --since 1h 2>&1 | grep -i error

# Count warnings
docker logs ai_engine --since 24h 2>&1 | grep -i warning | wc -l

# Export logs for analysis
docker logs ai_engine --since 24h > /tmp/ai_engine.log
```

---

## Compliance and Auditing

### HIPAA Compliance (for healthcare)

If handling Protected Health Information (PHI):

1. **Use Local Hybrid Configuration**:
   - Audio stays on-premises (STT/TTS local)
   - Only text transcripts sent to cloud LLM
   - No PHI in audio sent to cloud

2. **Enable Audit Logging**:
   ```yaml
   # In config/ai-agent.yaml
   logging:
     audit_enabled: true
     audit_log_path: /var/log/ai-voice-audit.log
   ```

3. **Encrypt Data at Rest**:
   ```bash
   # Encrypt audio storage
   cryptsetup luksFormat /dev/sdb
   cryptsetup open /dev/sdb ai_encrypted
   mkfs.ext4 /dev/mapper/ai_encrypted
   mount /dev/mapper/ai_encrypted /mnt/asterisk_media
   ```

4. **Sign Business Associate Agreement** with cloud providers (OpenAI, Deepgram)

5. **Regular Security Audits**

### GDPR Compliance (for EU users)

1. **Data Minimization**: Configure to collect only necessary data
2. **Right to Deletion**: Implement audio file retention policies
3. **Data Portability**: Provide call transcript exports
4. **Consent Management**: Record consent before call processing

**Audio Retention Policy**:
```bash
# Delete audio files older than 30 days
find ./asterisk_media/ai-generated -type f -mtime +30 -delete

# Schedule with cron (daily at 3 AM)
0 3 * * * find ./asterisk_media/ai-generated -type f -mtime +30 -delete
```

---

## Troubleshooting

### Container Won't Start

```bash
# Check logs
docker logs ai_engine

# Common issues:
# 1. Port already in use
sudo lsof -i :15000

# 2. Invalid configuration
docker compose run --rm ai_engine python -c "import yaml; yaml.safe_load(open('config/ai-agent.yaml'))"

# 3. Missing .env file
ls -l .env
```

### High CPU Usage

```bash
# Identify cause
docker stats ai_engine

# Check concurrent calls
curl http://localhost:15000/health | jq '.active_calls'

# If CPU high with low calls, check for:
# - Inefficient configuration
# - Memory leak (check memory trend)
# - External factor (provider slow response)
```

### Metrics Not Collecting

```bash
# Check Prometheus scraping
curl http://localhost:9090/api/v1/targets

# Check metrics endpoint
curl http://localhost:15000/metrics

# Check Prometheus logs
docker logs prometheus | tail -50
```

---

## Performance Tuning

### Optimize for Low Latency

```yaml
# config/ai-agent.yaml
streaming:
  jitter_buffer_ms: 60      # Reduce from 100ms
  min_start_ms: 200          # Reduce from 300ms
  low_watermark_ms: 100      # Reduce from 200ms

barge_in:
  min_ms: 250                # Responsive interrupts
  energy_threshold: 1500     # Tune per environment
```

### Optimize for High Concurrency

```yaml
# docker-compose.yml
services:
  ai_engine:
    deploy:
      resources:
        limits:
          cpus: '16'
          memory: 32G
```

```bash
# Increase file descriptors
ulimit -n 65536

# In /etc/security/limits.conf
* soft nofile 65536
* hard nofile 65536
```

---

## Migration from Existing System

### From Legacy Versions (v3.x / v4.x / v5.x)

1. **Review Breaking Changes**: Check CHANGELOG.md and [Migration Guide](MIGRATION.md)
2. **Update Configuration Format**: v6.x uses a three-file config layout (`ai-agent.yaml`, `ai-agent.local.yaml`, `.env`)
3. **Test Golden Baselines**: Try recommended configurations first
4. **Migrate Gradually**: Test in a staging environment before switching production

### From Other AI Voice Systems

1. **Provider Migration**:
   - Export conversation transcripts
   - Review API usage patterns
   - Test with small traffic percentage

2. **Audio Quality Validation**:
   - Run parallel test calls
   - Compare MOS scores
   - Validate codec compatibility

---

## Cost Optimization

### Cloud Configuration Costs

**OpenAI Realtime**: ~$0.06/minute
- Optimize: Use shorter conversations, clearer prompts

**Deepgram Voice Agent**: ~$0.03/minute
- Optimize: Batch processing where possible

**Local Hybrid**: ~$0.002/minute (LLM only)
- Optimize: Use gpt-4o-mini instead of gpt-4o

### Infrastructure Costs

- **Use reserved instances** for predictable workloads (30-50% savings)
- **Auto-scaling** for variable workloads
- **Spot instances** for non-critical workloads (up to 90% savings)

---

## Support and Escalation

### Internal Support Levels

**L1 - Basic Issues** (Ops Team):
- Container restarts
- Health check failures
- Standard alerts

**L2 - Application Issues** (Dev Team):
- Call quality problems
- Configuration errors
- Performance degradation

**L3 - Architecture Issues** (Architect):
- Scaling decisions
- Major outages
- Design changes

### External Support

- **GitHub Issues**: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/issues
- **Community Forum**: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/discussions
- **Documentation**: [docs/README.md](README.md)

---

## Checklist: Go-Live Readiness

### Infrastructure
- [ ] Production server provisioned and hardened
- [ ] Firewall rules configured
- [ ] DNS/Load balancer configured
- [ ] SSL certificates installed (if applicable)
- [ ] Backups configured and tested
- [ ] Monitoring deployed and verified

### Application
- [ ] Golden baseline configuration selected and tested
- [ ] API keys configured and validated
- [ ] Test calls successful with production config
- [ ] Load testing completed
- [ ] Logs verified and accessible
- [ ] Health checks passing

### Operations
- [ ] Runbooks created for common scenarios
- [ ] On-call rotation established
- [ ] Escalation procedures documented
- [ ] Backup/restore tested successfully
- [ ] Alert destinations configured
- [ ] Team trained on operations

### Compliance (if applicable)
- [ ] HIPAA/GDPR requirements reviewed
- [ ] Business Associate Agreements signed
- [ ] Audit logging enabled
- [ ] Data retention policies implemented
- [ ] Security audit completed

---

## Next Steps

After successful deployment:

1. **Monitor Closely**: First 48 hours are critical
2. **Tune Alerts**: Adjust thresholds based on actual performance
3. **Document Incidents**: Track issues and resolutions
4. **Regular Reviews**: Weekly performance reviews for first month
5. **Optimize**: Tune configuration based on real usage patterns

---

## Related Documentation

- [HARDWARE_REQUIREMENTS.md](HARDWARE_REQUIREMENTS.md) - Sizing and capacity planning
- [MONITORING_GUIDE.md](MONITORING_GUIDE.md) - Observability and alerting
- [FreePBX-Integration-Guide.md](FreePBX-Integration-Guide.md) - Asterisk integration
- [Configuration-Reference.md](Configuration-Reference.md) - Configuration options
- [CHANGELOG.md](../CHANGELOG.md) - Version history and breaking changes
