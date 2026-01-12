# Admin UI Setup Guide

**Version**: 1.0.0 (Introduced in Release 4.4.1; applies to v4.6.0+)  
**Release Date**: November 30, 2025  
**Quick Start**: 5 minutes to get running

---

## 📋 Overview

The Admin UI provides a modern web interface for managing your Asterisk AI Voice Agent. It replaces the command-line setup wizard and makes configuration changes simple and visual.

### What You Get
- 🧙 **Setup Wizard** - Quick first-time configuration
- ⚙️ **Configuration Management** - Visual editors for providers, pipelines, contexts
- 📊 **System Dashboard** - Monitor CPU, memory, and container status
- 🔍 **Live Logs** - Stream logs from ai_engine in real-time
- 📝 **YAML Editor** - Direct config editing with syntax validation

---

## 🚀 Quick Start (Docker)

**Recommended for most users**

### 1. Start the Admin UI

```bash
# From your project root (runs in background)
docker compose up -d --build admin_ui

# View logs if needed
docker compose logs -f admin_ui
```

That's it! The container will:
- ✅ Build the frontend automatically
- ✅ Set up the backend
- ✅ Create a default admin user
- ✅ Mount your config files

> **Note**: Run `./preflight.sh --apply-fixes` first (recommended) to create `.env` and generate a secure `JWT_SECRET`.
> The Admin UI reads/writes `config/ai-agent.yaml` and `.env`.

For first-call onboarding (dialplan + transport selection + verification), see:
- [Installation Guide](../docs/INSTALLATION.md)
- [Transport Compatibility](../docs/Transport-Mode-Compatibility.md)

### 2. Access the UI

Open your browser to:
```
http://localhost:3003
```

**Login Credentials**:
- Username: `admin`
- Password: `admin`

⚠️ **Important**: Change this password immediately after login!  
Go to: User Menu (top right) → Change Password

### 3. Complete Setup (First Time Only)

If this is a new installation, you'll see the Setup Wizard:

1. **Choose Provider** - Select from 5 options:
   - **Google Gemini Live** - Real-time bidirectional streaming (recommended)
   - **OpenAI Realtime** - Low-latency voice interactions
   - **Deepgram Voice Agent** - Enterprise-grade with 'Think' stage
   - **Local Hybrid** - Privacy-focused (audio local, LLM in cloud)
   - **Local (Full)** - 100% on-premises, no API keys required
2. **Enter API Keys** - Keys will be validated before proceeding (not required for Local Full)
3. **Configure Asterisk** - Enter your Asterisk ARI connection details
4. **Set AI Personality** - Choose greeting and assistant name
5. **Complete** - Configuration is saved to `config/ai-agent.yaml` and `.env`

If you already have a working configuration, the wizard will be skipped automatically.

---

## 🔧 Standalone Deployment (No Docker)

**For custom setups or testing without Docker**

### Prerequisites

- Node.js 18+ and npm
- Python 3.10+
- Git

### Installation Steps

#### 1. Build the Frontend

```bash
cd admin_ui/frontend
npm install
npm run build
```

This creates a `dist/` folder with the compiled React app.

#### 2. Copy to Backend

```bash
# From admin_ui/frontend
cp -r dist/* ../backend/static/
```

#### 3. Install Python Dependencies

```bash
cd ../backend
pip install -r requirements.txt
```

#### 4. Set Environment Variables

```bash
# Required: Project root path
export PROJECT_ROOT=/path/to/Asterisk-AI-Voice-Agent

# Optional: JWT secret (see Security section)
# export JWT_SECRET=$(openssl rand -hex 32)
```

#### 5. Start the Server

**Development Mode** (runs in foreground):
```bash
python main.py
```

**Production Mode** (daemon with nohup):
```bash
nohup python main.py > admin_ui.log 2>&1 &
echo $! > admin_ui.pid
```

To stop the daemon:
```bash
kill $(cat admin_ui.pid)
```

**Alternative** (using screen/tmux):
```bash
screen -dmS admin-ui python main.py
# Attach: screen -r admin-ui
# Detach: Ctrl+A, D
```

**Systemd Service** (recommended for production):

Create `/etc/systemd/system/admin-ui.service`:

```ini
[Unit]
Description=Admin UI for Asterisk AI Voice Agent
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/Asterisk-AI-Voice-Agent/admin_ui/backend
Environment="PROJECT_ROOT=/path/to/Asterisk-AI-Voice-Agent"
Environment="JWT_SECRET=your_random_secret_here"
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable admin-ui
sudo systemctl start admin-ui

# Check status
sudo systemctl status admin-ui

# View logs
sudo journalctl -u admin-ui -f
```

#### 6. Access the UI

Open your browser to:
```
http://localhost:3003
```

Login with `admin` / `admin` and change the password.

---

## 🔐 Security Configuration

### Default Credentials

**Username**: `admin`  
**Password**: `admin`

These credentials are created automatically on first run. The user data is stored in:
```
config/users.json
```

**⚠️ CRITICAL**: Change the default password immediately after first login!

### JWT Secret (Optional)

By default, the system uses a development JWT secret. This is fine for:
- ✅ Local development
- ✅ Internal networks
- ✅ Testing environments

**You should set a custom JWT secret if**:
- You're deploying to production
- The UI is accessible from the internet
- You need enhanced security

**To set a custom JWT secret**:

```bash
# Generate a secure random secret
openssl rand -hex 32

# Add to .env file
echo "JWT_SECRET=your_generated_secret_here" >> .env

# Restart admin-ui
docker compose restart admin_ui
# OR for standalone:
kill $(cat admin_ui.pid) && nohup python main.py > admin_ui.log 2>&1 &
```

**Note**: Changing the JWT secret will log out all users.

### Password Management

**To change your password**:
1. Login to the Admin UI
2. Click your username (top right corner)
3. Select "Change Password"
4. Enter old and new password
5. Click "Update Password"

**If you forget your password**:
```bash
# Delete the users file (resets to admin/admin)
rm config/users.json

# Restart admin-ui
docker compose restart admin_ui
```

---

## 🌐 Production Deployment

### HTTPS with Reverse Proxy

For production deployments, use a reverse proxy (Nginx or Traefik) to add HTTPS.

#### Option 1: Nginx

Create `/etc/nginx/sites-available/admin-ui`:

```nginx
server {
    listen 443 ssl http2;
    server_name admin.yourdomain.com;

    ssl_certificate /etc/ssl/certs/your_cert.crt;
    ssl_certificate_key /etc/ssl/private/your_key.key;

    location / {
        proxy_pass http://localhost:3003;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket support for live logs
    location /api/logs {
        proxy_pass http://localhost:3003;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

Enable and restart:
```bash
sudo ln -s /etc/nginx/sites-available/admin-ui /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

#### Option 2: Traefik (Docker)

Add labels to `docker-compose.yml`:

```yaml
services:
  admin-ui:
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.admin-ui.rule=Host(`admin.yourdomain.com`)"
      - "traefik.http.routers.admin-ui.entrypoints=websecure"
      - "traefik.http.routers.admin-ui.tls.certresolver=letsencrypt"
```

### Firewall Configuration

**For Docker Deployment**:
```bash
# Allow port 3003 only from trusted IPs
sudo ufw allow from 192.168.1.0/24 to any port 3003
```

**For Production with Reverse Proxy**:
```bash
# Block direct access to 3003, only allow Nginx
sudo ufw deny 3003
sudo ufw allow 443  # HTTPS through Nginx
```

### CORS Configuration (Production)

For production, restrict CORS to specific domains.

Edit `admin_ui/backend/main.py` line 14:

```python
# Change from:
allow_origins=["*"]

# To:
allow_origins=[
    "https://admin.yourdomain.com",
    "https://yourdomain.com"
]
```

---

## 🔄 Upgrading from CLI Setup

If you've been using `install.sh` and the Agent CLI, the Admin UI works alongside your existing setup.

### Migration Steps

1. **Backup your configuration** (recommended):
   ```bash
   cp config/ai-agent.yaml config/ai-agent.yaml.backup
   cp .env .env.backup
   ```

2. **Start the Admin UI**:
   ```bash
   docker compose up -d admin_ui
   ```

3. **Access the UI**:
   - The wizard will be skipped (config already exists)
   - Your existing configuration is loaded automatically

4. **Verify configuration**:
   - Check Providers page matches your setup
   - Check Contexts page
   - Review any warnings

### CLI Tools Still Work

The recommended v5.0 CLI commands are:
```bash
agent setup
agent check
agent rca
```

Both CLI and UI read/write the same files:
- `config/ai-agent.yaml`
- `.env`
- `config/users.json`

**Recommendation**: Choose one primary method (UI or CLI) to avoid confusion.

---

## 📊 Using the Admin UI

### Dashboard

The main dashboard shows:
- **System Metrics**: CPU, memory, disk usage
- **Container Status**: ai_engine, local_ai_server, admin_ui
- **Quick Actions**: Restart containers, view logs

### Configuration Pages

**Providers**:
- Add/edit/delete AI service providers
- Test connections before saving
- Enable/disable providers
- Configure API keys and endpoints
- **Provider Types**:
  - **Full Agent** (OpenAI Realtime, Deepgram, Google Live, Local Full) - Handles STT+LLM+TTS together
  - **Modular** (local_stt, local_llm, local_tts, openai_llm) - Single capability for pipelines
- ⚠️ Unregistered provider types show a warning and cannot be used in pipelines

**Pipelines**:
- Create custom AI pipelines
- Select STT, LLM, and TTS providers
- Configure pipeline-specific options
- Enable tool calling

**Contexts**:
- Define AI personalities
- Set greetings and prompts
- Override providers per context
- Configure audio profiles
- Enable background music (see below)

#### Background Music Configuration

Enable ambient music during AI conversations. Music plays to the caller while they talk with the AI agent.

**How to Enable**:
1. Go to **Configuration → Contexts**
2. Edit a context (or create new)
3. Scroll to **Background Music** section
4. Toggle **Enable Background Music**
5. Enter MOH class name (default: `default`)
6. Save changes

**Setting Up Music On Hold in Asterisk**:

1. **Place audio files** in `/var/lib/asterisk/moh/<class-name>/`
   ```bash
   # Example: Create custom class
   mkdir -p /var/lib/asterisk/moh/ambient
   cp your-music.wav /var/lib/asterisk/moh/ambient/
   ```

2. **For FreePBX**: Go to **Settings → Music On Hold** to create categories

3. **Supported formats**: WAV, ulaw, alaw, sln, mp3

**Best Practices**:
- **Reduce volume to 15-20%** - Loud music interferes with conversation
- Use **ambient/instrumental** music without lyrics
- Music is heard by the AI (affects VAD) - low volume helps accuracy
- Test with a real call before production use

**YAML Configuration** (manual):
```yaml
contexts:
  my_context:
    greeting: "Hello!"
    background_music: "ambient"  # MOH class name
```

**Audio Profiles**:
- Edit encoding settings
- Configure sample rates
- Set provider preferences

### Advanced Settings

- **VAD**: Voice Activity Detection tuning
- **Streaming**: Audio chunk sizes and formats
- **LLM**: Temperature, tokens, and model parameters
- **Transport**: AudioSocket vs ExternalMedia
- **Barge-In**: Interruption handling

### System Management

**Environment Variables** (`.env`):
- Edit API keys
- Update Asterisk connection
- Modify logging levels

**Raw YAML Editor**:
- Direct edit of `config/ai-agent.yaml`
- Syntax validation
- Careful: Invalid YAML will break the system!

**Logs Viewer**:
- Live streaming from ai_engine
- Filter by container
- Download logs

---

## 🐛 Troubleshooting

### Cannot Access UI

**Check if container is running**:
```bash
docker ps | grep admin_ui
```

**Check logs**:
```bash
docker logs admin_ui
```

**Rebuild if needed**:
```bash
docker compose up -d --build admin_ui
```

### Login Not Working

**Try default credentials**:
- Username: `admin`
- Password: `admin`

**Reset to defaults**:
```bash
# Stop container
docker compose stop admin_ui

# Delete users file
rm config/users.json

# Start container (recreates default user)
docker compose start admin_ui
```

### 401 Unauthorized Errors

**Token expired** (tokens last 24 hours):
- Logout and login again
- Or clear browser cache

**JWT secret changed**:
- Everyone will be logged out
- Login again with credentials

### Configuration Not Saving

**Check file permissions**:
```bash
ls -la config/
# Files should be writable by Docker user
```

**Check volume mounts**:
```bash
docker inspect admin_ui | grep Mounts -A 10
```

**For standalone, check PROJECT_ROOT**:
```bash
echo $PROJECT_ROOT
# Should point to your project directory
```

### Container Won't Start

**View detailed logs**:
```bash
docker compose logs admin_ui
```

**Common issues**:
- Port 3003 already in use: Change port in `docker-compose.yml`
- Build failed: Check Node.js/npm versions
- Python errors: Check `requirements.txt` dependencies

**Force rebuild**:
```bash
docker compose down
docker compose up -d --build admin_ui
```

### Page Not Loading

**Check backend is running**:
```bash
# Docker:
docker exec admin_ui ps aux | grep python

# Standalone:
ps aux | grep main.py
```

**Check port is accessible**:
```bash
curl http://localhost:3003
# Should return HTML or 401
```

**Clear browser cache**:
- Hard refresh: Ctrl+F5 (Windows/Linux) or Cmd+Shift+R (Mac)
- Or try incognito/private window

---

## 🔍 Port Reference

### Default Ports

| Service | Port | Protocol | Access |
|---------|------|----------|--------|
| Admin UI | 3003 | HTTP | http://localhost:3003 |
| AI Engine Health/Metrics | 15000 | HTTP | Internal/Local |
| AI Engine AudioSocket | 8090 | TCP | Asterisk → ai-engine |
| AI Engine ExternalMedia RTP | 18080 | UDP | Bidirectional |
| Local AI Server | 8765 | WebSocket | Internal |
| Asterisk ARI | 8088 | HTTP/WebSocket | Internal |

### Changing the Admin UI Port

Edit `docker-compose.yml`:
```yaml
admin-ui:
  ports:
    - "YOUR_PORT:8000"  # Change YOUR_PORT to desired port
```

Then restart:
```bash
docker compose up -d admin_ui
```

---

## 📁 File Locations

### Configuration Files (Created by UI)

```
config/
├── ai-agent.yaml      # Main configuration (providers, pipelines, contexts)
├── users.json         # Admin UI user credentials
└── ai-agent.yaml.backup  # Auto-backup (optional)

.env                   # Environment variables (API keys, Asterisk connection)
```

### Admin UI Files

```
admin_ui/
├── frontend/          # React application
│   ├── src/          # Source code
│   └── dist/         # Built files (after npm run build)
├── backend/          # FastAPI server
│   ├── main.py       # Server entry point
│   ├── auth.py       # Authentication logic
│   ├── api/          # API endpoints
│   └── static/       # Frontend build (served by backend)
├── Dockerfile        # Multi-stage build
└── UI_Setup_Guide.md # This file
```

---

## 🆘 Getting Help

### Documentation

- **This Guide**: Setup and troubleshooting
- **Main README**: [../README.md](../README.md) - Project overview
- **CHANGELOG**: [../CHANGELOG.md](../CHANGELOG.md) - Version history

### Support Channels

**GitHub Issues**: [Report bugs or request features](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/issues)
- Use label: `admin-ui`
- Include: Version, deployment method, error logs

**Discord Community**: [Join discussion](https://discord.gg/CAVACtaY)
- #support channel for questions
- #admin-ui for UI-specific topics

### Logs to Include When Reporting Issues

```bash
# Docker deployment
docker logs admin_ui > admin_ui_logs.txt

# Standalone deployment
cat admin_ui.log

# Browser console (F12 → Console tab)
# Screenshot of any errors
```

---

## 🔄 Updates and Maintenance

### Updating the Admin UI

**Docker deployment**:
```bash
# Pull latest code
git pull origin develop

# Rebuild and restart
docker compose up -d --build admin_ui
```

**Standalone deployment**:
```bash
# Pull latest code
git pull origin develop

# Rebuild frontend
cd admin_ui/frontend
npm install
npm run build
cp -r dist/* ../backend/static/

# Restart backend
kill $(cat admin_ui.pid)
cd ../backend
nohup python main.py > admin_ui.log 2>&1 &
echo $! > admin_ui.pid
```

### Backup Recommendations

**Before making changes**:
```bash
# Backup configuration
cp config/ai-agent.yaml config/ai-agent.yaml.$(date +%Y%m%d)
cp .env .env.$(date +%Y%m%d)

# Backup user data
cp config/users.json config/users.json.$(date +%Y%m%d)
```

**Automated backup** (add to crontab):
```bash
# Daily backup at 2 AM
0 2 * * * cd /path/to/project && tar czf backups/config-$(date +\%Y\%m\%d).tar.gz config/ .env
```

---

## 🎯 Next Steps

### After Setup

1. ✅ **Change default password** (admin/admin → your password)
2. ✅ **Complete setup wizard** (if new installation)
3. ✅ **Test a phone call** to verify configuration
4. ✅ **Explore the dashboard** and familiarize yourself with the UI
5. ✅ **Set up HTTPS** (if deploying to production)

### Upcoming Features (v1.1)

Coming in future releases:
1. **YAML Diff Preview** - See changes before saving
2. **Log Filtering** - Filter logs by level and component
3. **Multi-User Support** - Role-based access control
4. **2FA Authentication** - Two-factor authentication

---

## 📝 Version Information

- **Admin UI Version**: 1.0.0
- **Project Version**: 4.6.0+
- **Release Date**: November 30, 2025
- **Release Branch**: `develop`
- **Guide Version**: 1.0

---

## ✅ Quick Reference

### Essential Commands

```bash
# Start Admin UI (Docker)
docker compose up -d admin_ui

# Stop Admin UI
docker compose stop admin_ui

# View logs
docker logs admin_ui -f

# Restart after changes
docker compose restart admin_ui

# Rebuild from scratch
docker compose up -d --build admin_ui

# Access UI
# Browse to: http://localhost:3003
```

### Emergency Recovery

```bash
# Reset to admin/admin
rm config/users.json && docker compose restart admin_ui

# Restore configuration
cp config/ai-agent.yaml.backup config/ai-agent.yaml
cp .env.backup .env
docker compose restart ai_engine

# Full restart
docker compose down
docker compose up -d
```

---

**Happy managing! 🚀**

For questions or issues, reach out via GitHub Issues or Discord.
