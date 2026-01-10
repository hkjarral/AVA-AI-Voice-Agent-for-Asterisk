# Cross-Platform Support Plan

## Executive Summary

This document outlines the plan to make Asterisk AI Voice Agent (AAVA) work seamlessly across multiple operating systems, Docker versions, and Asterisk distributions.

### Scope

| Requirement | Value |
|-------------|-------|
| **Architecture** | x86_64 only (AAVA Docker images are 64-bit; 32-bit hosts unsupported) |
| **Docker Engine** | ≥ 20.10 required (hard fail below); ≥ 25.x recommended |
| **Docker Compose** | v2 required (v1 hard fail); v2 < 2.20 shows warning |
| **OS Policy** | Warn on EOL distributions; support if Docker/Compose requirements met |

### Supported OS Families

| Family | Primary Distros | Derivatives (Also Supported) |
|--------|-----------------|------------------------------|
| **Debian** | Ubuntu, Debian | Linux Mint |
| **RHEL** | RHEL, CentOS Stream, Fedora | Rocky Linux, AlmaLinux, Sangoma |

> **Note**: Rocky, Alma, and Sangoma are RHEL derivatives and inherit RHEL support. They are explicitly tested due to Asterisk/FreePBX prevalence.

### Core Principle: UI-First, Zero-Error Experience

> **Goal**: User runs ONE command before `docker compose up` → Admin UI launches with zero errors

```bash
# The dream user experience:
git clone https://github.com/hkjarral/Asterisk-AI-Voice-Agent.git
cd Asterisk-AI-Voice-Agent
./preflight.sh          # ← NEW: Detects, fixes, prepares everything
docker compose up -d
# Open http://localhost:3003 → Clean UI, no errors, ready to configure
```

### Design Philosophy

1. **UI-First**: CLI exists for advanced users, but UI is the primary interface
2. **Pre-flight over Runtime**: Fix issues BEFORE containers start, not after
3. **Silent Success, Loud Failure**: Only show output when user action needed
4. **Idempotent**: Safe to run preflight multiple times
5. **Opt-in Fixes**: Never auto-modify system state without `--apply-fixes` flag

---

## Table of Contents

1. [Current State Analysis](#1-current-state-analysis)
2. [Target User Experience](#2-target-user-experience)
3. [Pre-flight Script Design](#3-pre-flight-script-design)
4. [Target Environments](#4-target-environments)
5. [Architecture](#5-architecture)
6. [Implementation Phases](#6-implementation-phases)
7. [Timeline & Milestones](#7-timeline--milestones)
8. [What We're NOT Doing](#8-what-were-not-doing-deferred)

---

## 1. Current State Analysis

### What Works Today

- Docker Compose deployment on Ubuntu/Debian
- Manual configuration via YAML files
- CLI tools (agent init, agent doctor, agent troubleshoot)
- Admin UI for configuration (limited)
- AudioSocket and ExternalMedia transports

### Current Gaps
- No OS detection or adaptation
- Docker version not validated
- Hardcoded paths assume Ubuntu/Debian
- No SELinux/AppArmor handling
- Limited FreePBX integration
- Permission issues require manual intervention
- No Podman support

### User Pain Points (from community feedback)
- "Calls drop immediately" → Permission/port issues
- "Docker commands need sudo" → User not in docker group
- "Media directory errors" → Path/permission mismatch
- "Works on Ubuntu, fails on CentOS" → Path differences

---

## 2. Target User Experience

### Current Flow (Problems)

```text
git clone ...
docker compose up -d admin_ui
# User opens UI and sees:
# ❌ "Media directory not found"
# ❌ "Permission denied"
# ❌ "Docker connection error"
# User is confused, searches docs, asks Discord
```

### New Flow (UI-First)

```bash
git clone https://github.com/hkjarral/Asterisk-AI-Voice-Agent.git
cd Asterisk-AI-Voice-Agent
./preflight.sh              # ← Single command handles everything
docker compose up -d
# User opens http://localhost:3003
# ✅ Clean UI, zero errors
# ✅ Setup wizard ready
# ✅ All systems green
```

### What `preflight.sh` Does

| Check | Default Behavior | With `--apply-fixes` |
|-------|------------------|----------------------|
| Docker installed? | Show install link | N/A (can't auto-install) |
| Docker running? | Show start command | N/A (won't auto-start) |
| Docker Compose v1/v2? | Hard fail on v1; warn if old v2 | N/A |
| User in docker group? | Show `usermod` command | N/A (requires logout) |
| Media directory exists? | Show `mkdir` command | Create directory |
| Correct permissions? | Show `chown` command | Fix permissions |
| SELinux blocking? | Show `semanage` command | Apply SELinux context |
| .env file exists? | Show `cp` command | Copy from .env.example |
| Asterisk config? | Auto-detect or prompt user | Save path to .env |
| Port 3003 available? | Warn if in use | N/A |

> **Note**: We never auto-start Docker or modify user groups—those require logout/reboot and are shown as manual steps.

### Exit Behavior

```text
# If everything passes:
✓ All checks passed!

Start the Admin UI:
  docker compose up -d

Then open: http://localhost:3003

# If user action needed:
⚠ Some issues need manual fixes:

  sudo usermod -aG docker $USER
  sudo mkdir -p /mnt/asterisk_media/ai-generated

After running these commands, run ./preflight.sh again.
```

---

## 3. Pre-flight Script Design

### 3.1 Script Location and Naming

```text
Asterisk-AI-Voice-Agent/
├── preflight.sh          # ← NEW: Main entry point (bash)
├── install.sh            # Existing: Calls preflight + docker compose
├── docker-compose.yml
└── ...
```

### 3.2 Script Flow

```text
┌─────────────────────────────────────────────────────────┐
│                    preflight.sh                          │
├─────────────────────────────────────────────────────────┤
│  1. Detect OS (read /etc/os-release)                    │
│  2. Check Docker installed + running                     │
│  3. Check Docker Compose version                         │
│  4. Check user permissions                               │
│  5. Create/verify directories                            │
│  6. Handle SELinux (if RHEL family)                     │
│  7. Create .env if missing                               │
│  8. Write .preflight-ok marker                           │
│  9. Print summary and next steps (never auto-start)      │
└─────────────────────────────────────────────────────────┘
```

### 3.3 Preflight Script Implementation

```bash
#!/bin/bash
# preflight.sh - Prepare system for AAVA Admin UI
# NOTE: No 'set -e' - we want to collect ALL issues before exiting

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_ok() { echo -e "${GREEN}✓${NC} $1"; }
log_warn() { echo -e "${YELLOW}⚠${NC} $1"; WARNINGS+=("$1"); }
log_fail() { echo -e "${RED}✗${NC} $1"; FAILURES+=("$1"); }

# State
WARNINGS=()
FAILURES=()
FIX_CMDS=()          # Commands that --apply-fixes will run
MANUAL_CMDS=()       # Commands user must run manually (e.g., reboot/logout)
APPLY_FIXES=false
DOCKER_ROOTLESS=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Parse args
for arg in "$@"; do
    case $arg in
        --apply-fixes) APPLY_FIXES=true ;;
        --help) 
            echo "Usage: ./preflight.sh [--apply-fixes]"
            echo "  --apply-fixes  Apply fixes automatically (requires sudo for some)"
            exit 0 
            ;;
    esac
done

# ----- OS Detection + EOL Enforcement -----
detect_os() {
    OS_ID="unknown"
    OS_VERSION="unknown"
    OS_FAMILY="unknown"
    
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS_ID="$ID"
        OS_VERSION="${VERSION_ID:-unknown}"
        case "$ID" in
            ubuntu|debian|linuxmint) OS_FAMILY="debian" ;;
            centos|rhel|rocky|almalinux|fedora) OS_FAMILY="rhel" ;;
            *) 
                if [ -f /etc/sangoma/pbx ] || [ -f /etc/freepbx.conf ]; then
                    OS_FAMILY="rhel"
                    OS_ID="sangoma"
                fi
                ;;
        esac
    fi
    
    # Check architecture (HARD FAIL)
    ARCH=$(uname -m)
    if [ "$ARCH" != "x86_64" ]; then
        log_fail "Unsupported architecture: $ARCH (x86_64 required - AAVA images are 64-bit only)"
    else
        log_ok "Architecture: $ARCH"
    fi
    
    # Check EOL status (WARNING only - we still support if Docker/Compose work)
    case "$OS_ID" in
        ubuntu)
            case "$OS_VERSION" in
                18.04) log_warn "Ubuntu 18.04 is EOL - consider upgrading to 22.04+" ;;
                20.04) log_warn "Ubuntu 20.04 standard support ends Apr 2025" ;;
            esac
            ;;
        debian)
            case "$OS_VERSION" in
                9) log_warn "Debian 9 is EOL - consider upgrading to 11+" ;;
                10) log_warn "Debian 10 LTS ended Jun 2024 - consider upgrading" ;;
            esac
            ;;
        centos)
            case "$OS_VERSION" in
                7) log_warn "CentOS 7 is EOL (Jun 2024) - consider migrating to Rocky/Alma" ;;
                8) log_warn "CentOS 8 is EOL (Dec 2021) - consider migrating to Rocky/Alma" ;;
            esac
            ;;
    esac
    
    log_ok "OS: $OS_ID $OS_VERSION ($OS_FAMILY family)"
}

# ----- Docker Checks -----
check_docker() {
    if ! command -v docker &>/dev/null; then
        log_fail "Docker not installed"
        echo "  Install: https://docs.docker.com/engine/install/"
        return 1
    fi
    
    # Detect rootless Docker BEFORE trying to access
    if [ -n "$DOCKER_HOST" ]; then
        DOCKER_ROOTLESS=true
    elif [ -n "$XDG_RUNTIME_DIR" ] && [ -S "$XDG_RUNTIME_DIR/docker.sock" ]; then
        DOCKER_ROOTLESS=true
        export DOCKER_HOST="unix://$XDG_RUNTIME_DIR/docker.sock"
    fi
    
    if ! docker ps &>/dev/null 2>&1; then
        # NOTE: We do NOT auto-start docker - that's a side effect
        # Instead, we tell the user what to do based on mode
        if [ "$DOCKER_ROOTLESS" = true ]; then
            log_warn "Rootless Docker not running"
            MANUAL_CMDS+=("systemctl --user start docker")
            # Rootless doesn't need usermod or sudo
        else
            # Check if it's a permission issue vs not running
            if docker info &>/dev/null 2>&1; then
                log_warn "Docker daemon not running"
                MANUAL_CMDS+=("sudo systemctl start docker")
            else
                log_warn "Cannot access Docker daemon (permission denied?)"
                MANUAL_CMDS+=("sudo systemctl start docker")
                MANUAL_CMDS+=("sudo usermod -aG docker \$USER")
                MANUAL_CMDS+=("# Then log out and back in, or run: newgrp docker")
            fi
        fi
        return 1
    fi
    
    # Version check (HARD FAIL below minimum)
    DOCKER_VERSION=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "0.0.0")
    DOCKER_MAJOR=$(echo "$DOCKER_VERSION" | cut -d. -f1)
    
    if [ "$DOCKER_MAJOR" -lt 20 ]; then
        log_fail "Docker $DOCKER_VERSION too old (minimum: 20.10) - upgrade required"
    elif [ "$DOCKER_MAJOR" -lt 25 ]; then
        log_warn "Docker $DOCKER_VERSION supported but upgrade to 25.x+ recommended"
    else
        log_ok "Docker: $DOCKER_VERSION"
    fi
    
    if [ "$DOCKER_ROOTLESS" = true ]; then
        log_ok "Docker mode: rootless"
    fi
}

# ----- Compose Detection -----
check_compose() {
    COMPOSE_CMD=""
    COMPOSE_VER=""
    
    if docker compose version &>/dev/null 2>&1; then
        COMPOSE_CMD="docker compose"
        COMPOSE_VER=$(docker compose version --short 2>/dev/null | sed 's/^v//')
    elif command -v docker-compose &>/dev/null; then
        COMPOSE_CMD="docker-compose"
        COMPOSE_VER=$(docker-compose version --short 2>/dev/null | sed 's/^v//')
        # Compose v1 is HARD FAIL
        log_fail "Docker Compose v1 detected - EOL July 2023, security risk"
        log_fail "  Upgrade required: https://docs.docker.com/compose/install/"
        return 1
    fi
    
    if [ -z "$COMPOSE_CMD" ]; then
        log_fail "Docker Compose not found"
        return 1
    fi
    
    # Parse version (e.g., "2.20.0" -> major=2, minor=20)
    COMPOSE_MAJOR=$(echo "$COMPOSE_VER" | cut -d. -f1)
    COMPOSE_MINOR=$(echo "$COMPOSE_VER" | cut -d. -f2)
    
    if [ "$COMPOSE_MAJOR" -eq 2 ] && [ "$COMPOSE_MINOR" -lt 20 ]; then
        log_warn "Compose $COMPOSE_VER - upgrade to 2.20+ recommended (missing profiles, watch)"
    else
        log_ok "Docker Compose: $COMPOSE_VER (v2 plugin)"
    fi
}

# ----- Directory Setup -----
check_directories() {
    MEDIA_DIR="${MEDIA_DIR:-/mnt/asterisk_media/ai-generated}"
    
    if [ -d "$MEDIA_DIR" ] && [ -w "$MEDIA_DIR" ]; then
        log_ok "Media directory: $MEDIA_DIR"
        return 0
    fi
    
    if [ ! -d "$MEDIA_DIR" ]; then
        log_warn "Media directory missing: $MEDIA_DIR"
    else
        log_warn "Media directory not writable: $MEDIA_DIR"
    fi
    
    # Rootless-aware fix commands
    if [ "$DOCKER_ROOTLESS" = true ]; then
        # For rootless, user can create in their home or use :Z for SELinux
        FIX_CMDS+=("mkdir -p $MEDIA_DIR")
        log_warn "  Rootless tip: Use volume with :Z suffix for SELinux compatibility"
    else
        FIX_CMDS+=("sudo mkdir -p $MEDIA_DIR")
        FIX_CMDS+=("sudo chown -R \$(id -u):\$(id -g) $MEDIA_DIR")
    fi
}

# ----- SELinux (RHEL family) -----
check_selinux() {
    [ "$OS_FAMILY" != "rhel" ] && return 0
    command -v getenforce &>/dev/null || return 0
    
    SELINUX_MODE=$(getenforce 2>/dev/null || echo "Disabled")
    
    if [ "$SELINUX_MODE" = "Enforcing" ]; then
        # Check if semanage is available
        if ! command -v semanage &>/dev/null; then
            log_warn "SELinux: Enforcing but semanage not installed"
            FIX_CMDS+=("sudo dnf install -y policycoreutils-python-utils")
        fi
        
        log_warn "SELinux: Enforcing (context fix needed for media directory)"
        FIX_CMDS+=("sudo semanage fcontext -a -t container_file_t '${MEDIA_DIR}(/.*)?'")
        FIX_CMDS+=("sudo restorecon -Rv ${MEDIA_DIR}")
    else
        log_ok "SELinux: $SELINUX_MODE"
    fi
}

# ----- Environment File -----
check_env() {
    if [ -f "$SCRIPT_DIR/.env" ]; then
        log_ok ".env file exists"
    elif [ -f "$SCRIPT_DIR/.env.example" ]; then
        # This is safe to auto-apply
        if [ "$APPLY_FIXES" = true ]; then
            cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
            log_ok "Created .env from .env.example"
        else
            log_warn ".env file missing"
            FIX_CMDS+=("cp $SCRIPT_DIR/.env.example $SCRIPT_DIR/.env")
        fi
        log_warn "Edit .env to add your API keys"
    else
        log_warn ".env.example not found"
    fi
}

# ----- Asterisk Detection -----
check_asterisk() {
    ASTERISK_DIR=""
    ASTERISK_FOUND=false
    
    # Common Asterisk config locations
    ASTERISK_PATHS=(
        "/etc/asterisk"
        "/usr/local/etc/asterisk"
        "/opt/asterisk/etc"
    )
    
    # Try to find Asterisk config directory
    for path in "${ASTERISK_PATHS[@]}"; do
        if [ -d "$path" ] && [ -f "$path/asterisk.conf" ]; then
            ASTERISK_DIR="$path"
            ASTERISK_FOUND=true
            break
        fi
    done
    
    # Check if Asterisk binary exists
    if command -v asterisk &>/dev/null; then
        ASTERISK_VERSION=$(asterisk -V 2>/dev/null | head -1 || echo "unknown")
        log_ok "Asterisk binary: $ASTERISK_VERSION"
    else
        log_warn "Asterisk binary not found in PATH"
    fi
    
    if [ "$ASTERISK_FOUND" = true ]; then
        log_ok "Asterisk config: $ASTERISK_DIR"
        
        # Check for FreePBX
        if [ -f "/etc/freepbx.conf" ] || [ -f "/etc/sangoma/pbx" ]; then
            FREEPBX_VERSION=$(fwconsole -V 2>/dev/null | head -1 || echo "detected")
            log_ok "FreePBX: $FREEPBX_VERSION"
        fi
    else
        log_warn "Asterisk config directory not found in standard locations"
        
        # Interactive mode: ask user for path
        if [ -t 0 ]; then  # Check if running interactively
            echo ""
            echo -e "${YELLOW}Enter Asterisk config directory path (or press Enter to skip):${NC}"
            read -r USER_ASTERISK_PATH
            
            if [ -n "$USER_ASTERISK_PATH" ]; then
                if [ -d "$USER_ASTERISK_PATH" ]; then
                    if [ -f "$USER_ASTERISK_PATH/asterisk.conf" ]; then
                        ASTERISK_DIR="$USER_ASTERISK_PATH"
                        ASTERISK_FOUND=true
                        log_ok "Asterisk config: $ASTERISK_DIR (user provided)"
                        
                        # Save to .env for future use
                        if [ -f "$SCRIPT_DIR/.env" ]; then
                            if ! grep -q "ASTERISK_CONFIG_DIR" "$SCRIPT_DIR/.env"; then
                                echo "ASTERISK_CONFIG_DIR=$ASTERISK_DIR" >> "$SCRIPT_DIR/.env"
                                log_ok "Saved ASTERISK_CONFIG_DIR to .env"
                            fi
                        fi
                    else
                        log_warn "No asterisk.conf found in $USER_ASTERISK_PATH"
                    fi
                else
                    log_warn "Directory does not exist: $USER_ASTERISK_PATH"
                fi
            else
                log_warn "Skipped Asterisk detection - dialplan integration may require manual setup"
            fi
        else
            # Non-interactive mode
            MANUAL_CMDS+=("# Set ASTERISK_CONFIG_DIR in .env if Asterisk is installed elsewhere")
        fi
    fi
    
    # Export for other functions
    export ASTERISK_DIR
    export ASTERISK_FOUND
}

# ----- Port Check -----
check_ports() {
    PORT=3003
    if command -v ss &>/dev/null; then
        if ss -tln | grep -q ":$PORT "; then
            log_warn "Port $PORT already in use"
        else
            log_ok "Port $PORT available"
        fi
    elif command -v netstat &>/dev/null; then
        if netstat -tln | grep -q ":$PORT "; then
            log_warn "Port $PORT already in use"
        else
            log_ok "Port $PORT available"
        fi
    fi
}

# ----- Apply Fixes -----
apply_fixes() {
    if [ ${#FIX_CMDS[@]} -eq 0 ]; then
        return 0
    fi
    
    echo ""
    echo -e "${YELLOW}Applying fixes...${NC}"
    
    for cmd in "${FIX_CMDS[@]}"; do
        echo "  Running: $cmd"
        if eval "$cmd"; then
            echo -e "    ${GREEN}✓${NC} Success"
        else
            echo -e "    ${RED}✗${NC} Failed"
        fi
    done
}

# ----- Summary -----
print_summary() {
    echo ""
    echo "========================================"
    echo "Pre-flight Summary"
    echo "========================================"
    
    if [ ${#FAILURES[@]} -gt 0 ]; then
        echo -e "${RED}Failures (${#FAILURES[@]}) - BLOCKING:${NC}"
        for f in "${FAILURES[@]}"; do echo "  ✗ $f"; done
        echo ""
    fi
    
    if [ ${#WARNINGS[@]} -gt 0 ]; then
        echo -e "${YELLOW}Warnings (${#WARNINGS[@]}):${NC}"
        for w in "${WARNINGS[@]}"; do echo "  ⚠ $w"; done
        echo ""
    fi
    
    if [ ${#MANUAL_CMDS[@]} -gt 0 ]; then
        echo -e "${YELLOW}Manual steps required:${NC}"
        for cmd in "${MANUAL_CMDS[@]}"; do echo "  $cmd"; done
        echo ""
    fi
    
    if [ ${#FIX_CMDS[@]} -gt 0 ] && [ "$APPLY_FIXES" = false ]; then
        echo -e "${YELLOW}Auto-fixable issues (run with --apply-fixes):${NC}"
        for cmd in "${FIX_CMDS[@]}"; do echo "  $cmd"; done
        echo ""
    fi
    
    if [ ${#FAILURES[@]} -eq 0 ] && [ ${#WARNINGS[@]} -eq 0 ]; then
        touch "$SCRIPT_DIR/.preflight-ok"
        echo -e "${GREEN}✓ All checks passed!${NC}"
        echo ""
        echo "Start the Admin UI:"
        echo "  $COMPOSE_CMD up -d"
        echo ""
        echo "Then open: http://localhost:3003"
    elif [ ${#FAILURES[@]} -gt 0 ]; then
        echo -e "${RED}Cannot proceed - fix failures above first.${NC}"
    fi
}

# ----- Main -----
main() {
    echo ""
    echo "AAVA Pre-flight Checks"
    echo "======================"
    echo ""
    
    detect_os
    check_docker
    check_compose
    check_directories
    check_selinux
    # NOTE: AppArmor checks removed - deferred per Section 8
    check_env
    check_asterisk
    check_ports
    
    # Apply fixes if requested
    if [ "$APPLY_FIXES" = true ]; then
        apply_fixes
    fi
    
    print_summary
    
    # Exit code: 2 for failures (blocking), 1 for warnings only, 0 for clean
    if [ ${#FAILURES[@]} -gt 0 ]; then
        exit 2
    elif [ ${#WARNINGS[@]} -gt 0 ]; then
        exit 1
    fi
    exit 0
}

main "$@"
```

---

## 4. Target Environments

### Supported Distributions (Non-EOL Only)

| OS | Versions | EOL Date | Init System | Notes |
|----|----------|----------|-------------|-------|
| **Ubuntu** | 22.04 LTS, 24.04 LTS | Apr 2027, Apr 2029 | systemd | Primary dev platform |
| **Debian** | 11 (Bullseye), 12 (Bookworm) | Jun 2026, Jun 2028 | systemd | Common server OS |
| **RHEL** | 8, 9 | May 2029, May 2032 | systemd | Enterprise |
| **Rocky Linux** | 8, 9 | May 2029, May 2032 | systemd | RHEL replacement |
| **AlmaLinux** | 8, 9 | May 2029, May 2032 | systemd | RHEL replacement |
| **Fedora** | 40, 41 | ~Nov 2025, ~May 2026 | systemd | Rootless Docker common |
| **Sangoma/FreePBX** | 16, 17 | Follows RHEL | systemd | Primary Asterisk target |

### Docker/Compose Version Matrix

| Version | Status | Notes |
|---------|--------|-------|
| **Docker ≥ 25.x** | ✅ Recommended | Current supported baseline |
| **Docker 20.10-24.x** | ⚠️ Supported | Upgrade recommended; API 1.41+ |
| **Docker < 20.10** | ❌ Unsupported | Missing BuildKit, health checks |
| **Compose v2 ≥ 2.20** | ✅ Recommended | Plugin mode (`docker compose`) |
| **Compose v2 < 2.20** | ⚠️ Warning | Missing profiles, watch |
| **Compose v1 (any)** | ❌ Deprecated | EOL July 2023, security risk |

### Known-Bad Versions

| Version | Issue | Remediation |
|---------|-------|-------------|
| Docker 20.10.0-20.10.6 | overlay2 storage bugs | Upgrade to 20.10.7+ |
| Compose v1 all | No security updates since May 2021 | Migrate to v2 |
| Compose v2 < 2.40.2 | [CVE-2025-62725](https://nvd.nist.gov/vuln/detail/CVE-2025-62725) (CVSS 8.9, path traversal) | Upgrade immediately |

### EOL/Older Versions (Supported with Warning)

| OS | EOL Date | Status |
|----|----------|--------|
| CentOS 7 | Jun 2024 | ⚠️ Warning - still works if Docker ≥20.10 |
| CentOS 8 | Dec 2021 | ⚠️ Warning - still works if Docker ≥20.10 |
| Ubuntu 20.04 | Apr 2025 | ⚠️ Warning - fully supported until EOL |
| Ubuntu 18.04 | Apr 2023 | ⚠️ Warning - may work but no testing |
| Debian 10 | Jun 2024 | ⚠️ Warning - still works if Docker ≥20.10 |
| Debian 9 | Jun 2022 | ⚠️ Warning - may work but no testing |

> **Policy**: We don't block installation on older OSes. If Docker and Docker Compose meet our minimum versions, AAVA will work. Users are warned about EOL status.

### Rootless vs Rootful Docker

| Mode | Detection | Socket Path | Notes |
|------|-----------|-------------|-------|
| **Rootful** | `docker context ls` shows `unix:///var/run/docker.sock` | `/var/run/docker.sock` | Default on most servers |
| **Rootless** | `$DOCKER_HOST` or `$XDG_RUNTIME_DIR` set | `$XDG_RUNTIME_DIR/docker.sock` | Default on Fedora, Ubuntu Desktop |

**Rootless implications:**

| Issue | Rootful Command | Rootless Command |
|-------|-----------------|------------------|
| Start Docker | `sudo systemctl start docker` | `systemctl --user start docker` |
| Add to group | `sudo usermod -aG docker $USER` | N/A (not needed) |
| Create media dir | `sudo mkdir -p /mnt/...` | `mkdir -p ~/aava-media` |
| Fix permissions | `sudo chown ...` | N/A (user already owns) |
| Ports < 1024 | Works by default | `sudo sysctl -w net.ipv4.ip_unprivileged_port_start=0` |
| SELinux volumes | No suffix needed | Add `:Z` suffix to mounts |

> **Note**: Preflight script detects rootless mode and suggests appropriate commands.

---

## 5. Architecture

### Shared Platform Data File

To avoid hardcoding platform-specific commands in multiple places, use a shared YAML config:

```yaml
# config/platforms.yaml - Shared by preflight.sh, CLI, and Admin UI

platforms:
  debian:
    docker_install: "apt-get update && apt-get install -y docker.io"
    docker_start: "systemctl start docker"
    docker_start_fallback: "service docker start"
    compose_install: "apt-get install -y docker-compose-v2"
    add_user_to_docker: "usermod -aG docker {user}"
    firewall_open_port: "ufw allow {port}/udp"
    selinux: false
    apparmor: true
    media_paths:
      default: "/mnt/asterisk_media/ai-generated"
      asterisk_sounds: "/var/lib/asterisk/sounds"
    
  rhel:
    docker_install: "dnf install -y docker-ce docker-ce-cli containerd.io"
    docker_start: "systemctl start docker"
    docker_start_fallback: "service docker start"
    compose_install: "dnf install -y docker-compose-plugin"
    add_user_to_docker: "usermod -aG docker {user}"
    firewall_open_port: "firewall-cmd --add-port={port}/udp --permanent && firewall-cmd --reload"
    selinux: true
    selinux_context: "semanage fcontext -a -t container_file_t '{path}(/.*)?'"
    selinux_restore: "restorecon -Rv {path}"
    selinux_tools_package: "policycoreutils-python-utils"
    apparmor: false
    media_paths:
      default: "/mnt/asterisk_media/ai-generated"
      asterisk_sounds: "/var/lib/asterisk/sounds"
  
  fedora:
    inherit: rhel
    docker_install: "dnf install -y moby-engine docker-compose"
    rootless_default: true
    rootless_socket: "$XDG_RUNTIME_DIR/docker.sock"
```

### API Schema for Admin UI

```yaml
# GET /api/system/platform
# Returns platform detection and check results

response:
  platform:
    os:
      id: "ubuntu"
      version: "22.04"
      family: "debian"
      arch: "x86_64"
      eol_date: "2027-04-01"
      is_eol: false
    docker:
      installed: true
      version: "25.0.3"
      mode: "rootful"  # or "rootless"
      socket: "/var/run/docker.sock"
      status: "ok"  # ok | warning | error
      message: null
    compose:
      installed: true
      version: "2.24.5"
      type: "plugin"  # plugin | standalone_v1 (deprecated)
      status: "ok"
      message: null
    selinux:
      present: true
      mode: "enforcing"  # enforcing | permissive | disabled
      tools_installed: true  # semanage available
    directories:
      media:
        path: "/mnt/asterisk_media/ai-generated"
        exists: true
        writable: true
        status: "ok"
    asterisk:
      detected: true
      version: "20.5.0"
      freepbx:
        detected: true
        version: "16.0.40"
    
  checks:
    - id: "os_eol"
      status: "warning"  # warning = informational, not blocking
      message: "CentOS 7 is EOL (Jun 2024)"
      blocking: false    # Still works if Docker/Compose meet requirements
      action:
        type: "link"
        label: "Migration Guide (Optional)"
        value: "https://rockylinux.org/migrate2rocky"
    - id: "docker_not_running"
      status: "warning"
      message: "Docker daemon not running"
      blocking: false
      action:
        type: "command"
        label: "Start Docker"
        value: "sudo systemctl start docker"
        rootless_value: "systemctl --user start docker"
    - id: "docker_permission"
      status: "warning"
      message: "Cannot access Docker (permission denied)"
      blocking: false
      action:
        type: "command"
        label: "Add to docker group"
        value: "sudo usermod -aG docker $USER && newgrp docker"
        rootless_value: null  # Not applicable for rootless
    - id: "docker_version"
      status: "ok"
      message: "Docker 25.0.3"
      blocking: false
      action: null
    - id: "compose_version"
      status: "error"
      message: "Compose v1 is EOL and unsupported"
      blocking: true
      action:
        type: "link"
        label: "Upgrade Guide"
        value: "https://docs.docker.com/compose/install/"
    - id: "media_dir"
      status: "warning"
      message: "Media directory not writable"
      blocking: false  # Can proceed, but may have issues
      action:
        type: "command"
        label: "Fix Permissions"
        value: "sudo chown -R $(id -u) /mnt/asterisk_media"
        rootless_value: "mkdir -p ~/aava-media"  # Different for rootless
    - id: "asterisk_config"
      status: "ok"
      message: "Asterisk config: /etc/asterisk"
      blocking: false
      action: null
    - id: "asterisk_not_found"
      status: "warning"
      message: "Asterisk config directory not found"
      blocking: false  # Can still run containerized Asterisk
      action:
        type: "modal"
        label: "Set Asterisk Path"
        modal_fields:
          - name: "asterisk_config_dir"
            label: "Asterisk Config Directory"
            placeholder: "/etc/asterisk"
            validation: "directory_exists"
  
  summary:
    total_checks: 8
    passed: 5
    warnings: 2   # OS EOL is now a warning, not error
    errors: 1     # Only Docker/Compose version errors are blocking
    blocking_errors: 1
    ready: false  # false only if blocking errors (Docker/Compose)
```

### Container Control API

```yaml
# POST /api/system/containers/{action}
# Actions: start | stop | restart | refresh

# Example: POST /api/system/containers/restart
request:
  containers: ["ai-engine", "admin-ui"]  # Optional, default: all
  
response:
  success: true
  results:
    - container: "ai-engine"
      action: "restart"
      status: "completed"
      duration_ms: 3200
    - container: "admin-ui"
      action: "restart"
      status: "completed"
      duration_ms: 1800

# POST /api/system/preflight
# Re-run preflight checks and return updated status

response:
  # Same as GET /api/system/platform, but freshly computed
```

### Check Severity to UI State Mapping

| Check Status | `blocking` | UI Behavior |
|--------------|------------|-------------|
| `error` | `true` | Red banner, "Fix Required", deployment blocked |
| `error` | `false` | Red icon, can proceed with warning |
| `warning` | `false` | Yellow icon, informational |
| `ok` | `false` | Green check |

### Rootless-Aware Actions

When `docker.mode == "rootless"`, the API should:

1. **Omit** `usermod -aG docker` suggestions (not applicable)
2. **Include** `systemctl --user start docker` instead of `sudo systemctl start docker`
3. **Suggest** user-writable paths (e.g., `~/aava-media`) instead of `/mnt/...`
4. **Note** that ports < 1024 require sysctl config:

```yaml
action:
  type: "command"
  label: "Enable low ports"
  value: "sudo sysctl -w net.ipv4.ip_unprivileged_port_start=0"
```

### UI Actions from Checks

| Check Status | UI Display | Action Type |
|--------------|------------|-------------|
| `ok` | Green check | None |
| `warning` | Yellow warning | Copyable command or docs link |
| `error` | Red X | Modal with steps or block deployment |

### Component Responsibilities

```
┌─────────────────────────────────────────────────────────────┐
│                     config/platforms.yaml                    │
│              (Shared platform-specific commands)             │
└─────────────────────┬───────────────────────────────────────┘
                      │ Read by
          ┌───────────┼───────────┐
          ▼           ▼           ▼
   ┌────────────┐ ┌────────┐ ┌──────────────┐
   │ preflight  │ │  CLI   │ │  Admin UI    │
   │   .sh      │ │ (Go)   │ │  Backend     │
   └────────────┘ └────────┘ └──────────────┘
         │             │            │
         │             │            ▼
         │             │     ┌──────────────┐
         │             │     │ /api/system/ │
         │             │     │   platform   │
         │             │     └──────────────┘
         │             │            │
         ▼             ▼            ▼
   ┌─────────────────────────────────────────┐
   │          Consistent UX Across           │
   │         CLI, Script, and UI             │
   └─────────────────────────────────────────┘
```

### CLI Commands (Advanced Users)

```bash
agent doctor             # Run all checks, show summary
agent doctor --json      # Output for scripting
agent doctor --fix       # Apply fixes (requires sudo)
agent platform           # Show detected platform info
agent platform --json    # Machine-readable output
```

---

## 6. Implementation Phases

### Phase 1: Pre-flight Script (Week 1-2)

**Deliverables:**

- [ ] Create `preflight.sh` with full check suite
- [ ] OS detection (debian/rhel/fedora families) with EOL warnings
- [ ] Docker version check (hard fail < 20.10)
- [ ] Rootless Docker detection with mode-specific commands
- [ ] Compose v1 hard fail; v2 version check
- [ ] SELinux detection + `semanage` availability check (RHEL family)
- [ ] Directory permission checks (rootless-aware)
- [ ] Asterisk detection (auto-locate or prompt for path)
- [ ] FreePBX detection
- [ ] Port availability check
- [ ] `--apply-fixes` flag for opt-in fixes
- [ ] Separate FIX_CMDS (auto-fixable) vs MANUAL_CMDS (user action)
- [ ] Summary output (always shown, even on failure)
- [ ] Update README with new quick start

> **Note**: AppArmor detection is deferred (see Section 8). Docker on Ubuntu auto-configures AppArmor profiles.

**Success Criteria:**

```bash
# Ubuntu 22.04 (typical setup):
./preflight.sh
# ✓ All checks pass, ready to start

# Rocky 9 with SELinux + missing semanage:
./preflight.sh
# Shows: sudo dnf install policycoreutils-python-utils
# Shows: sudo semanage fcontext ... (after tools installed)

# Fedora with rootless Docker:
./preflight.sh
# Detects rootless mode, skips usermod suggestion
```

### Phase 2: Platform Data File (Week 3-4)

**Deliverables:**

- [ ] Create `config/platforms.yaml` with per-distro commands
- [ ] Include: docker install, start, compose, firewall, SELinux
- [ ] Fedora-specific rootless defaults
- [ ] Update `preflight.sh` to read from YAML (optional, can remain bash)
- [ ] Update CLI `agent doctor` to use same data
- [ ] Document how to add new distro support

**Success Criteria:**

- Adding Fedora 42 support = add YAML block, not code changes
- CLI and script show consistent remediation commands

### Phase 3: Admin UI Integration (Week 5-6)

**Deliverables:**

- [ ] Backend endpoint: `GET /api/system/platform`
- [ ] Response matches schema in Section 5
- [ ] Dashboard "System Status" card with expandable checks
- [ ] Each check shows: status icon, message, action button
- [ ] Action button: copy command / open docs / show modal
- [ ] "Run Preflight" button that triggers check refresh
- [ ] FreePBX detection in status panel

**Success Criteria:**

```
User opens Admin UI → System Status card:
┌─────────────────────────────────────────┐
│ System Status                     [⟳]   │
├─────────────────────────────────────────┤
│ ✓ OS: Ubuntu 22.04 (x86_64)            │
│ ✓ Docker: 25.0.3                        │
│ ⚠ Compose: 2.18.1 (upgrade recommended) │
│   [Copy upgrade command]                │
│ ✓ Asterisk: Connected (20.5.0)          │
│ ✓ FreePBX: 16.0.40                      │
└─────────────────────────────────────────┘
```

### Phase 4: Testing & Documentation (Week 7-8)

**Deliverables:**

- [ ] CI matrix: Ubuntu 22.04, 24.04, Rocky 9, Debian 12
- [ ] Manual test on Sangoma FreePBX 16/17
- [ ] Manual test on Fedora 40 (rootless)
- [ ] Update README quick start section
- [x] Create `docs/TROUBLESHOOTING.md` with common issues (redirects to `docs/TROUBLESHOOTING_GUIDE.md`)
- [ ] Add platform-specific FAQ sections
- [ ] Document version matrix and EOL policy

---

## 7. Timeline & Milestones

| Week | Milestone | Deliverable |
|------|-----------|-------------|
| 2 | **M1** | `preflight.sh` working on Ubuntu, Rocky, Fedora |
| 4 | **M2** | `config/platforms.yaml` + CLI integration |
| 6 | **M3** | Admin UI `/api/system/platform` + status card |
| 8 | **M4** | CI matrix + docs + ready for release |

### Exit Criteria for Release

- [ ] `preflight.sh` tested on: Ubuntu 22.04, Rocky 9, Fedora 40, Sangoma 16
- [ ] Zero failures on fresh install (warnings acceptable)
- [ ] Admin UI shows platform status without errors
- [ ] All version checks match documented matrix
- [ ] Rootless Docker works on Fedora
- [ ] SELinux works on Rocky/Sangoma
- [ ] README updated with new quick start

---

## 8. What We're NOT Doing (Deferred)

| Feature | Reason | Status |
|---------|--------|--------|
| Podman native support | 95% users on Docker | Deferred - user demand required |
| ARM64 architecture | Most Asterisk servers x86_64 | Deferred - user demand required |
| 32-bit x86 | Docker images are 64-bit only | **Not supported** |
| Systemd service auto-install | Too many edge cases | Deferred - after v5.0 |
| Complex Go handler system | Bash script sufficient | **Won't do** (YAGNI) |
| GraphQL FreePBX integration | fwconsole CLI sufficient | **Won't do** (YAGNI) |
| **AppArmor handling** | Rarely blocks Docker on Ubuntu | **Deferred** - checks removed from preflight |

### AppArmor Clarification

AppArmor is **not checked** in the preflight script. Rationale:

1. Docker on Ubuntu auto-configures AppArmor profiles
2. AppArmor issues are rare and hard to diagnose programmatically
3. Adding AppArmor checks would increase false positives
4. If users report AppArmor-related mount failures, we'll add targeted guidance

**If AppArmor causes issues**, users should consult the [Docker AppArmor documentation](https://docs.docker.com/engine/security/apparmor/).

---

## Appendix: File Locations by Platform

| Item | Debian/Ubuntu | RHEL/CentOS | Sangoma |
|------|---------------|-------------|---------|
| Asterisk config | `/etc/asterisk` | `/etc/asterisk` | `/etc/asterisk` |
| Asterisk sounds | `/var/lib/asterisk/sounds` | `/var/lib/asterisk/sounds` | `/var/lib/asterisk/sounds` |
| FreePBX web | N/A | N/A | `/var/www/html` |
| fwconsole | N/A | N/A | `/usr/sbin/fwconsole` |
| Media dir | `/mnt/asterisk_media` | `/mnt/asterisk_media` | `/mnt/asterisk_media` |

---

## Appendix: Command Mapping

| Action | Debian/Ubuntu | RHEL/CentOS | Sangoma |
|--------|---------------|-------------|---------|
| Install Docker | `apt install docker.io` | `dnf install docker-ce` | `yum install docker-ce` |
| Compose command | `docker compose` | `docker compose` | `docker compose` |
| Add to docker group | `usermod -aG docker $USER` | `usermod -aG docker $USER` | `usermod -aG docker $USER` |
| Reload Asterisk | `asterisk -rx 'core reload'` | `asterisk -rx 'core reload'` | `fwconsole reload` |
| SELinux context | N/A | `semanage fcontext -a -t container_file_t` | Same |
| Firewall (ports) | `ufw allow 18080:18099/udp` | `firewall-cmd --add-port=18080-18099/udp` | Same |
