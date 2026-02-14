#!/bin/bash
# preflight.sh - Prepare system for AAVA Admin UI
# AAVA-126: Cross-Platform Support
#
# Usage:
#   ./preflight.sh              # Check system, show issues
#   ./preflight.sh --apply-fixes # Auto-fix what we can
#   ./preflight.sh --help        # Show usage
#
# Exit codes:
#   0 = All checks passed
#   1 = Warnings only (can proceed)
#   2 = Failures (blocking issues)

# NOTE: No 'set -e' - we want to collect ALL issues before exiting

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_ok() { echo -e "${GREEN}✓${NC} $1"; }
log_warn() { echo -e "${YELLOW}⚠${NC} $1"; WARNINGS+=("$1"); }
log_fail() { echo -e "${RED}✗${NC} $1"; FAILURES+=("$1"); }
log_info() { echo -e "${BLUE}ℹ${NC} $1"; }

# State
WARNINGS=()
FAILURES=()
FIX_CMDS=()          # Commands that --apply-fixes will run
MANUAL_CMDS=()       # Commands user must run manually (e.g., reboot/logout)
APPLY_FIXES=false
FORCE_MODE=false
DOCKER_ROOTLESS=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Detected values
OS_ID="unknown"
OS_VERSION="unknown"
OS_FAMILY="unknown"
ARCH=""
ASTERISK_DIR=""
ASTERISK_FOUND=false
COMPOSE_CMD=""

# Docs and platform config (best-effort; script still works without them)
AAVA_DOCS_BASE_URL="${AAVA_DOCS_BASE_URL:-https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/main/}"
PLATFORMS_YAML="$SCRIPT_DIR/config/platforms.yaml"

github_docs_url() {
    local path_or_url="$1"
    [ -z "$path_or_url" ] && return 1
    if [[ "$path_or_url" == http://* || "$path_or_url" == https://* ]]; then
        echo "$path_or_url"
        return 0
    fi
    echo "${AAVA_DOCS_BASE_URL%/}/$(echo "$path_or_url" | sed 's#^/##')"
}

platform_yaml_get() {
    local dotted_key="$1"
    [ -z "$dotted_key" ] && return 1
    command -v python3 &>/dev/null || return 1
    [ -f "$PLATFORMS_YAML" ] || return 1

    python3 - "$PLATFORMS_YAML" "$OS_ID" "$OS_FAMILY" "$dotted_key" <<'PY' 2>/dev/null
import sys, yaml

path, os_id, os_family, dotted_key = sys.argv[1:5]
with open(path, "r") as f:
    data = yaml.safe_load(f) or {}

def deep_merge(base, override):
    out = dict(base or {})
    for k, v in (override or {}).items():
        if k == "inherit":
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out.get(k), v)
        else:
            out[k] = v
    return out

def resolve_platform(key):
    node = data.get(key)
    if not isinstance(node, dict):
        return {}
    parent = node.get("inherit")
    if isinstance(parent, str) and parent:
        return deep_merge(resolve_platform(parent), node)
    return deep_merge({}, node)

def select_key():
    if os_id in data and isinstance(data.get(os_id), dict):
        return os_id
    for k, node in data.items():
        if not isinstance(node, dict):
            continue
        ids = node.get("os_ids") or []
        if isinstance(ids, list) and os_id in ids:
            return k
    if os_family in data and isinstance(data.get(os_family), dict):
        return os_family
    return None

platform_key = select_key()
platform = resolve_platform(platform_key) if platform_key else {}

cur = platform
for k in dotted_key.split("."):
    if not isinstance(cur, dict) or k not in cur:
        sys.exit(1)
    cur = cur[k]

if isinstance(cur, (dict, list)):
    sys.exit(1)
print(cur)
PY
}

# ============================================================================
# Filesystem + env helpers (portable across GNU/BSD)
# ============================================================================
CONTAINER_UID_DEFAULT=1000

stat_uid() {
    stat -c '%u' "$1" 2>/dev/null || stat -f '%u' "$1" 2>/dev/null || echo ""
}

stat_gid() {
    stat -c '%g' "$1" 2>/dev/null || stat -f '%g' "$1" 2>/dev/null || echo ""
}

stat_mode() {
    stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1" 2>/dev/null || echo ""
}

env_get() {
    local key="$1"
    local default_value="${2:-}"
    [ -z "$key" ] && { echo "$default_value"; return 0; }
    [ ! -f "$SCRIPT_DIR/.env" ] && { echo "$default_value"; return 0; }

    local value
    value="$(grep -E "^[# ]*${key}=" "$SCRIPT_DIR/.env" 2>/dev/null | tail -n1 | sed -E "s/^[# ]*${key}=//")"
    value="$(echo "$value" | tr -d '\r' | xargs 2>/dev/null || echo "$value")"
    if [ -z "$value" ]; then
        echo "$default_value"
    else
        echo "$value"
    fi
}

choose_shared_gid() {
    # Prefer the host's actual asterisk group when present (local Asterisk case).
    local ast_gid=""
    if id asterisk &>/dev/null; then
        ast_gid="$(id -g asterisk 2>/dev/null || true)"
    elif getent passwd asterisk &>/dev/null; then
        # Some systems encode primary GID in passwd entry.
        ast_gid="$(getent passwd asterisk | cut -d: -f4 2>/dev/null || true)"
    fi
    if [[ "$ast_gid" =~ ^[0-9]+$ ]]; then
        echo "$ast_gid"
        return 0
    fi

    # Next best: align with the configured container build arg (if user already set it).
    ast_gid="$(env_get "ASTERISK_GID" "")"
    if [[ "$ast_gid" =~ ^[0-9]+$ ]]; then
        echo "$ast_gid"
        return 0
    fi

    # Fallback: container runtime user group (keeps Admin UI + ai-engine writable).
    echo "${CONTAINER_UID_DEFAULT}"
}

print_fix_and_docs() {
    local cmd="$1"
    local docs="$2"
    if [ -n "$cmd" ]; then
        log_info "  Fix command:"
        while IFS= read -r line; do
            [ -z "$line" ] && continue
            echo "      $line"
        done <<< "$cmd"
    fi
    if [ -n "$docs" ]; then
        log_info "  Docs: $docs"
    fi
}

is_systemd_available() {
    command -v systemctl >/dev/null 2>&1 || return 1
    local pid1
    pid1="$(ps -p 1 -o comm= 2>/dev/null | tr -d ' ' || true)"
    [ "$pid1" = "systemd" ]
}

fstab_has_mountpoint() {
    local mountpoint="$1"
    awk -v mp="$mountpoint" '
        $0 ~ /^[[:space:]]*#/ { next }
        NF < 2 { next }
        $2 == mp { found=1 }
        END { exit(found ? 0 : 1) }
    ' /etc/fstab 2>/dev/null
}

fstab_mountpoint_has_bind_option() {
    local mountpoint="$1"
    awk -v mp="$mountpoint" '
        $0 ~ /^[[:space:]]*#/ { next }
        NF < 4 { next }
        $2 == mp && $4 ~ /(^|,)bind(,|$)/ { found=1 }
        END { exit(found ? 0 : 1) }
    ' /etc/fstab 2>/dev/null
}

ensure_fstab_bind_mount() {
    local source="$1"
    local target="$2"

    if [ ! -f /etc/fstab ]; then
        log_fail "/etc/fstab not found; cannot persist media bind mount"
        return 1
    fi

    if fstab_has_mountpoint "$target"; then
        if fstab_mountpoint_has_bind_option "$target"; then
            log_ok "Bind mount already persisted in /etc/fstab: $target"
        else
            log_warn "Found an /etc/fstab entry for $target but it is not a bind mount; leaving unchanged"
        fi
        return 0
    fi

    local options="bind,nofail"
    if is_systemd_available; then
        options="bind,nofail,x-systemd.automount"
    fi
    local fstab_line="$source $target none $options 0 0"

    if {
        echo ""
        echo "# AAVA: expose generated audio to Asterisk (bind mount)"
        echo "$fstab_line"
    } | sudo tee -a /etc/fstab >/dev/null; then
        log_ok "Persisted media bind mount in /etc/fstab"
        if is_systemd_available; then
            sudo systemctl daemon-reload >/dev/null 2>&1 || log_warn "systemctl daemon-reload failed (fstab changes may not apply until reboot)"
        fi
        return 0
    fi

    log_fail "Failed to update /etc/fstab for media bind mount"
    log_info "  You can persist it manually by adding this line to /etc/fstab:"
    log_info "  $fstab_line"
    return 1
}

verify_fstab_bind_mount() {
    local target="$1"
    if [ ! -f /etc/fstab ]; then
        log_warn "/etc/fstab not found; cannot verify bind mount persistence"
        return 1
    fi
    if ! fstab_has_mountpoint "$target"; then
        log_warn "No /etc/fstab entry found for: $target"
        return 1
    fi
    if fstab_mountpoint_has_bind_option "$target"; then
        log_ok "Bind mount persistence entry found in /etc/fstab: $target"
        return 0
    fi
    log_warn "/etc/fstab has an entry for $target but it does not appear to be a bind mount"
    return 1
}

# Parse args
LOCAL_AI_MODE_OVERRIDE=""
PERSIST_MEDIA_MOUNT=false
for arg in "$@"; do
    case $arg in
        --apply-fixes) APPLY_FIXES=true ;;
        --force) FORCE_MODE=true ;;
        --local-ai-mode=*) LOCAL_AI_MODE_OVERRIDE="${arg#*=}" ;;
        --local-ai-minimal) LOCAL_AI_MODE_OVERRIDE="minimal" ;;
        --local-ai-full) LOCAL_AI_MODE_OVERRIDE="full" ;;
        --persist-media-mount) PERSIST_MEDIA_MOUNT=true ;;
        --help|-h) 
            echo "AAVA Pre-flight Check"
            echo ""
            echo "Usage: sudo ./preflight.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --apply-fixes  Apply fixes automatically (requires root/sudo)"
            echo "  --force        Downgrade unsupported-OS failure to warning (for users with Docker pre-installed)"
            echo "  --local-ai-mode=MODE  Set LOCAL_AI_MODE in .env (MODE=full|minimal)"
            echo "  --local-ai-minimal    Shortcut for --local-ai-mode=minimal"
            echo "  --local-ai-full       Shortcut for --local-ai-mode=full"
            echo "  --persist-media-mount Verify (and with --apply-fixes, persist) Asterisk sounds bind mount when bind-mount mode is used"
            echo "  --help         Show this help message"
            echo ""
            echo "Exit codes:"
            echo "  0 = All checks passed"
            echo "  1 = Warnings only (can proceed)"
            echo "  2 = Failures (blocking issues)"
            echo ""
            echo "Note: For --apply-fixes, run as root or with sudo:"
            echo "  sudo ./preflight.sh --apply-fixes"
            exit 0 
            ;;
    esac
done

# Check for root/sudo when --apply-fixes is used
if [ "$APPLY_FIXES" = true ] && [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}ERROR: --apply-fixes requires root privileges${NC}"
    echo ""
    echo "Please run with sudo:"
    echo "  sudo ./preflight.sh --apply-fixes"
    echo ""
    echo "Or run without --apply-fixes to see issues only:"
    echo "  ./preflight.sh"
    exit 2
fi

# ============================================================================
# OS Detection
# ============================================================================
detect_os() {
    IS_SANGOMA=false

    # Always detect the host OS from /etc/os-release first.
    # IMPORTANT: FreePBX can run on Debian-family distros; do not override OS detection based on /etc/freepbx.conf.
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS_ID="${ID:-unknown}"
        OS_VERSION="${VERSION_ID:-unknown}"

        case "$OS_ID" in
            ubuntu|debian|linuxmint) OS_FAMILY="debian" ;;
            centos|rhel|rocky|almalinux|fedora) OS_FAMILY="rhel" ;;
        esac

        # Best-effort: infer family from ID_LIKE for derivatives (e.g., some Debian 12 variants).
        if [ "$OS_FAMILY" = "unknown" ] && [ -n "${ID_LIKE:-}" ]; then
            local id_like
            id_like="$(echo "${ID_LIKE:-}" | tr '[:upper:]' '[:lower:]')"
            if [[ "$id_like" == *debian* || "$id_like" == *ubuntu* ]]; then
                OS_FAMILY="debian"
                log_warn "OS family inferred from ID_LIKE ($ID_LIKE) - best-effort support"
            elif [[ "$id_like" == *rhel* || "$id_like" == *fedora* || "$id_like" == *centos* ]]; then
                OS_FAMILY="rhel"
                log_warn "OS family inferred from ID_LIKE ($ID_LIKE) - best-effort support"
            fi
        fi
    fi

    # Sangoma Linux is CentOS 7 based; only treat as "sangoma" when Sangoma markers exist.
    if [ -f /etc/sangoma/pbx ]; then
        IS_SANGOMA=true
        OS_ID="sangoma"
        OS_FAMILY="rhel"
    fi
    
    # Check architecture (HARD FAIL for non-x86_64)
    ARCH=$(uname -m)
    if [ "$ARCH" != "x86_64" ]; then
        log_fail "Unsupported architecture: $ARCH"
        log_info "  AAVA requires x86_64 (64-bit Intel/AMD) architecture"
        log_info "  ARM64/aarch64 support is planned for a future release"
        log_info "  Docs: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/main/docs/SUPPORTED_PLATFORMS.md"
    else
        log_ok "Architecture: $ARCH"
    fi
    
    # Check for unsupported OS family with helpful instructions
    if [ "$OS_FAMILY" = "unknown" ]; then
        if [ "$FORCE_MODE" = true ]; then
            log_warn "Unsupported Linux distribution: $OS_ID (continuing due to --force)"
        else
            log_fail "Unsupported Linux distribution: $OS_ID"
        fi
        log_info ""
        log_info "  Verified (maintainer-tested):"
        log_info "    - PBX Distro 12.7.8-2306-1.sng7 (Sangoma/FreePBX)"
        log_info ""
        log_info "  Best-effort (community-supported):"
        log_info "    - Ubuntu/Debian"
        log_info "    - RHEL/Rocky/Alma/Fedora"
        log_info ""
        log_info "  For other distributions, you can still run AAVA if you:"
        log_info "    1. Install Docker manually: https://docs.docker.com/engine/install/"
        log_info "    2. Install Docker Compose v2"
        log_info "    3. Ensure systemd is available"
        log_info ""
        log_info "  Supported platforms matrix:"
        log_info "    https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/main/docs/SUPPORTED_PLATFORMS.md"
        log_info ""
        log_info "  Then re-run with --force to skip this check:"
        log_info "    ./preflight.sh --force"
    fi
    
    # Check EOL status (WARNING only - we still support if Docker works)
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

# ============================================================================
# IPv6 Check (GA best-effort)
# ============================================================================
check_ipv6() {
    # AAVA runs in host-network mode by default; container-level IPv6 sysctls are not reliable here.
    # We warn (non-blocking) and recommend host-level disable for GA stability.
    local IPV6_SYSCTL="/proc/sys/net/ipv6/conf/all/disable_ipv6"
    [ -r "$IPV6_SYSCTL" ] || return 0

    local disabled
    disabled="$(cat "$IPV6_SYSCTL" 2>/dev/null | tr -d '[:space:]' || true)"
    if [ "$disabled" = "0" ]; then
        local IPV6_DOCS_URL
        IPV6_DOCS_URL="$(github_docs_url "docs/TROUBLESHOOTING_GUIDE.md" 2>/dev/null || true)"
        log_warn "IPv6 is enabled (best-effort) - recommend disabling IPv6 on the host for GA stability"
        log_info "  Recommendation (temporary):"
        log_info "    sudo sysctl -w net.ipv6.conf.all.disable_ipv6=1"
        log_info "    sudo sysctl -w net.ipv6.conf.default.disable_ipv6=1"
        log_info "  Recommendation (persistent):"
        log_info "    cat <<'EOF' | sudo tee /etc/sysctl.d/99-disable-ipv6.conf"
        log_info "    net.ipv6.conf.all.disable_ipv6=1"
        log_info "    net.ipv6.conf.default.disable_ipv6=1"
        log_info "    EOF"
        log_info "    sudo sysctl --system"
        [ -n "$IPV6_DOCS_URL" ] && log_info "  Docs: ${IPV6_DOCS_URL}#ipv6-ga-policy"
    fi
}

# ============================================================================
# Docker Installation (for --apply-fixes)
# ============================================================================
install_docker_rhel() {
    log_info "Installing Docker for RHEL/CentOS family..."
    
    # Detect package manager (dnf for RHEL 8+/Fedora, yum for CentOS 7/Sangoma)
    local PKG_MGR="yum"
    local PKG_MGR_CONFIG="yum-config-manager"

    if ! command -v dnf &>/dev/null && ! command -v yum &>/dev/null; then
        log_fail "No RHEL-family package manager found (dnf/yum missing)"
        log_info "  Detected OS: $OS_ID $OS_VERSION ($OS_FAMILY family)"
        log_info "  Install Docker manually: https://docs.docker.com/engine/install/"
        return 1
    fi
    
    if command -v dnf &>/dev/null; then
        PKG_MGR="dnf"
        # dnf uses dnf config-manager (with space, not hyphen)
        PKG_MGR_CONFIG="dnf config-manager"
        log_info "Using dnf package manager"
    else
        log_info "Using yum package manager"
    fi
    
    # Remove old Docker if present
    $PKG_MGR remove -y docker docker-client docker-client-latest docker-common \
        docker-latest docker-latest-logrotate docker-logrotate docker-engine 2>/dev/null
    
    # Install prerequisites
    if [ "$PKG_MGR" = "dnf" ]; then
        dnf install -y dnf-plugins-core
    else
        yum install -y yum-utils
    fi
    
    # Determine Docker repo URL based on distro
    local DOCKER_REPO_URL=""
    local DOCKER_REPO_VERSION=""
    
    # Source os-release for accurate detection
    if [ -f /etc/os-release ]; then
        . /etc/os-release
    fi
    
    # For Sangoma/FreePBX Distro, we need to create the repo manually
    # because the distro version string which Docker doesn't recognize
    if [ "${IS_SANGOMA:-false}" = true ] || [ "$OS_ID" = "sangoma" ] || [ -f /etc/sangoma/pbx ]; then
        log_info "Detected Sangoma/FreePBX - using CentOS 7 Docker repo"
        DOCKER_REPO_VERSION="7"
        mkdir -p /etc/yum.repos.d
        cat > /etc/yum.repos.d/docker-ce.repo << 'EOF'
[docker-ce-stable]
name=Docker CE Stable - $basearch
baseurl=https://download.docker.com/linux/centos/7/$basearch/stable
enabled=1
gpgcheck=1
gpgkey=https://download.docker.com/linux/centos/gpg
EOF
    elif [ "$ID" = "fedora" ]; then
        log_info "Detected Fedora - using Fedora Docker repo"
        $PKG_MGR_CONFIG --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
    elif [ "$ID" = "rhel" ] || [ "$ID" = "centos" ] || [ "$ID" = "rocky" ] || [ "$ID" = "almalinux" ]; then
        # Determine version for repo URL
        local MAJOR_VERSION="${VERSION_ID%%.*}"
        log_info "Detected $ID $MAJOR_VERSION - using CentOS $MAJOR_VERSION Docker repo"
        
        if [ "$MAJOR_VERSION" -ge 8 ]; then
            # RHEL 8+ uses dnf
            $PKG_MGR_CONFIG --add-repo https://download.docker.com/linux/centos/docker-ce.repo
        else
            # CentOS 7
            yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
        fi
    else
        log_fail "Unsupported RHEL-family distro: $ID"
        log_info "  Please install Docker manually: https://docs.docker.com/engine/install/"
        return 1
    fi
    
    # Install Docker CE
    if ! $PKG_MGR install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; then
        log_fail "Docker package installation failed"
        log_info "  This may happen if Docker doesn't support your OS version"
        log_info "  Try manual installation: https://docs.docker.com/engine/install/centos/"
        return 1
    fi
    
    # Start and enable Docker
    systemctl start docker
    systemctl enable docker
    
    # Verify
    if docker --version &>/dev/null; then
        log_ok "Docker installed successfully"
        return 0
    else
        log_fail "Docker installation failed"
        log_info "  Check logs: journalctl -u docker"
        return 1
    fi
}

install_docker_debian() {
    log_info "Installing Docker for Debian/Ubuntu family..."
    
    # Determine the correct Docker repo based on actual distro
    local DOCKER_DISTRO=""
    local DOCKER_CODENAME=""
    
    # Source os-release to get ID and VERSION_CODENAME.
    # NOTE: Some environments omit VERSION_CODENAME (or use derivatives), so we fall back to VERSION_ID mappings.
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        case "$ID" in
            ubuntu)
                DOCKER_DISTRO="ubuntu"
                DOCKER_CODENAME="${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}"
                if [ -z "$DOCKER_CODENAME" ] && [ -n "${VERSION_ID:-}" ]; then
                    case "$VERSION_ID" in
                        24.04*) DOCKER_CODENAME="noble" ;;
                        23.10*) DOCKER_CODENAME="mantic" ;;
                        23.04*) DOCKER_CODENAME="lunar" ;;
                        22.04*) DOCKER_CODENAME="jammy" ;;
                        20.04*) DOCKER_CODENAME="focal" ;;
                        18.04*) DOCKER_CODENAME="bionic" ;;
                    esac
                fi
                ;;
            debian)
                DOCKER_DISTRO="debian"
                DOCKER_CODENAME="${VERSION_CODENAME:-}"
                if [ -z "$DOCKER_CODENAME" ] && [ -n "${VERSION_ID:-}" ]; then
                    case "$VERSION_ID" in
                        13*) DOCKER_CODENAME="trixie" ;;   # Debian testing/next (best-effort)
                        12*) DOCKER_CODENAME="bookworm" ;;
                        11*) DOCKER_CODENAME="bullseye" ;;
                        10*) DOCKER_CODENAME="buster" ;;
                        9*) DOCKER_CODENAME="stretch" ;;
                    esac
                fi
                ;;
            linuxmint)
                # Linux Mint uses Ubuntu repos - map to Ubuntu base
                DOCKER_DISTRO="ubuntu"
                # Mint 21.x = Ubuntu 22.04 (jammy), Mint 20.x = Ubuntu 20.04 (focal)
                case "${VERSION_ID%%.*}" in
                    21) DOCKER_CODENAME="jammy" ;;
                    20) DOCKER_CODENAME="focal" ;;
                    *) DOCKER_CODENAME="focal" ;;
                esac
                log_info "Linux Mint detected - using Ubuntu $DOCKER_CODENAME Docker repo"
                ;;
            *)
                log_fail "Unsupported Debian-family distro: $ID"
                log_info "  Please install Docker manually: https://docs.docker.com/engine/install/"
                return 1
                ;;
        esac
    else
        log_fail "Cannot detect OS version - /etc/os-release not found"
        return 1
    fi

    if [ -z "$DOCKER_CODENAME" ]; then
        log_fail "Cannot determine Debian/Ubuntu codename for Docker repo (VERSION_CODENAME missing)"
        log_info "  Please install Docker manually: https://docs.docker.com/engine/install/"
        return 1
    fi
    
    log_info "Using Docker repo: $DOCKER_DISTRO ($DOCKER_CODENAME)"
    
    # Remove old Docker if present
    apt-get remove -y docker docker-engine docker.io containerd runc 2>/dev/null
    
    # Install prerequisites
    apt-get update
    apt-get install -y ca-certificates curl gnupg
    
    # Add Docker's official GPG key (use correct distro)
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/${DOCKER_DISTRO}/gpg" | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    
    # Add Docker repository (use correct distro and codename)
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${DOCKER_DISTRO} \
      ${DOCKER_CODENAME} stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null
    
    # Install Docker CE
    apt-get update
    if ! apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; then
        log_fail "Docker package installation failed"
        log_info "  This may happen if Docker doesn't support your OS version"
        log_info "  Try manual installation: https://docs.docker.com/engine/install/${DOCKER_DISTRO}/"
        return 1
    fi
    
    # Start and enable Docker
    systemctl start docker
    systemctl enable docker
    
    # Verify
    if docker --version &>/dev/null; then
        log_ok "Docker installed successfully"
        return 0
    else
        log_fail "Docker installation failed"
        log_info "  Check logs: journalctl -u docker"
        return 1
    fi
}

# ============================================================================
# Podman Detection
# ============================================================================
is_podman() {
    # Check if docker command is actually Podman
    if command -v docker &>/dev/null; then
        docker --version 2>/dev/null | grep -qi "podman" && return 0
        docker version 2>/dev/null | grep -qi "podman" && return 0
    fi
    return 1
}

# ============================================================================
# Docker Checks
# ============================================================================
check_docker() {
    if ! command -v docker &>/dev/null; then
        log_fail "Docker not installed"

        local DOCKER_AAVA_DOCS_PATH
        DOCKER_AAVA_DOCS_PATH="$(platform_yaml_get docker.aava_docs || echo "docs/INSTALLATION.md")"
        local DOCKER_AAVA_DOCS_URL
        DOCKER_AAVA_DOCS_URL="$(github_docs_url "$DOCKER_AAVA_DOCS_PATH" 2>/dev/null || echo "https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/main/docs/INSTALLATION.md")"
        
        # Offer to install based on OS family
        if [ "$APPLY_FIXES" = true ]; then
            case "$OS_FAMILY" in
                rhel)
                    install_docker_rhel
                    ;;
                debian)
                    install_docker_debian
                    ;;
                *)
                    log_info "  Install manually: https://docs.docker.com/engine/install/"
                    ;;
            esac
        else
            log_info "  Recommended: sudo ./preflight.sh --apply-fixes"

            local DOCKER_INSTALL_CMD
            DOCKER_INSTALL_CMD="$(platform_yaml_get docker.install_cmd || true)"
            if [ -n "$DOCKER_INSTALL_CMD" ]; then
                print_fix_and_docs "$DOCKER_INSTALL_CMD" "$DOCKER_AAVA_DOCS_URL"
            else
                log_info "  Install manually: https://docs.docker.com/engine/install/"
                print_fix_and_docs "" "$DOCKER_AAVA_DOCS_URL"
            fi
            FIX_CMDS+=("# Docker will be installed automatically with --apply-fixes")
        fi
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
        if [ "$DOCKER_ROOTLESS" = true ]; then
            log_fail "Rootless Docker not running"
            local ROOTLESS_START_CMD
            ROOTLESS_START_CMD="$(platform_yaml_get docker.rootless_start_cmd || echo "systemctl --user start docker")"
            local ROOTLESS_DOCS
            ROOTLESS_DOCS="$(github_docs_url "$(platform_yaml_get docker.rootless_docs || echo "docs/CROSS_PLATFORM_PLAN.md")" 2>/dev/null || echo "https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/main/docs/CROSS_PLATFORM_PLAN.md")"
            MANUAL_CMDS+=("$ROOTLESS_START_CMD")
            print_fix_and_docs "$ROOTLESS_START_CMD" "$ROOTLESS_DOCS"
        else
            # Check if it's a permission issue vs not running
            if sudo docker ps &>/dev/null 2>&1; then
                log_fail "Cannot access Docker daemon (permission denied)"
                local DOCKER_GROUP_CMD
                DOCKER_GROUP_CMD="$(platform_yaml_get docker.user_group_cmd || echo "sudo usermod -aG docker \$USER")"
                MANUAL_CMDS+=("$DOCKER_GROUP_CMD")
                MANUAL_CMDS+=("# Then log out and back in, or run: newgrp docker")
                print_fix_and_docs "$DOCKER_GROUP_CMD" "$(github_docs_url "$(platform_yaml_get docker.aava_docs || echo "docs/INSTALLATION.md")" 2>/dev/null || true)"
            else
                log_fail "Docker daemon not running"
                local DOCKER_START_CMD
                DOCKER_START_CMD="$(platform_yaml_get docker.start_cmd || echo "sudo systemctl start docker")"
                MANUAL_CMDS+=("$DOCKER_START_CMD")
                print_fix_and_docs "$DOCKER_START_CMD" "$(github_docs_url "$(platform_yaml_get docker.aava_docs || echo "docs/INSTALLATION.md")" 2>/dev/null || true)"
            fi
        fi
        return 1
    fi

    # Detect Podman - skip Docker-specific version checks
    if is_podman; then
        PODMAN_VERSION=$(docker --version 2>/dev/null | sed -n 's/.*podman version \([0-9.]*\).*/\1/ip' || echo "unknown")
        [ -z "$PODMAN_VERSION" ] && PODMAN_VERSION="unknown"
        log_warn "Podman detected (version $PODMAN_VERSION) - Docker checks skipped"
        log_info "  Podman compatibility is community-supported"
        log_info "  Some Docker-specific features may not work as expected"
        log_info "  If you encounter issues, consider using Docker instead"
        # Skip version checks for Podman
        return 0
    fi

    # Version check (HARD FAIL below minimum)
    DOCKER_VERSION=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "0.0.0")
    DOCKER_MAJOR=$(echo "$DOCKER_VERSION" | cut -d. -f1)

    if [ "$DOCKER_MAJOR" -lt 20 ]; then
        log_fail "Docker $DOCKER_VERSION too old (minimum: 20.10) - upgrade required"
        local DOCKER_INSTALL_CMD
        DOCKER_INSTALL_CMD="$(platform_yaml_get docker.install_cmd || true)"
        print_fix_and_docs "$DOCKER_INSTALL_CMD" "$(github_docs_url "$(platform_yaml_get docker.aava_docs || echo "docs/INSTALLATION.md")" 2>/dev/null || echo "https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/main/docs/INSTALLATION.md")"
    elif [ "$DOCKER_MAJOR" -lt 25 ]; then
        log_warn "Docker $DOCKER_VERSION supported but upgrade to 25.x+ recommended"
    else
        log_ok "Docker: $DOCKER_VERSION"
    fi
    
    if [ "$DOCKER_ROOTLESS" = true ]; then
        log_ok "Docker mode: rootless"
        local ROOTLESS_SOCKET="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/docker.sock"
        local ROOTLESS_DOCS
        ROOTLESS_DOCS="$(github_docs_url "$(platform_yaml_get docker.rootless_docs || echo "docs/CROSS_PLATFORM_PLAN.md")" 2>/dev/null || true)"
        log_info "  Admin UI (rootless) tip:"
        log_info "    export DOCKER_SOCK=$ROOTLESS_SOCKET"
        log_info "    ${COMPOSE_CMD:-docker compose} -p asterisk-ai-voice-agent up -d --force-recreate admin_ui"
        [ -n "$ROOTLESS_DOCS" ] && log_info "    Docs: $ROOTLESS_DOCS"
    fi
}

# ============================================================================
# Docker Socket GID (Admin UI needs group_add)
# ============================================================================
check_docker_gid() {
    # Best-effort: only relevant when the Admin UI mounts a Docker socket.
    # docker-compose.yml uses:
    #   - ${DOCKER_SOCK:-/var/run/docker.sock}:/var/run/docker.sock
    #   group_add: [${DOCKER_GID:-999}]

    # Prefer .env-configured socket path; fall back to default.
    local sock
    sock="$(env_get "DOCKER_SOCK" "/var/run/docker.sock")"
    sock="$(echo "$sock" | tr -d '\r' | xargs 2>/dev/null || echo "$sock")"

    if [ -z "$sock" ]; then
        sock="/var/run/docker.sock"
    fi

    if [ ! -S "$sock" ]; then
        # On some hosts (or rootless), the socket may not exist until docker starts.
        # If docker is available, we already checked docker ps; so missing socket likely means
        # the user isn't using socket-mount control (or is on an unsupported layout).
        return 0
    fi

    local actual_gid
    actual_gid="$(stat_gid "$sock")"
    if ! [[ "$actual_gid" =~ ^[0-9]+$ ]]; then
        log_warn "Could not determine Docker socket GID: $sock"
        return 0
    fi

    local configured_gid
    configured_gid="$(env_get "DOCKER_GID" "")"
    if [ -z "$configured_gid" ]; then
        configured_gid="999"
    fi

    if ! [[ "$configured_gid" =~ ^[0-9]+$ ]]; then
        log_warn "DOCKER_GID in .env is not numeric: $configured_gid"
        return 0
    fi

    if [ "$configured_gid" != "$actual_gid" ]; then
        log_warn "Docker socket GID mismatch (Admin UI may not control containers)"
        log_info "  Socket: $sock (gid=$actual_gid)"
        log_info "  .env: DOCKER_GID=$configured_gid"
        log_info "  Fix: set DOCKER_GID=$actual_gid in .env and recreate admin_ui"

        if [ -f "$SCRIPT_DIR/.env" ]; then
            if [ "$APPLY_FIXES" = true ]; then
                if grep -qE '^[# ]*DOCKER_GID=' "$SCRIPT_DIR/.env"; then
                    sed -i.bak "s/^[# ]*DOCKER_GID=.*/DOCKER_GID=${actual_gid}/" "$SCRIPT_DIR/.env" 2>/dev/null || \
                        sed -i '' "s/^[# ]*DOCKER_GID=.*/DOCKER_GID=${actual_gid}/" "$SCRIPT_DIR/.env"
                else
                    echo "" >> "$SCRIPT_DIR/.env"
                    echo "DOCKER_GID=${actual_gid}" >> "$SCRIPT_DIR/.env"
                fi
                rm -f "$SCRIPT_DIR/.env.bak" 2>/dev/null || true
                log_ok "Updated .env with DOCKER_GID=$actual_gid"
                log_info "  Recreate admin_ui: ${COMPOSE_CMD:-docker compose} -p asterisk-ai-voice-agent up -d --force-recreate admin_ui"
            else
                FIX_CMDS+=("# Update .env with: DOCKER_GID=$actual_gid  (docker socket: $sock)")
                FIX_CMDS+=("# Then recreate admin_ui:")
                FIX_CMDS+=("${COMPOSE_CMD:-docker compose} -p asterisk-ai-voice-agent up -d --force-recreate admin_ui")
            fi
        fi
    else
        log_ok "Docker socket GID: $actual_gid (matches DOCKER_GID)"
    fi
}

# ============================================================================
# Docker Compose Checks
# ============================================================================
check_compose() {
    COMPOSE_CMD=""
    COMPOSE_VER=""
    local COMPOSE_AAVA_DOCS_URL
    COMPOSE_AAVA_DOCS_URL="$(github_docs_url "$(platform_yaml_get compose.aava_docs || echo "docs/INSTALLATION.md")" 2>/dev/null || echo "https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/main/docs/INSTALLATION.md")"
    
    if docker compose version &>/dev/null 2>&1; then
        COMPOSE_CMD="docker compose"
        COMPOSE_VER=$(docker compose version --short 2>/dev/null | sed 's/^v//')
        
        # Create docker-compose wrapper if it doesn't exist (needed for Admin UI)
        if ! command -v docker-compose &>/dev/null; then
            if [ "$APPLY_FIXES" = true ]; then
                # Remove if it's a directory (broken state)
                [ -d /usr/local/bin/docker-compose ] && rm -rf /usr/local/bin/docker-compose
                
                echo '#!/bin/bash
docker compose "$@"' > /usr/local/bin/docker-compose
                chmod +x /usr/local/bin/docker-compose
                log_ok "Created docker-compose wrapper for compatibility"
            else
                log_warn "docker-compose command not found (Admin UI needs this)"
                FIX_CMDS+=("echo '#!/bin/bash\ndocker compose \"\$@\"' > /usr/local/bin/docker-compose && chmod +x /usr/local/bin/docker-compose")
                log_info "  Docs: $COMPOSE_AAVA_DOCS_URL"
            fi
        fi
    elif command -v docker-compose &>/dev/null; then
        COMPOSE_CMD="docker-compose"
        local compose_raw
        compose_raw="$(docker-compose version --short 2>/dev/null || true)"
        COMPOSE_VER="$(echo "$compose_raw" | sed 's/^v//')"

        # docker-compose may be either v1 (EOL) or v2 standalone binary.
        # Only hard-fail on v1.
        if [[ "$compose_raw" =~ ^v?2\. ]]; then
            # v2 standalone binary - OK. Version validation happens below.
            :
        else
            log_fail "Docker Compose v1 detected - EOL July 2023, security risk"

            # Manual install works on all distros (including Sangoma/FreePBX)
            local MANUAL_COMPOSE_V2_CMD
            MANUAL_COMPOSE_V2_CMD=$'sudo curl -L \"https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64\" -o /usr/local/bin/docker-compose\nsudo chmod +x /usr/local/bin/docker-compose\nsudo mkdir -p /usr/local/lib/docker/cli-plugins\nsudo ln -sf /usr/local/bin/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose'
            print_fix_and_docs "$MANUAL_COMPOSE_V2_CMD" "$COMPOSE_AAVA_DOCS_URL"

            # Add to FIX_CMDS for --apply-fixes
            FIX_CMDS+=("sudo curl -L 'https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64' -o /usr/local/bin/docker-compose && sudo chmod +x /usr/local/bin/docker-compose && sudo mkdir -p /usr/local/lib/docker/cli-plugins && sudo ln -sf /usr/local/bin/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose")
            return 1
        fi
    fi
    
    if [ -z "$COMPOSE_CMD" ]; then
        log_fail "Docker Compose not found"
        local MANUAL_COMPOSE_V2_CMD
        MANUAL_COMPOSE_V2_CMD=$'sudo curl -L \"https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64\" -o /usr/local/bin/docker-compose\nsudo chmod +x /usr/local/bin/docker-compose\nsudo mkdir -p /usr/local/lib/docker/cli-plugins\nsudo ln -sf /usr/local/bin/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose'
        print_fix_and_docs "$MANUAL_COMPOSE_V2_CMD" "$COMPOSE_AAVA_DOCS_URL"
        
        FIX_CMDS+=("sudo curl -L 'https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64' -o /usr/local/bin/docker-compose && sudo chmod +x /usr/local/bin/docker-compose && sudo mkdir -p /usr/local/lib/docker/cli-plugins && sudo ln -sf /usr/local/bin/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose")
        return 1
    fi
    
    # Parse version (e.g., "2.20.0" -> major=2, minor=20)
    COMPOSE_MAJOR=$(echo "$COMPOSE_VER" | cut -d. -f1)
    COMPOSE_MINOR=$(echo "$COMPOSE_VER" | cut -d. -f2)

    # Validate that version components are numeric before comparison
    if [[ "$COMPOSE_MAJOR" =~ ^[0-9]+$ ]] && [[ "$COMPOSE_MINOR" =~ ^[0-9]+$ ]]; then
        if [ "$COMPOSE_MAJOR" -eq 2 ] && [ "$COMPOSE_MINOR" -lt 20 ]; then
            log_warn "Compose $COMPOSE_VER - upgrade to 2.20+ recommended (missing profiles, watch)"
            log_info "  Docs: $COMPOSE_AAVA_DOCS_URL"
        else
            log_ok "Docker Compose: $COMPOSE_VER"
        fi
    else
        # Non-standard version (e.g., "dev") - skip validation
        log_warn "Docker Compose version non-standard: $COMPOSE_VER"
        log_info "  Skipping version check - ensure you have Compose 2.20+ features"
        log_info "  Docs: $COMPOSE_AAVA_DOCS_URL"
    fi
    
    # Check buildx version (required >= 0.17 for compose build)
    if docker buildx version &>/dev/null 2>&1; then
        BUILDX_VER=$(docker buildx version 2>/dev/null | grep -oP 'v?\K[0-9]+\.[0-9]+' | head -1)
        BUILDX_MAJOR=$(echo "$BUILDX_VER" | cut -d. -f1)
        BUILDX_MINOR=$(echo "$BUILDX_VER" | cut -d. -f2)

        # Validate that version components are numeric before comparison
        if [[ "$BUILDX_MAJOR" =~ ^[0-9]+$ ]] && [[ "$BUILDX_MINOR" =~ ^[0-9]+$ ]]; then
            if [ "$BUILDX_MAJOR" -eq 0 ] && [ "$BUILDX_MINOR" -lt 17 ]; then
                log_warn "Docker Buildx $BUILDX_VER - requires 0.17+ for compose build"
                log_info "  Fix: mkdir -p /usr/local/lib/docker/cli-plugins && curl -L https://github.com/docker/buildx/releases/download/v0.17.1/buildx-v0.17.1.linux-amd64 -o /usr/local/lib/docker/cli-plugins/docker-buildx && chmod +x /usr/local/lib/docker/cli-plugins/docker-buildx"
                log_info "  Docs: $COMPOSE_AAVA_DOCS_URL"
                FIX_CMDS+=("mkdir -p /usr/local/lib/docker/cli-plugins && curl -L https://github.com/docker/buildx/releases/download/v0.17.1/buildx-v0.17.1.linux-amd64 -o /usr/local/lib/docker/cli-plugins/docker-buildx && chmod +x /usr/local/lib/docker/cli-plugins/docker-buildx")
            else
                log_ok "Docker Buildx: $BUILDX_VER"
            fi
        elif [ -n "$BUILDX_VER" ]; then
            # Version detected but non-standard format
            log_warn "Docker Buildx version non-standard: $BUILDX_VER"
            log_info "  Skipping version check - ensure you have Buildx 0.17+ features"
        fi
    fi
}

# ============================================================================
# Directory Setup
# ============================================================================
check_directories() {
    # Host paths (repo-local). These are bind-mounted into containers.
    #
    # NOTE: AST_MEDIA_DIR is a *container* path (e.g., /mnt/asterisk_media/ai-generated).
    # Preflight must not use it as a host path.
    local MEDIA_PARENT="$SCRIPT_DIR/asterisk_media"
    local MEDIA_DIR_HOST="$SCRIPT_DIR/asterisk_media/ai-generated"
    local MEDIA_DIR_CONTAINER="${AST_MEDIA_DIR:-/mnt/asterisk_media/ai-generated}"
    local DATA_DIR="$SCRIPT_DIR/data"
    local MODELS_DIR="$SCRIPT_DIR/models"
    local SHARED_GID
    SHARED_GID="$(choose_shared_gid)"
    local CONTAINER_UID="${CONTAINER_UID_DEFAULT}"
    
    # Check media directory
    if [ -d "$MEDIA_DIR_HOST" ]; then
        local media_uid media_gid media_mode parent_uid parent_gid parent_mode
        parent_uid="$(stat_uid "$MEDIA_PARENT")"
        parent_gid="$(stat_gid "$MEDIA_PARENT")"
        parent_mode="$(stat_mode "$MEDIA_PARENT")"
        media_uid="$(stat_uid "$MEDIA_DIR_HOST")"
        media_gid="$(stat_gid "$MEDIA_DIR_HOST")"
        media_mode="$(stat_mode "$MEDIA_DIR_HOST")"

        local media_ok=true
        # Log-to-file often targets /mnt/asterisk_media/*.log, so the parent directory must be writable too.
        if [ "$parent_uid" != "$CONTAINER_UID" ]; then
            media_ok=false
        fi
        if [[ "$parent_mode" =~ ^[0-9]+$ ]]; then
            local parent_base_mode="${parent_mode: -3}"
            if [[ "$parent_base_mode" =~ ^[0-9]{3}$ ]]; then
                local parent_base_oct=$((8#$parent_base_mode))
                if [ $((parent_base_oct & 0020)) -eq 0 ]; then
                    media_ok=false
                fi
            fi
        fi
        if [ "$media_uid" != "$CONTAINER_UID" ]; then
            media_ok=false
        fi
        # Require group-writable; setgid recommended to keep group inheritance stable.
        if [[ "$media_mode" =~ ^[0-9]+$ ]]; then
            local base_mode="${media_mode: -3}"
            if [[ "$base_mode" =~ ^[0-9]{3}$ ]]; then
                local base_oct=$((8#$base_mode))
                if [ $((base_oct & 0020)) -eq 0 ]; then
                    media_ok=false
                fi
            fi
        fi

        if [ "$media_ok" = true ]; then
            log_ok "Media directory (host): $MEDIA_DIR_HOST"
            log_info "  Container path: $MEDIA_DIR_CONTAINER"
        else
            log_warn "Media directory permissions need alignment (host): $MEDIA_DIR_HOST"
            log_info "  Expected: owner UID $CONTAINER_UID, group-writable (recommended: 2775), group GID $SHARED_GID"
            log_info "  Parent: uid=${parent_uid:-unknown} gid=${parent_gid:-unknown} mode=${parent_mode:-unknown} path=$MEDIA_PARENT"
            log_info "  Detected: uid=${media_uid:-unknown} gid=${media_gid:-unknown} mode=${media_mode:-unknown}"
            log_info "  Container path: $MEDIA_DIR_CONTAINER"

            FIX_CMDS+=("sudo chown -R $CONTAINER_UID:$SHARED_GID $SCRIPT_DIR/asterisk_media")
            FIX_CMDS+=("sudo chmod 2775 $SCRIPT_DIR/asterisk_media $MEDIA_DIR_HOST")
        fi
    else
        log_warn "Media directory missing (host): $MEDIA_DIR_HOST"
        log_info "  Container path: $MEDIA_DIR_CONTAINER"

        FIX_CMDS+=("mkdir -p $MEDIA_DIR_HOST")
        FIX_CMDS+=("sudo chown -R $CONTAINER_UID:$SHARED_GID $SCRIPT_DIR/asterisk_media")
        FIX_CMDS+=("sudo chmod 2775 $SCRIPT_DIR/asterisk_media $MEDIA_DIR_HOST")
        if [ "$DOCKER_ROOTLESS" = true ]; then
            log_info "  Rootless tip: If you cannot chown to UID $CONTAINER_UID, run containers as your host UID or use a named volume"
        fi
    fi
    
    # Check data directory (for call history SQLite DB)
    if [ -d "$DATA_DIR" ]; then
        local data_uid data_gid data_mode
        data_uid="$(stat_uid "$DATA_DIR")"
        data_gid="$(stat_gid "$DATA_DIR")"
        data_mode="$(stat_mode "$DATA_DIR")"

        local data_ok=true
        if [ "$data_uid" != "$CONTAINER_UID" ]; then
            data_ok=false
        fi
        if [[ "$data_mode" =~ ^[0-9]+$ ]]; then
            local base_mode="${data_mode: -3}"
            if [[ "$base_mode" =~ ^[0-9]{3}$ ]]; then
                local base_oct=$((8#$base_mode))
                if [ $((base_oct & 0020)) -eq 0 ]; then
                    data_ok=false
                fi
            fi
        fi

        if [ "$data_ok" = true ]; then
            log_ok "Data directory (host): $DATA_DIR"
        else
            log_warn "Data directory permissions need alignment (host): $DATA_DIR"
            log_info "  Expected: owner UID $CONTAINER_UID, group-writable (recommended: 2775), group GID $SHARED_GID"
            log_info "  Detected: uid=${data_uid:-unknown} gid=${data_gid:-unknown} mode=${data_mode:-unknown}"
            FIX_CMDS+=("sudo chown -R $CONTAINER_UID:$SHARED_GID $DATA_DIR")
            FIX_CMDS+=("sudo chmod 2775 $DATA_DIR")
        fi
        # Best-effort: validate we can create an SQLite file inside the data directory.
        # Avoid touching the real call_history.db here; use a temp file and delete it.
        if command -v python3 &>/dev/null; then
            if python3 - "$DATA_DIR" <<'PY' 2>/dev/null; then
import os, sqlite3, sys
data_dir = sys.argv[1]
path = os.path.join(data_dir, ".call_history_sqlite_test.db")
conn = sqlite3.connect(path, timeout=1.0)
conn.execute("CREATE TABLE IF NOT EXISTS __preflight_test (id INTEGER PRIMARY KEY)")
conn.commit()
conn.close()
os.remove(path)
PY
                log_ok "Call history DB: writable (SQLite test passed)"
            else
                log_warn "Call history DB: may fail (SQLite file test failed)"
                log_info "  If call history fails at runtime, check container logs for: 'Failed to initialize call history database'"
                log_info "  Common causes: permissions, SELinux contexts, or non-local filesystems that break SQLite locking"
            fi
        fi
    else
        if [ "$APPLY_FIXES" = true ]; then
            mkdir -p "$DATA_DIR"
            chmod 2775 "$DATA_DIR"
            # Ensure .gitkeep exists to maintain directory in git
            touch "$DATA_DIR/.gitkeep"
            log_ok "Created data directory: $DATA_DIR"
        else
            log_warn "Data directory missing: $DATA_DIR"
            log_info "  ⚠️  Call history will NOT be recorded without this directory!"
            log_info "  Run: ./preflight.sh --apply-fixes to create it automatically"
            FIX_CMDS+=("mkdir -p $DATA_DIR && chmod 2775 $DATA_DIR && touch $DATA_DIR/.gitkeep")
        fi
    fi

    # Check models directory (required for local/local_hybrid pipelines and Admin UI model downloads).
    # The Admin UI runs as UID 1000 and must be able to create models/{stt,tts,llm,kroko}.
    local MODEL_SUBDIRS=("stt" "tts" "llm" "kroko")

    if [ -d "$MODELS_DIR" ]; then
        local models_uid models_gid models_mode
        models_uid="$(stat_uid "$MODELS_DIR")"
        models_gid="$(stat_gid "$MODELS_DIR")"
        models_mode="$(stat_mode "$MODELS_DIR")"

        local models_ok=true
        if [ "$models_uid" != "$CONTAINER_UID" ]; then
            models_ok=false
        fi
        if [[ "$models_mode" =~ ^[0-9]+$ ]]; then
            local base_mode="${models_mode: -3}"
            if [[ "$base_mode" =~ ^[0-9]{3}$ ]]; then
                local base_oct=$((8#$base_mode))
                if [ $((base_oct & 0020)) -eq 0 ]; then
                    models_ok=false
                fi
            fi
        fi

        # Ensure expected subdirectories exist (best-effort).
        local missing_sub=()
        for sub in "${MODEL_SUBDIRS[@]}"; do
            if [ ! -d "$MODELS_DIR/$sub" ]; then
                missing_sub+=("$MODELS_DIR/$sub")
            fi
        done

        if [ "$models_ok" = true ] && [ ${#missing_sub[@]} -eq 0 ]; then
            log_ok "Models directory (host): $MODELS_DIR"
        else
            log_warn "Models directory not ready for local AI downloads (host): $MODELS_DIR"
            log_info "  Expected: owner UID $CONTAINER_UID, group-writable (recommended: 2775), group GID $SHARED_GID"
            log_info "  Detected: uid=${models_uid:-unknown} gid=${models_gid:-unknown} mode=${models_mode:-unknown}"
            if [ ${#missing_sub[@]} -gt 0 ]; then
                log_info "  Missing: ${missing_sub[*]}"
            fi
            FIX_CMDS+=("sudo mkdir -p $MODELS_DIR/stt $MODELS_DIR/tts $MODELS_DIR/llm $MODELS_DIR/kroko")
            FIX_CMDS+=("sudo chown $CONTAINER_UID:$SHARED_GID $MODELS_DIR $MODELS_DIR/stt $MODELS_DIR/tts $MODELS_DIR/llm $MODELS_DIR/kroko")
            FIX_CMDS+=("sudo chmod 2775 $MODELS_DIR $MODELS_DIR/stt $MODELS_DIR/tts $MODELS_DIR/llm $MODELS_DIR/kroko")
        fi
    else
        if [ "$APPLY_FIXES" = true ]; then
            mkdir -p "$MODELS_DIR"/stt "$MODELS_DIR"/tts "$MODELS_DIR"/llm "$MODELS_DIR"/kroko
            chown "$CONTAINER_UID:$SHARED_GID" "$MODELS_DIR" "$MODELS_DIR"/stt "$MODELS_DIR"/tts "$MODELS_DIR"/llm "$MODELS_DIR"/kroko 2>/dev/null || true
            chmod 2775 "$MODELS_DIR" "$MODELS_DIR"/stt "$MODELS_DIR"/tts "$MODELS_DIR"/llm "$MODELS_DIR"/kroko 2>/dev/null || true
            log_ok "Created models directories: $MODELS_DIR/{stt,tts,llm,kroko}"
        else
            log_warn "Models directory missing: $MODELS_DIR"
            log_info "  Local AI setup (and Admin UI model downloads) will fail without this directory."
            log_info "  Run: sudo ./preflight.sh --apply-fixes to create it automatically"
            FIX_CMDS+=("mkdir -p $MODELS_DIR/stt $MODELS_DIR/tts $MODELS_DIR/llm $MODELS_DIR/kroko && chown $CONTAINER_UID:$SHARED_GID $MODELS_DIR $MODELS_DIR/stt $MODELS_DIR/tts $MODELS_DIR/llm $MODELS_DIR/kroko && chmod 2775 $MODELS_DIR $MODELS_DIR/stt $MODELS_DIR/tts $MODELS_DIR/llm $MODELS_DIR/kroko")
        fi
    fi
}

# ============================================================================
# Project Directory Permissions (Admin UI needs to write .env + config)
# ============================================================================
check_project_permissions() {
    local SHARED_GID
    SHARED_GID="$(choose_shared_gid)"
    local CONTAINER_UID="${CONTAINER_UID_DEFAULT}"

    # Admin UI edits .env and config/ai-agent.yaml via atomic temp-file + rename.
    # That requires the containing directories to be writable by the container UID.
    local dirs=("$SCRIPT_DIR" "$SCRIPT_DIR/config")
    for d in "${dirs[@]}"; do
        [ -d "$d" ] || continue
        local uid mode
        uid="$(stat_uid "$d")"
        mode="$(stat_mode "$d")"

        local needs_fix=false
        if [ "$uid" != "$CONTAINER_UID" ]; then
            needs_fix=true
        fi
        if [[ "$mode" =~ ^[0-9]+$ ]]; then
            local base_mode="${mode: -3}"
            if [[ "$base_mode" =~ ^[0-9]{3}$ ]]; then
                local base_oct=$((8#$base_mode))
                # require owner write
                if [ $((base_oct & 0200)) -eq 0 ]; then
                    needs_fix=true
                fi
            fi
        fi

        if [ "$needs_fix" = true ]; then
            log_warn "Project directory not writable by containers (host): $d"
            log_info "  Required so Admin UI can save .env and config changes"
            log_info "  Expected: owner UID $CONTAINER_UID, mode 2775 (recommended), group GID $SHARED_GID"
            log_info "  Detected: uid=${uid:-unknown} mode=${mode:-unknown}"
            FIX_CMDS+=("sudo chown $CONTAINER_UID:$SHARED_GID $d")
            FIX_CMDS+=("sudo chmod 2775 $d")
        fi
    done
}

# ============================================================================
# Data Directory Permissions (AAVA-150)
# Always runs regardless of Asterisk location - fixes remote Asterisk case
# ============================================================================
check_data_permissions() {
    local DATA_DIR="$SCRIPT_DIR/data"
    local SHARED_GID
    SHARED_GID="$(choose_shared_gid)"
    local CONTAINER_UID="${CONTAINER_UID_DEFAULT}"
    
    # Skip if data directory doesn't exist yet (check_directories handles creation)
    [ ! -d "$DATA_DIR" ] && return 0

    # Ensure directory itself is writable by containers (ai_engine + admin_ui run as UID 1000).
    local dir_uid dir_gid dir_mode
    dir_uid="$(stat_uid "$DATA_DIR")"
    dir_gid="$(stat_gid "$DATA_DIR")"
    dir_mode="$(stat_mode "$DATA_DIR")"

    local dir_needs_fix=false
    if [ "$dir_uid" != "$CONTAINER_UID" ]; then
        dir_needs_fix=true
    fi
    if [[ "$dir_mode" =~ ^[0-9]+$ ]]; then
        local base_mode="${dir_mode: -3}"
        if [[ "$base_mode" =~ ^[0-9]{3}$ ]]; then
            local base_oct=$((8#$base_mode))
            if [ $((base_oct & 0020)) -eq 0 ]; then
                dir_needs_fix=true
            fi
        fi
    fi

    if [ "$dir_needs_fix" = true ]; then
        log_warn "Data directory not aligned for containers (host): $DATA_DIR"
        log_info "  Expected: owner UID $CONTAINER_UID, group-writable (recommended: 2775), group GID $SHARED_GID"
        log_info "  Detected: uid=${dir_uid:-unknown} gid=${dir_gid:-unknown} mode=${dir_mode:-unknown}"
        if [ "$APPLY_FIXES" = true ]; then
            if sudo chown "$CONTAINER_UID:$SHARED_GID" "$DATA_DIR" 2>/dev/null && sudo chmod 2775 "$DATA_DIR" 2>/dev/null; then
                log_ok "Fixed data directory permissions for containers"
            else
                log_warn "Could not fix data directory permissions (may need sudo)"
            fi
        else
            FIX_CMDS+=("sudo chown $CONTAINER_UID:$SHARED_GID $DATA_DIR")
            FIX_CMDS+=("sudo chmod 2775 $DATA_DIR")
        fi
    fi

    # Ensure call history DB files are owned by the container runtime UID (not root) and group-readable.
    local db_files=("$DATA_DIR/call_history.db" "$DATA_DIR/call_history.db-wal" "$DATA_DIR/call_history.db-shm")
    local db_needs_fix=false
    for db_file in "${db_files[@]}"; do
        if [ -f "$db_file" ]; then
            local owner_uid
            owner_uid="$(stat_uid "$db_file")"
            if [ "$owner_uid" = "0" ] || [ "$owner_uid" != "$CONTAINER_UID" ]; then
                db_needs_fix=true
                break
            fi
        fi
    done

    if [ "$db_needs_fix" = true ]; then
        log_warn "call_history.db ownership/permissions may block writes by containers"
        log_info "  Fix: run ./preflight.sh --apply-fixes (preferred). Avoid chmod 666/777."
        if [ "$APPLY_FIXES" = true ]; then
            local fix_success=true
            for db_file in "${db_files[@]}"; do
                if [ -f "$db_file" ]; then
                    if sudo chown "$CONTAINER_UID:$SHARED_GID" "$db_file" 2>/dev/null && sudo chmod 664 "$db_file" 2>/dev/null; then
                        log_ok "Fixed: $db_file"
                    else
                        log_warn "Could not fix (may need sudo): $db_file"
                        fix_success=false
                    fi
                fi
            done
            if [ "$fix_success" = true ]; then
                log_ok "call_history.db ownership/permissions aligned for containers"
            fi
        else
            FIX_CMDS+=("sudo chown $CONTAINER_UID:$SHARED_GID $DATA_DIR/call_history.db*")
            FIX_CMDS+=("sudo chmod 664 $DATA_DIR/call_history.db*")
        fi
    else
        if [ -f "$DATA_DIR/call_history.db" ]; then
            log_ok "call_history.db ownership: OK (writable by containers)"
        fi
    fi
}

# ============================================================================
# SELinux (RHEL family)
# ============================================================================
check_selinux() {
    [ "$OS_FAMILY" != "rhel" ] && return 0
    command -v getenforce &>/dev/null || return 0
    
    SELINUX_MODE=$(getenforce 2>/dev/null || echo "Disabled")
    # SELinux contexts apply to host paths (repo-local), not container mount paths.
    MEDIA_DIR="$SCRIPT_DIR/asterisk_media/ai-generated"
    DATA_DIR="$SCRIPT_DIR/data"
    
    if [ "$SELINUX_MODE" = "Enforcing" ]; then
        # Check if semanage is available
        if ! command -v semanage &>/dev/null; then
            log_warn "SELinux: Enforcing but semanage not installed"
            local SELINUX_TOOLS_CMD
            SELINUX_TOOLS_CMD="$(platform_yaml_get selinux.tools_install_cmd || true)"
            if [ -z "$SELINUX_TOOLS_CMD" ]; then
                # Use dnf or yum based on availability
                if command -v dnf &>/dev/null; then
                    SELINUX_TOOLS_CMD="dnf install -y policycoreutils-python-utils"
                else
                    SELINUX_TOOLS_CMD="yum install -y policycoreutils-python-utils"
                fi
            fi
            FIX_CMDS+=("$SELINUX_TOOLS_CMD")
            print_fix_and_docs "$SELINUX_TOOLS_CMD" "$(github_docs_url "$(platform_yaml_get selinux.aava_docs || echo "docs/INSTALLATION.md")" 2>/dev/null || true)"
        fi
        
        log_warn "SELinux: Enforcing (context fix may be needed for media and data directories)"
        local SELINUX_CONTEXT_CMD
        local SELINUX_RESTORE_CMD
        
        # Media directory SELinux context
        SELINUX_CONTEXT_CMD="$(platform_yaml_get selinux.context_cmd || echo "sudo semanage fcontext -a -t container_file_t '{path}(/.*)?'")"
        SELINUX_RESTORE_CMD="$(platform_yaml_get selinux.restore_cmd || echo "sudo restorecon -Rv {path}")"
        local MEDIA_CONTEXT_CMD="${SELINUX_CONTEXT_CMD//\{path\}/$MEDIA_DIR}"
        local MEDIA_RESTORE_CMD="${SELINUX_RESTORE_CMD//\{path\}/$MEDIA_DIR}"
        FIX_CMDS+=("$MEDIA_CONTEXT_CMD")
        FIX_CMDS+=("$MEDIA_RESTORE_CMD")
        
        # Data directory SELinux context (for call history DB)
        local DATA_CONTEXT_CMD="${SELINUX_CONTEXT_CMD//\{path\}/$DATA_DIR}"
        local DATA_RESTORE_CMD="${SELINUX_RESTORE_CMD//\{path\}/$DATA_DIR}"
        FIX_CMDS+=("$DATA_CONTEXT_CMD")
        FIX_CMDS+=("$DATA_RESTORE_CMD")
        
        log_info "  Media directory: $MEDIA_DIR"
        log_info "  Data directory: $DATA_DIR (call history)"
        print_fix_and_docs "$MEDIA_CONTEXT_CMD"$'\n'"$MEDIA_RESTORE_CMD"$'\n'"$DATA_CONTEXT_CMD"$'\n'"$DATA_RESTORE_CMD" "$(github_docs_url "$(platform_yaml_get selinux.aava_docs || echo "docs/INSTALLATION.md")" 2>/dev/null || true)"
    else
        log_ok "SELinux: $SELINUX_MODE"
    fi
}

# ============================================================================
# Environment File
# ============================================================================
check_env() {
    if [ -f "$SCRIPT_DIR/.env" ]; then
        log_ok ".env file exists"
        log_info "  Tip: For local_only pipeline, no API keys needed!"
    elif [ -f "$SCRIPT_DIR/.env.example" ]; then
        # Creating .env is repo-local and safe; do it automatically (no sudo needed).
        cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
        log_ok "Created .env from .env.example"
        log_info "  Tip: For local_only pipeline, no API keys needed!"
        log_info "  For cloud providers, edit .env to add your API keys"
    else
        log_warn ".env.example not found"
    fi

    if [ -n "$LOCAL_AI_MODE_OVERRIDE" ] && [ -f "$SCRIPT_DIR/.env" ]; then
        local mode
        mode="$(echo "$LOCAL_AI_MODE_OVERRIDE" | tr '[:upper:]' '[:lower:]' | tr -d '\r' | xargs 2>/dev/null || echo "$LOCAL_AI_MODE_OVERRIDE")"
        if [ "$mode" != "full" ] && [ "$mode" != "minimal" ]; then
            log_warn "Invalid --local-ai-mode value: $LOCAL_AI_MODE_OVERRIDE (expected full|minimal)"
        else
            if grep -qE '^[# ]*LOCAL_AI_MODE=' "$SCRIPT_DIR/.env"; then
                sed -i.bak "s/^[# ]*LOCAL_AI_MODE=.*/LOCAL_AI_MODE=${mode}/" "$SCRIPT_DIR/.env" 2>/dev/null || \
                    sed -i '' "s/^[# ]*LOCAL_AI_MODE=.*/LOCAL_AI_MODE=${mode}/" "$SCRIPT_DIR/.env"
            else
                echo "" >> "$SCRIPT_DIR/.env"
                echo "LOCAL_AI_MODE=${mode}" >> "$SCRIPT_DIR/.env"
            fi
            rm -f "$SCRIPT_DIR/.env.bak" 2>/dev/null || true
            log_ok "Set LOCAL_AI_MODE=${mode} in .env"
            log_info "  Recreate local_ai_server container to apply .env changes"
        fi
    fi

    # Ensure JWT_SECRET is non-empty when Admin UI is remotely accessible by default.
    # This is a repo-local change and safe to apply automatically.
    if [ -f "$SCRIPT_DIR/.env" ]; then
        local current_secret
        current_secret="$(grep -E '^[# ]*JWT_SECRET=' "$SCRIPT_DIR/.env" | tail -n1 | sed -E 's/^[# ]*JWT_SECRET=//')"
        current_secret="$(echo "$current_secret" | tr -d '\r' | xargs 2>/dev/null || echo "$current_secret")"

        if [ -z "$current_secret" ] || [ "$current_secret" = "change-me-please" ] || [ "$current_secret" = "changeme" ]; then
            local new_secret=""
            if command -v openssl >/dev/null 2>&1; then
                new_secret="$(openssl rand -hex 32 2>/dev/null || true)"
            fi
            if [ -z "$new_secret" ] && command -v python3 >/dev/null 2>&1; then
                new_secret="$(python3 -c 'import secrets; print(secrets.token_hex(32))' 2>/dev/null || true)"
            fi
            if [ -z "$new_secret" ] && command -v shasum >/dev/null 2>&1; then
                new_secret="$(date +%s%N 2>/dev/null | shasum -a 256 | awk '{print $1}' | cut -c1-64)"
            fi
            if [ -z "$new_secret" ] && command -v sha256sum >/dev/null 2>&1; then
                new_secret="$(date +%s%N 2>/dev/null | sha256sum | awk '{print $1}' | cut -c1-64)"
            fi

            if [ -n "$new_secret" ]; then
                # Upsert JWT_SECRET in .env (portable sed: GNU + BSD)
                if grep -qE '^[# ]*JWT_SECRET=' "$SCRIPT_DIR/.env"; then
                    sed -i.bak "s/^[# ]*JWT_SECRET=.*/JWT_SECRET=${new_secret}/" "$SCRIPT_DIR/.env" 2>/dev/null || \
                        sed -i '' "s/^[# ]*JWT_SECRET=.*/JWT_SECRET=${new_secret}/" "$SCRIPT_DIR/.env"
                else
                    echo "" >> "$SCRIPT_DIR/.env"
                    echo "JWT_SECRET=${new_secret}" >> "$SCRIPT_DIR/.env"
                fi
                rm -f "$SCRIPT_DIR/.env.bak" 2>/dev/null || true
                log_ok "Generated JWT_SECRET in .env (Admin UI exposed on :3003 by default)"
                log_warn "SECURITY: Admin UI binds to 0.0.0.0 by default; restrict port 3003 and change admin password on first login"
            else
                log_warn "JWT_SECRET is missing and could not be generated automatically"
                log_info "  Set JWT_SECRET in .env (recommended: openssl rand -hex 32)"
            fi
        fi
    fi

    # Ensure Admin UI can control containers by mounting the correct Docker socket.
    # docker-compose.yml mounts `${DOCKER_SOCK:-/var/run/docker.sock}` into admin_ui as `/var/run/docker.sock`.
    # On rootless Docker/Podman, `/var/run/docker.sock` is usually absent, so we must persist DOCKER_SOCK in `.env`.
    if [ -f "$SCRIPT_DIR/.env" ]; then
        local current_sock desired_sock
        current_sock="$(grep -E '^[# ]*DOCKER_SOCK=' "$SCRIPT_DIR/.env" | tail -n1 | sed -E 's/^[# ]*DOCKER_SOCK=//')"
        current_sock="$(echo "$current_sock" | tr -d '\r' | xargs 2>/dev/null || echo "$current_sock")"

        desired_sock=""
        # Prefer explicit unix socket from DOCKER_HOST when present.
        if [ -n "${DOCKER_HOST:-}" ] && [[ "${DOCKER_HOST}" == unix://* ]]; then
            desired_sock="${DOCKER_HOST#unix://}"
        elif [ "$DOCKER_ROOTLESS" = true ]; then
            desired_sock="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/docker.sock"
        elif is_podman; then
            # Podman (rootless) commonly exposes a Docker-compatible socket here.
            if [ -n "${XDG_RUNTIME_DIR:-}" ] && [ -S "$XDG_RUNTIME_DIR/podman/podman.sock" ]; then
                desired_sock="$XDG_RUNTIME_DIR/podman/podman.sock"
            fi
        fi

        # Only write DOCKER_SOCK when needed (non-default socket, missing/invalid config).
        # Do not override an explicit, valid value.
        if [ -n "$desired_sock" ] && [ "$desired_sock" != "/var/run/docker.sock" ]; then
            local needs_update=false
            if [ -z "$current_sock" ]; then
                needs_update=true
            elif [ ! -S "$current_sock" ]; then
                needs_update=true
            fi

            if [ "$needs_update" = true ]; then
                if grep -qE '^[# ]*DOCKER_SOCK=' "$SCRIPT_DIR/.env"; then
                    sed -i.bak "s|^[# ]*DOCKER_SOCK=.*|DOCKER_SOCK=${desired_sock}|" "$SCRIPT_DIR/.env" 2>/dev/null || \
                        sed -i '' "s|^[# ]*DOCKER_SOCK=.*|DOCKER_SOCK=${desired_sock}|" "$SCRIPT_DIR/.env"
                else
                    echo "" >> "$SCRIPT_DIR/.env"
                    echo "DOCKER_SOCK=${desired_sock}" >> "$SCRIPT_DIR/.env"
                fi
                rm -f "$SCRIPT_DIR/.env.bak" 2>/dev/null || true
                log_ok "Set DOCKER_SOCK in .env for Admin UI container control"
                log_info "  DOCKER_SOCK=${desired_sock}"
                log_info "  Recreate admin_ui to apply: ${COMPOSE_CMD:-docker compose} -p asterisk-ai-voice-agent up -d --force-recreate admin_ui"
            fi
        fi
    fi
}

# ============================================================================
# Asterisk Detection
# ============================================================================
_json_escape() {
    local s="${1-}"
    s=${s//\\/\\\\}
    s=${s//\"/\\\"}
    s=${s//$'\n'/\\n}
    s=${s//$'\r'/\\r}
    s=${s//$'\t'/\\t}
    printf '%s' "$s"
}

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
        log_info "Asterisk binary not found in PATH (may be containerized)"
    fi
    
    if [ "$ASTERISK_FOUND" = true ]; then
        log_ok "Asterisk config: $ASTERISK_DIR"
        
        # Check for FreePBX
        if [ -f "/etc/freepbx.conf" ] || [ -f "/etc/sangoma/pbx" ]; then
            FREEPBX_VERSION=$(fwconsole -V 2>/dev/null | head -1 || echo "detected")
            log_ok "FreePBX: $FREEPBX_VERSION"
        fi
    else
        log_info "Asterisk config directory not found (may be containerized or custom path)"
        
        # Interactive mode: ask user for path (with timeout)
        if [ -t 0 ] && [ -t 1 ]; then
            echo ""
            echo -e "${YELLOW}Enter Asterisk config directory path (or press Enter to skip):${NC}"
            read -t 10 -r USER_ASTERISK_PATH || USER_ASTERISK_PATH=""
            
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
            fi
        fi
    fi
}

# ============================================================================
# Asterisk UID/GID Detection
# ============================================================================
check_asterisk_uid_gid() {
    # Skip if Asterisk not found on host
    if ! command -v asterisk &>/dev/null && [ ! -f /etc/asterisk/asterisk.conf ]; then
        log_info "Asterisk not on host - skipping UID/GID detection"
        return 0
    fi
    
    local AST_UID=""
    local AST_GID=""
    
    # Try to get asterisk user UID/GID
    if id asterisk &>/dev/null; then
        AST_UID=$(id -u asterisk 2>/dev/null)
        AST_GID=$(id -g asterisk 2>/dev/null)
    elif getent passwd asterisk &>/dev/null; then
        AST_UID=$(getent passwd asterisk | cut -d: -f3)
        AST_GID=$(getent passwd asterisk | cut -d: -f4)
    fi
    
    if [ -z "$AST_UID" ] || [ -z "$AST_GID" ]; then
        log_warn "Could not detect Asterisk UID/GID - using defaults (995:995)"
        return 0
    fi
    
    log_ok "Asterisk UID:GID = $AST_UID:$AST_GID"
    
    # Set up media directory with setgid bit for group permission inheritance
    MEDIA_DIR="$SCRIPT_DIR/asterisk_media/ai-generated"
    DATA_DIR="$SCRIPT_DIR/data"
    ASTERISK_SOUNDS_LINK="/var/lib/asterisk/sounds/ai-generated"

    # Detect when Asterisk can't traverse the media directory path. Common pitfall:
    # project lives under /root (0700), so Asterisk sees "file does not exist" for ai-generated sounds.
    local use_bind_mount=false
    if [ -d "/var/lib/asterisk/sounds" ] && id asterisk &>/dev/null; then
        if ! sudo -u asterisk test -x "$MEDIA_DIR" 2>/dev/null; then
            use_bind_mount=true
            log_warn "Asterisk user cannot access media directory path; file playback via symlink may fail"
            log_info "  media_dir=$MEDIA_DIR"
            log_info "  Fix: use a bind mount at $ASTERISK_SOUNDS_LINK (avoids /root traversal)."
        fi
    fi
    if [ "$APPLY_FIXES" = true ]; then
        # Create directory if it doesn't exist
        mkdir -p "$MEDIA_DIR" 2>/dev/null
        
        # Change group ownership to asterisk group
        if sudo chgrp "$AST_GID" "$MEDIA_DIR" 2>/dev/null; then
            log_ok "Set media directory group to asterisk (GID=$AST_GID)"
        else
            log_warn "Could not set media directory group (may need sudo)"
            FIX_CMDS+=("sudo chgrp $AST_GID $MEDIA_DIR")
        fi
        
        # Set setgid bit so new files inherit group ownership
        if sudo chmod 2775 "$MEDIA_DIR" 2>/dev/null; then
            log_ok "Set media directory permissions (setgid enabled)"
        else
            log_warn "Could not set media directory permissions (may need sudo)"
            FIX_CMDS+=("sudo chmod 2775 $MEDIA_DIR")
        fi
        
        # Also set parent directory
        MEDIA_PARENT="$SCRIPT_DIR/asterisk_media"
        sudo chgrp "$AST_GID" "$MEDIA_PARENT" 2>/dev/null
        sudo chmod 2775 "$MEDIA_PARENT" 2>/dev/null

        # Ensure data directory is writable by the container runtime user (appuser in asterisk group).
        # This is required for persistent SQLite call history across container recreates.
        mkdir -p "$DATA_DIR" 2>/dev/null
        touch "$DATA_DIR/.gitkeep" 2>/dev/null || true
        if sudo chgrp "$AST_GID" "$DATA_DIR" 2>/dev/null; then
            log_ok "Set data directory group to asterisk (GID=$AST_GID)"
        else
            log_warn "Could not set data directory group (may need sudo)"
            FIX_CMDS+=("sudo chgrp $AST_GID $DATA_DIR")
        fi
        if sudo chmod 2775 "$DATA_DIR" 2>/dev/null; then
            log_ok "Set data directory permissions (setgid enabled)"
        else
            log_warn "Could not set data directory permissions (may need sudo)"
            FIX_CMDS+=("sudo chmod 2775 $DATA_DIR")
        fi

        # If the DB already exists (or Admin UI created WAL/SHM), ensure it is group-writable.
        # Otherwise ai-engine (runs as appuser) may fail with:
        #   sqlite3.OperationalError: attempt to write a readonly database
        local CH_DB="$DATA_DIR/call_history.db"
        for f in "$CH_DB" "$CH_DB-wal" "$CH_DB-shm"; do
            if [ -f "$f" ]; then
                if sudo chgrp "$AST_GID" "$f" 2>/dev/null; then
                    log_ok "Set call history file group to asterisk: $f"
                else
                    log_warn "Could not set call history file group (may need sudo): $f"
                    FIX_CMDS+=("sudo chgrp $AST_GID $f")
                fi
                if sudo chmod 664 "$f" 2>/dev/null; then
                    log_ok "Set call history file permissions (group-writable): $f"
                else
                    log_warn "Could not set call history file permissions (may need sudo): $f"
                    FIX_CMDS+=("sudo chmod 664 $f")
                fi
            fi
        done

        # Create the Asterisk sounds symlink so Asterisk can serve generated audio.
        # Only do this when Asterisk sounds directory exists on host.
        if [ -d "/var/lib/asterisk/sounds" ]; then
            if [ "$use_bind_mount" = true ]; then
                # Clean up accidental nested symlink: <media_dir>/ai-generated -> <media_dir>
                if [ -L "$MEDIA_DIR/ai-generated" ]; then
                    local resolved
                    resolved="$(readlink -f "$MEDIA_DIR/ai-generated" 2>/dev/null || true)"
                    if [ "$resolved" = "$MEDIA_DIR" ]; then
                        rm -f "$MEDIA_DIR/ai-generated" 2>/dev/null || sudo rm -f "$MEDIA_DIR/ai-generated" 2>/dev/null || true
                    fi
                fi

                # Replace any existing symlink at the mountpoint.
                if [ -L "$ASTERISK_SOUNDS_LINK" ]; then
                    rm -f "$ASTERISK_SOUNDS_LINK" 2>/dev/null || sudo rm -f "$ASTERISK_SOUNDS_LINK" 2>/dev/null || true
                fi
                mkdir -p "$ASTERISK_SOUNDS_LINK" 2>/dev/null || sudo mkdir -p "$ASTERISK_SOUNDS_LINK" 2>/dev/null || true

                if mountpoint -q "$ASTERISK_SOUNDS_LINK" 2>/dev/null; then
                    log_ok "Asterisk sounds bind mount already present: $ASTERISK_SOUNDS_LINK"
                elif sudo mount --bind "$MEDIA_DIR" "$ASTERISK_SOUNDS_LINK" 2>/dev/null; then
                    log_ok "Asterisk sounds bind mount: $ASTERISK_SOUNDS_LINK ⇢ $MEDIA_DIR"
                else
                    log_warn "Could not create Asterisk sounds bind mount (may need sudo)"
                    FIX_CMDS+=("sudo mount --bind $MEDIA_DIR $ASTERISK_SOUNDS_LINK")
                fi

                # Persist bind mount across reboots (systemd assumed; falls back to generic fstab options).
                ensure_fstab_bind_mount "$MEDIA_DIR" "$ASTERISK_SOUNDS_LINK" || true

                # Optional: explicit verification (useful for troubleshooting after reboots).
                if [ "$PERSIST_MEDIA_MOUNT" = true ]; then
                    verify_fstab_bind_mount "$ASTERISK_SOUNDS_LINK" || true
                    if mountpoint -q "$ASTERISK_SOUNDS_LINK" 2>/dev/null; then
                        log_ok "Bind mount is currently active: $ASTERISK_SOUNDS_LINK"
                    else
                        log_warn "Bind mount is not currently active: $ASTERISK_SOUNDS_LINK"
                        log_info "  Try: sudo mount '$ASTERISK_SOUNDS_LINK'  (or reboot)"
                    fi
                fi
            else
                # Symlink mode (works when Asterisk can traverse MEDIA_DIR)
                # AAVA-150: Handle existing directory at symlink target
                local symlink_blocked=false
                if [ -e "$ASTERISK_SOUNDS_LINK" ] && [ ! -L "$ASTERISK_SOUNDS_LINK" ]; then
                    if [ -d "$ASTERISK_SOUNDS_LINK" ]; then
                        if [ -z "$(ls -A "$ASTERISK_SOUNDS_LINK" 2>/dev/null)" ]; then
                            # Empty directory - safe to remove
                            rmdir "$ASTERISK_SOUNDS_LINK" 2>/dev/null || sudo rmdir "$ASTERISK_SOUNDS_LINK" 2>/dev/null || true
                        elif [ "$APPLY_FIXES" = true ]; then
                            # AAVA-150: Non-empty directory - auto-backup with --apply-fixes
                            log_info "Backing up existing directory: ${ASTERISK_SOUNDS_LINK}.bak"
                            if sudo mv "$ASTERISK_SOUNDS_LINK" "${ASTERISK_SOUNDS_LINK}.bak" 2>/dev/null; then
                                log_ok "Backed up: $ASTERISK_SOUNDS_LINK → ${ASTERISK_SOUNDS_LINK}.bak"
                            else
                                log_warn "Could not backup existing directory (may need sudo)"
                                symlink_blocked=true
                            fi
                        else
                            # Without --apply-fixes, warn and add to FIX_CMDS
                            log_warn "Asterisk sounds path exists but is not a symlink: $ASTERISK_SOUNDS_LINK"
                            log_info "  This will cause 'ai-generated/ai-generated/' double-path issues"
                            log_info "  Run with --apply-fixes to auto-backup and create symlink"
                            FIX_CMDS+=("sudo mv $ASTERISK_SOUNDS_LINK ${ASTERISK_SOUNDS_LINK}.bak")
                            FIX_CMDS+=("sudo ln -sfn $MEDIA_DIR $ASTERISK_SOUNDS_LINK")
                            symlink_blocked=true
                        fi
                    else
                        # It's a file, not a directory
                        log_warn "Asterisk sounds path exists as a file (not directory): $ASTERISK_SOUNDS_LINK"
                        if [ "$APPLY_FIXES" = true ]; then
                            sudo mv "$ASTERISK_SOUNDS_LINK" "${ASTERISK_SOUNDS_LINK}.bak" 2>/dev/null || true
                        else
                            FIX_CMDS+=("sudo mv $ASTERISK_SOUNDS_LINK ${ASTERISK_SOUNDS_LINK}.bak")
                            symlink_blocked=true
                        fi
                    fi
                fi
                
                # Only attempt symlink creation if not blocked
                if [ "$symlink_blocked" = false ]; then
                    if ln -sfn "$MEDIA_DIR" "$ASTERISK_SOUNDS_LINK" 2>/dev/null; then
                        log_ok "Asterisk sounds symlink: $ASTERISK_SOUNDS_LINK → $MEDIA_DIR"
                    elif sudo ln -sfn "$MEDIA_DIR" "$ASTERISK_SOUNDS_LINK" 2>/dev/null; then
                        log_ok "Asterisk sounds symlink: $ASTERISK_SOUNDS_LINK → $MEDIA_DIR (via sudo)"
                    else
                        log_warn "Could not create Asterisk sounds symlink (may need sudo)"
                        FIX_CMDS+=("sudo ln -sfn $MEDIA_DIR $ASTERISK_SOUNDS_LINK")
                    fi
                fi
            fi
        fi
    else
        # Check if directory setup is needed
        if [ ! -d "$MEDIA_DIR" ]; then
            FIX_CMDS+=("mkdir -p $MEDIA_DIR")
        fi
        FIX_CMDS+=("sudo chgrp $AST_GID $MEDIA_DIR")
        FIX_CMDS+=("sudo chmod 2775 $MEDIA_DIR  # setgid for group inheritance")
        if [ ! -d "$DATA_DIR" ]; then
            FIX_CMDS+=("mkdir -p $DATA_DIR && touch $DATA_DIR/.gitkeep")
        fi
        FIX_CMDS+=("sudo chgrp $AST_GID $DATA_DIR")
        FIX_CMDS+=("sudo chmod 2775 $DATA_DIR  # setgid for group inheritance (SQLite call history)")
        if [ -d "/var/lib/asterisk/sounds" ]; then
            if [ "$use_bind_mount" = true ]; then
                FIX_CMDS+=("sudo rm -f $ASTERISK_SOUNDS_LINK && sudo mkdir -p $ASTERISK_SOUNDS_LINK")
                FIX_CMDS+=("sudo mount --bind $MEDIA_DIR $ASTERISK_SOUNDS_LINK  # avoid /root traversal issues")
                FIX_CMDS+=("# Persist bind mount across reboots (systemd):")
                FIX_CMDS+=("# echo '$MEDIA_DIR $ASTERISK_SOUNDS_LINK none bind,nofail,x-systemd.automount 0 0' | sudo tee -a /etc/fstab")
                FIX_CMDS+=("# sudo systemctl daemon-reload")
                if [ "$PERSIST_MEDIA_MOUNT" = true ]; then
                    verify_fstab_bind_mount "$ASTERISK_SOUNDS_LINK" || true
                    log_info "To apply persistence automatically, run:"
                    log_info "  sudo ./preflight.sh --apply-fixes"
                fi
            else
                FIX_CMDS+=("sudo ln -sfn $MEDIA_DIR $ASTERISK_SOUNDS_LINK  # allow Asterisk to serve generated audio")
            fi
        fi
    fi
    
    # Check if .env exists and update if needed
    if [ -f "$SCRIPT_DIR/.env" ]; then
        local NEEDS_UPDATE=false
        
        # Check if ASTERISK_UID is set correctly
        if grep -q "^ASTERISK_UID=" "$SCRIPT_DIR/.env"; then
            local CURRENT_UID=$(grep "^ASTERISK_UID=" "$SCRIPT_DIR/.env" | cut -d= -f2)
            if [ "$CURRENT_UID" != "$AST_UID" ]; then
                log_warn "ASTERISK_UID in .env ($CURRENT_UID) doesn't match system ($AST_UID)"
                NEEDS_UPDATE=true
            fi
        else
            # Not set, need to add if not default
            if [ "$AST_UID" != "995" ]; then
                NEEDS_UPDATE=true
            fi
        fi
        
        # Check if ASTERISK_GID is set correctly
        if grep -q "^ASTERISK_GID=" "$SCRIPT_DIR/.env"; then
            local CURRENT_GID=$(grep "^ASTERISK_GID=" "$SCRIPT_DIR/.env" | cut -d= -f2)
            if [ "$CURRENT_GID" != "$AST_GID" ]; then
                log_warn "ASTERISK_GID in .env ($CURRENT_GID) doesn't match system ($AST_GID)"
                NEEDS_UPDATE=true
            fi
        else
            # Not set, need to add if not default
            if [ "$AST_GID" != "995" ]; then
                NEEDS_UPDATE=true
            fi
        fi
        
        # Update .env if needed
        if [ "$NEEDS_UPDATE" = true ]; then
            if [ "$APPLY_FIXES" = true ]; then
                # Remove old entries if they exist
                sed -i.bak '/^ASTERISK_UID=/d' "$SCRIPT_DIR/.env" 2>/dev/null || \
                    sed -i '' '/^ASTERISK_UID=/d' "$SCRIPT_DIR/.env"
                sed -i.bak '/^ASTERISK_GID=/d' "$SCRIPT_DIR/.env" 2>/dev/null || \
                    sed -i '' '/^ASTERISK_GID=/d' "$SCRIPT_DIR/.env"
                
                # Add correct values
                echo "" >> "$SCRIPT_DIR/.env"
                echo "# Asterisk user UID/GID for file permissions (auto-detected by preflight.sh)" >> "$SCRIPT_DIR/.env"
                echo "ASTERISK_UID=$AST_UID" >> "$SCRIPT_DIR/.env"
                echo "ASTERISK_GID=$AST_GID" >> "$SCRIPT_DIR/.env"
                
                # Clean up backup file
                rm -f "$SCRIPT_DIR/.env.bak"
                
                log_ok "Updated .env with ASTERISK_UID=$AST_UID ASTERISK_GID=$AST_GID"
            else
                log_warn "ASTERISK_UID/GID need to be updated in .env"
                FIX_CMDS+=("# Update .env with: ASTERISK_UID=$AST_UID ASTERISK_GID=$AST_GID")
            fi
        fi
    else
        # No .env file yet - will be created by check_env, add to FIX_CMDS
        if [ "$AST_UID" != "995" ] || [ "$AST_GID" != "995" ]; then
            FIX_CMDS+=("echo 'ASTERISK_UID=$AST_UID' >> $SCRIPT_DIR/.env")
            FIX_CMDS+=("echo 'ASTERISK_GID=$AST_GID' >> $SCRIPT_DIR/.env")
        fi
    fi
}

# ============================================================================
# Asterisk Config Audit → JSON Manifest (AAVA: Asterisk Config Discovery)
# ============================================================================
check_asterisk_config() {
    # Only run if Asterisk was detected on host
    if [ "$ASTERISK_FOUND" != true ] || [ -z "$ASTERISK_DIR" ]; then
        log_info "Asterisk config audit skipped (not detected on host)"
        # Write minimal manifest so the UI knows preflight ran
        _write_asterisk_manifest false "" "" false "" "{}"
        return 0
    fi

    local APP_NAME
    APP_NAME="$(grep -E '^[[:space:]]*app_name:' "$SCRIPT_DIR/config/ai-agent.yaml" 2>/dev/null | head -1 | sed 's/.*app_name:[[:space:]]*//' | tr -d '\r\n"'"'" || echo "asterisk-ai-voice-agent")"
    [ -z "$APP_NAME" ] && APP_NAME="asterisk-ai-voice-agent"

    local FREEPBX_DETECTED=false
    local FREEPBX_VER=""
    if [ -f "/etc/freepbx.conf" ] || [ -f "/etc/sangoma/pbx" ]; then
        FREEPBX_DETECTED=true
        FREEPBX_VER=$(fwconsole -V 2>/dev/null | head -1 || echo "detected")
    fi

    # --- ARI enabled check ---
    local ari_enabled_ok=false
    local ari_enabled_detail="not found"
    # FreePBX splits config via #include; check both main and included files
    for f in "$ASTERISK_DIR/ari.conf" "$ASTERISK_DIR/ari_general_additional.conf" "$ASTERISK_DIR/ari_general_custom.conf"; do
        if [ -f "$f" ] && grep -qiE '^[[:space:]]*enabled[[:space:]]*=[[:space:]]*yes' "$f" 2>/dev/null; then
            ari_enabled_ok=true
            ari_enabled_detail="enabled=yes in $(basename "$f")"
            break
        fi
    done
    if [ "$ari_enabled_ok" = true ]; then
        log_ok "ARI enabled: $ari_enabled_detail"
    else
        log_warn "ARI not enabled in ari.conf (or included files)"
        log_info "  Fix: ensure 'enabled=yes' under [general] in ari.conf or ari_general_custom.conf"
    fi

    # --- ARI user check ---
    local ari_user_ok=false
    local ari_user_detail="not found"
    local ARI_USERNAME="${ASTERISK_ARI_USERNAME:-}"
    [ -z "$ARI_USERNAME" ] && ARI_USERNAME="$(grep -E '^ASTERISK_ARI_USERNAME=' "$SCRIPT_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '\r\n"'"'" || true)"
    [ -z "$ARI_USERNAME" ] && ARI_USERNAME="AIAgent"
    for f in "$ASTERISK_DIR/ari.conf" "$ASTERISK_DIR/ari_additional.conf" "$ASTERISK_DIR/ari_additional_custom.conf"; do
        if [ -f "$f" ] && grep -qE "^\[$ARI_USERNAME\]" "$f" 2>/dev/null; then
            ari_user_ok=true
            ari_user_detail="[$ARI_USERNAME] found in $(basename "$f")"
            break
        fi
    done
    if [ "$ari_user_ok" = true ]; then
        log_ok "ARI user: $ari_user_detail"
    else
        log_warn "ARI user [$ARI_USERNAME] not found in ari.conf (or included files)"
        log_info "  Fix: add user block in ari_additional_custom.conf or via FreePBX Admin"
    fi

    # --- HTTP enabled check ---
    local http_enabled_ok=false
    local http_enabled_detail="not found"
    for f in "$ASTERISK_DIR/http.conf" "$ASTERISK_DIR/http_additional.conf" "$ASTERISK_DIR/http_custom.conf"; do
        if [ -f "$f" ] && grep -qiE '^[[:space:]]*enabled[[:space:]]*=[[:space:]]*yes' "$f" 2>/dev/null; then
            http_enabled_ok=true
            http_enabled_detail="enabled=yes in $(basename "$f")"
            break
        fi
    done
    if [ "$http_enabled_ok" = true ]; then
        log_ok "HTTP server: $http_enabled_detail"
    else
        log_warn "HTTP server not enabled in http.conf (or included files)"
        log_info "  Fix: ensure 'enabled=yes' under [general] in http.conf or http_custom.conf"
    fi

    # --- Dialplan context check ---
    local dialplan_ok=false
    local dialplan_detail="not found"
    if [ -f "$ASTERISK_DIR/extensions_custom.conf" ]; then
        if grep -qiE "Stasis\($APP_NAME\)" "$ASTERISK_DIR/extensions_custom.conf" 2>/dev/null; then
            dialplan_ok=true
            dialplan_detail="Stasis($APP_NAME) in extensions_custom.conf"
        fi
    fi
    if [ "$dialplan_ok" = true ]; then
        log_ok "Dialplan context: $dialplan_detail"
    else
        log_warn "No Stasis($APP_NAME) context found in extensions_custom.conf"
        log_info "  Fix: add a context with 'Stasis($APP_NAME)' to extensions_custom.conf"
    fi

    # --- Module checks (requires asterisk binary) ---
    local mod_audiosocket_ok=false mod_audiosocket_detail="binary not available"
    local mod_res_ari_ok=false mod_res_ari_detail="binary not available"
    local mod_res_stasis_ok=false mod_res_stasis_detail="binary not available"
    local mod_chan_pjsip_ok=false mod_chan_pjsip_detail="binary not available"

    if command -v asterisk &>/dev/null; then
        _check_ast_module "app_audiosocket" && mod_audiosocket_ok=true && mod_audiosocket_detail="Running"
        [ "$mod_audiosocket_ok" = false ] && mod_audiosocket_detail="Not loaded"

        _check_ast_module "res_ari" && mod_res_ari_ok=true && mod_res_ari_detail="Running"
        [ "$mod_res_ari_ok" = false ] && mod_res_ari_detail="Not loaded"

        _check_ast_module "res_stasis" && mod_res_stasis_ok=true && mod_res_stasis_detail="Running"
        [ "$mod_res_stasis_ok" = false ] && mod_res_stasis_detail="Not loaded"

        _check_ast_module "chan_pjsip" && mod_chan_pjsip_ok=true && mod_chan_pjsip_detail="Running"
        [ "$mod_chan_pjsip_ok" = false ] && mod_chan_pjsip_detail="Not loaded"

        log_ok "Asterisk modules: audiosocket=$mod_audiosocket_detail, res_ari=$mod_res_ari_detail, res_stasis=$mod_res_stasis_detail, chan_pjsip=$mod_chan_pjsip_detail"
    else
        log_info "Asterisk binary not in PATH — module checks skipped (will use ARI in Admin UI)"
    fi

    # --- Build JSON checks object ---
    local ari_enabled_detail_e ari_user_detail_e http_enabled_detail_e dialplan_detail_e
    local mod_audiosocket_detail_e mod_res_ari_detail_e mod_res_stasis_detail_e mod_chan_pjsip_detail_e
    ari_enabled_detail_e=$(_json_escape "$ari_enabled_detail")
    ari_user_detail_e=$(_json_escape "$ari_user_detail")
    http_enabled_detail_e=$(_json_escape "$http_enabled_detail")
    dialplan_detail_e=$(_json_escape "$dialplan_detail")
    mod_audiosocket_detail_e=$(_json_escape "$mod_audiosocket_detail")
    mod_res_ari_detail_e=$(_json_escape "$mod_res_ari_detail")
    mod_res_stasis_detail_e=$(_json_escape "$mod_res_stasis_detail")
    mod_chan_pjsip_detail_e=$(_json_escape "$mod_chan_pjsip_detail")

    local CHECKS
    CHECKS=$(cat <<JSONEOF
{
    "ari_enabled": { "ok": $ari_enabled_ok, "detail": "$ari_enabled_detail_e" },
    "ari_user": { "ok": $ari_user_ok, "detail": "$ari_user_detail_e" },
    "http_enabled": { "ok": $http_enabled_ok, "detail": "$http_enabled_detail_e" },
    "dialplan_context": { "ok": $dialplan_ok, "detail": "$dialplan_detail_e" },
    "module_app_audiosocket": { "ok": $mod_audiosocket_ok, "detail": "$mod_audiosocket_detail_e" },
    "module_res_ari": { "ok": $mod_res_ari_ok, "detail": "$mod_res_ari_detail_e" },
    "module_res_stasis": { "ok": $mod_res_stasis_ok, "detail": "$mod_res_stasis_detail_e" },
    "module_chan_pjsip": { "ok": $mod_chan_pjsip_ok, "detail": "$mod_chan_pjsip_detail_e" }
}
JSONEOF
    )

    _write_asterisk_manifest true "${ASTERISK_VERSION:-unknown}" "$ASTERISK_DIR" "$FREEPBX_DETECTED" "$FREEPBX_VER" "$CHECKS"
}

# Helper: check if an Asterisk module is loaded via CLI
_check_ast_module() {
    local mod_name="$1"
    local output
    output=$(asterisk -rx "module show like $mod_name" 2>/dev/null || true)
    echo "$output" | grep -qiE "$mod_name.*Running"
}

# Helper: write the JSON manifest to data/asterisk_status.json
_write_asterisk_manifest() {
    local ast_found="$1" ast_version="$2" config_dir="$3" fpbx_detected="$4" fpbx_version="$5" checks_json="$6"
    local MANIFEST_DIR="$SCRIPT_DIR/data"
    local MANIFEST_FILE="$MANIFEST_DIR/asterisk_status.json"
    local TIMESTAMP
    TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date +"%Y-%m-%dT%H:%M:%SZ")"
    local timestamp_e ast_version_e config_dir_e fpbx_version_e
    timestamp_e=$(_json_escape "$TIMESTAMP")
    ast_version_e=$(_json_escape "$ast_version")
    config_dir_e=$(_json_escape "$config_dir")
    fpbx_version_e=$(_json_escape "$fpbx_version")

    mkdir -p "$MANIFEST_DIR" 2>/dev/null || true

    cat > "$MANIFEST_FILE" <<MANIFESTEOF
{
    "timestamp": "$timestamp_e",
    "asterisk_found": $ast_found,
    "asterisk_version": "$ast_version_e",
    "config_dir": "$config_dir_e",
    "freepbx": {
        "detected": $fpbx_detected,
        "version": "$fpbx_version_e"
    },
    "checks": $checks_json
}
MANIFESTEOF

    if [ -f "$MANIFEST_FILE" ]; then
        log_ok "Asterisk config manifest: $MANIFEST_FILE"
    else
        log_warn "Could not write Asterisk config manifest to $MANIFEST_FILE"
    fi
}

# ============================================================================
# GPU Detection (AAVA-140)
# ============================================================================
check_gpu() {
    GPU_AVAILABLE=false
    GPU_NAME=""
    
    # Step 1: Check if nvidia-smi exists on host
    if ! command -v nvidia-smi &>/dev/null; then
        log_info "No NVIDIA GPU detected (nvidia-smi not found)"
        update_env_gpu "false"
        return 0
    fi
    
    # Step 2: Check if nvidia-smi works (driver loaded)
    if ! nvidia-smi &>/dev/null; then
        log_warn "NVIDIA driver not working (nvidia-smi failed)"
        log_info "  Check driver status: nvidia-smi"
        log_info "  Install drivers: https://docs.nvidia.com/datacenter/tesla/tesla-installation-notes/"
        update_env_gpu "false"
        return 0
    fi
    
    # GPU detected!
    GPU_AVAILABLE=true
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 | xargs)
    GPU_MEMORY=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1 | xargs)
    log_ok "NVIDIA GPU detected: $GPU_NAME ($GPU_MEMORY)"
    
    # Step 3: Check nvidia-container-toolkit
    if ! command -v nvidia-container-cli &>/dev/null; then
        log_warn "nvidia-container-toolkit not installed"
        log_info "  GPU detected but Docker cannot use it without the toolkit"
        
        # Offer install instructions based on OS
        case "$OS_FAMILY" in
            debian)
                log_info "  Install with:"
                log_info "    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg"
                log_info "    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \\"
                log_info "      sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \\"
                log_info "      sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list"
                log_info "    sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit"
                log_info "    sudo nvidia-ctk runtime configure --runtime=docker"
                log_info "    sudo systemctl restart docker"
                ;;
            rhel)
                log_info "  Install with:"
                log_info "    curl -s -L https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo | \\"
                log_info "      sudo tee /etc/yum.repos.d/nvidia-container-toolkit.repo"
                log_info "    sudo yum install -y nvidia-container-toolkit"
                log_info "    sudo nvidia-ctk runtime configure --runtime=docker"
                log_info "    sudo systemctl restart docker"
                ;;
        esac
        log_info "  Docs: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html"
        update_env_gpu "true"  # GPU exists, just toolkit missing
        return 0
    fi
    
    log_ok "nvidia-container-toolkit installed"
    
    # Step 4: Test Docker GPU passthrough
    log_info "Testing Docker GPU passthrough..."
    local cuda_test_images=(
        "nvidia/cuda:12.0.0-base-ubi8"
        "nvidia/cuda:12.0-base"
        "nvidia/cuda:12.4.1-base-ubuntu22.04"
    )
    local passthrough_test_ok=false
    local working_cuda_test_image=""
    local cuda_test_image
    for cuda_test_image in "${cuda_test_images[@]}"; do
        if docker run --rm --gpus all "$cuda_test_image" nvidia-smi &>/dev/null 2>&1; then
            passthrough_test_ok=true
            working_cuda_test_image="$cuda_test_image"
            break
        fi
    done

    if [ "$passthrough_test_ok" = true ]; then
        log_ok "Docker GPU passthrough working"
        log_info "  Verified with image: $working_cuda_test_image"
        update_env_gpu "true"
        
        # Inform user - GPU detection works via .env, no workflow change needed
        log_info ""
        log_info "  GPU will be detected by Setup Wizard automatically (via GPU_AVAILABLE in .env)"
        log_info ""
        log_info "  To use GPU for LLM inference (optional, faster responses):"
        log_info "    1. Set LOCAL_LLM_GPU_LAYERS=-1 in .env"
        log_info "    2. Start local_ai_server with GPU override:"
        log_info "       ${COMPOSE_CMD:-docker compose} -p asterisk-ai-voice-agent -f docker-compose.yml -f docker-compose.gpu.yml up -d --build local_ai_server"
    else
        log_warn "Docker GPU passthrough test failed"
        log_info "  GPU detected and toolkit installed, but Docker cannot access GPU"
        log_info "  Try: sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
        update_env_gpu "true"  # GPU exists, passthrough just needs config
    fi
}

# Helper: Update GPU_AVAILABLE in .env
update_env_gpu() {
    local gpu_value="$1"
    
    [ ! -f "$SCRIPT_DIR/.env" ] && return 0
    
    # Check if GPU_AVAILABLE already set correctly
    local current_value
    current_value="$(grep -E '^GPU_AVAILABLE=' "$SCRIPT_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')"
    
    if [ "$current_value" = "$gpu_value" ]; then
        return 0  # Already correct
    fi
    
    # Update or add GPU_AVAILABLE
    if grep -qE '^#?GPU_AVAILABLE=' "$SCRIPT_DIR/.env" 2>/dev/null; then
        # Update existing line
        sed -i.bak "s/^#*GPU_AVAILABLE=.*/GPU_AVAILABLE=$gpu_value/" "$SCRIPT_DIR/.env" 2>/dev/null || \
            sed -i '' "s/^#*GPU_AVAILABLE=.*/GPU_AVAILABLE=$gpu_value/" "$SCRIPT_DIR/.env"
        rm -f "$SCRIPT_DIR/.env.bak" 2>/dev/null
    else
        # Add new line in GPU section (after LOCAL_LLM_GPU_LAYERS or at end)
        if grep -q "LOCAL_LLM_GPU_LAYERS" "$SCRIPT_DIR/.env" 2>/dev/null; then
            sed -i.bak "/LOCAL_LLM_GPU_LAYERS/a\\
GPU_AVAILABLE=$gpu_value" "$SCRIPT_DIR/.env" 2>/dev/null || \
                sed -i '' "/LOCAL_LLM_GPU_LAYERS/a\\
GPU_AVAILABLE=$gpu_value" "$SCRIPT_DIR/.env"
            rm -f "$SCRIPT_DIR/.env.bak" 2>/dev/null
        else
            echo "" >> "$SCRIPT_DIR/.env"
            echo "# GPU detected by preflight.sh (AAVA-140)" >> "$SCRIPT_DIR/.env"
            echo "GPU_AVAILABLE=$gpu_value" >> "$SCRIPT_DIR/.env"
        fi
    fi
    
    if [ "$gpu_value" = "true" ]; then
        log_ok "Set GPU_AVAILABLE=true in .env"
    fi
}

# ============================================================================
# Port Check
# ============================================================================
check_ports() {
    PORT=3003
    if command -v ss &>/dev/null; then
        if ss -tln | grep -q ":$PORT "; then
            log_warn "Port $PORT already in use (Admin UI port)"
        else
            log_ok "Port $PORT available"
        fi
    elif command -v netstat &>/dev/null; then
        if netstat -tln | grep -q ":$PORT "; then
            log_warn "Port $PORT already in use (Admin UI port)"
        else
            log_ok "Port $PORT available"
        fi
    fi
}

# ============================================================================
# Apply Fixes
# ============================================================================
apply_fixes() {
    if [ ${#FIX_CMDS[@]} -eq 0 ]; then
        return 0
    fi
    
    echo ""
    echo -e "${YELLOW}Applying fixes...${NC}"
    
    local all_success=true
    for cmd in "${FIX_CMDS[@]}"; do
        echo "  Running: $cmd"
        if eval "$cmd" 2>/dev/null; then
            echo -e "    ${GREEN}✓${NC} Success"
        else
            echo -e "    ${RED}✗${NC} Failed (may need sudo)"
            all_success=false
        fi
    done
    
    # Re-validate after applying fixes
    if [ "$all_success" = true ]; then
        echo ""
        echo -e "${BLUE}Re-validating after fixes...${NC}"
        
        # Clear arrays and re-run checks
        WARNINGS=()
        FAILURES=()
        FIX_CMDS=()
        
        # Re-run the checks silently first, then show summary
        check_docker >/dev/null 2>&1
        check_compose >/dev/null 2>&1
        check_directories >/dev/null 2>&1
        check_project_permissions >/dev/null 2>&1
        check_data_permissions >/dev/null 2>&1
        check_selinux >/dev/null 2>&1
        check_env >/dev/null 2>&1
        check_docker_gid >/dev/null 2>&1
    fi
}

# ============================================================================
# Summary
# ============================================================================
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
        echo "╔═══════════════════════════════════════════════════════════════════════════╗"
        echo "║  ⚠️  SECURITY NOTICE                                                       ║"
        echo "╠═══════════════════════════════════════════════════════════════════════════╣"
        echo "║  Admin UI binds to 0.0.0.0:3003 by default (accessible on network).       ║"
        echo "║                                                                           ║"
        echo "║  REQUIRED ACTIONS:                                                        ║"
        echo "║    1. Change default password (admin/admin) on first login                ║"
        echo "║    2. Restrict port 3003 via firewall, VPN, or reverse proxy              ║"
        echo "╚═══════════════════════════════════════════════════════════════════════════╝"
        echo ""
        echo "Next steps:"
        echo ""
        echo "  Tip (file playback):"
        echo "     If Asterisk file playback fails with 'File ... does not exist' and your project is under /root,"
        echo "     run: sudo ./preflight.sh --apply-fixes"
        echo "     (For troubleshooting, add: --persist-media-mount)"
        echo ""
        echo "  Tip (local AI build mode):"
        echo "     Local AI Server is optional (only needed for local_hybrid/local_only pipelines)."
        echo "     Note: local_ai_server is based on Debian trixie intentionally (for embedded Kroko compatibility)."
        echo "           admin_ui and ai_engine are based on Debian bookworm."
        echo "     Use a smaller image for most users:"
        echo "       sudo ./preflight.sh --apply-fixes --local-ai-minimal"
        echo "     Or enable the full build (more models / larger image):"
        echo "       sudo ./preflight.sh --apply-fixes --local-ai-full"
        echo "     After changing LOCAL_AI_MODE, rebuild/recreate local_ai_server:"
        echo "       ${COMPOSE_CMD:-docker compose} -p asterisk-ai-voice-agent up -d --build --force-recreate local_ai_server"
        echo ""
        echo "  1. Start the Admin UI:"
        echo "     ${COMPOSE_CMD:-docker compose} -p asterisk-ai-voice-agent up -d admin_ui"
        echo ""
        if [ -n "${SSH_CONNECTION:-}" ] || [ -n "${SSH_TTY:-}" ]; then
            echo "  2. Access the Admin UI:"
            echo "     http://<server-ip>:3003"
        else
            echo "  2. Open: http://localhost:3003"
        fi
        echo ""
        echo "  3. Complete the Setup Wizard, then start ai_engine:"
        echo "     ${COMPOSE_CMD:-docker compose} -p asterisk-ai-voice-agent up -d ai_engine"
        echo ""
        echo "  4. For local_hybrid or local_only pipeline, also start:"
        echo "     ${COMPOSE_CMD:-docker compose} -p asterisk-ai-voice-agent up -d local_ai_server"
        echo ""
    elif [ ${#FAILURES[@]} -eq 0 ]; then
        touch "$SCRIPT_DIR/.preflight-ok"
        echo -e "${YELLOW}Checks passed with warnings.${NC}"
        echo ""
        echo "You can proceed, but consider addressing the warnings above."
        echo ""
        echo "Tip (local AI build mode):"
        echo "  Local AI Server is optional (only needed for local_hybrid/local_only pipelines)."
        echo "  Note: local_ai_server is based on Debian trixie intentionally (for embedded Kroko compatibility)."
        echo "        admin_ui and ai_engine are based on Debian bookworm."
        echo "  Use a smaller image for most users:"
        echo "    sudo ./preflight.sh --apply-fixes --local-ai-minimal"
        echo "  Or enable the full build (more models / larger image):"
        echo "    sudo ./preflight.sh --apply-fixes --local-ai-full"
        echo "  After changing LOCAL_AI_MODE, rebuild/recreate local_ai_server:"
        echo "    ${COMPOSE_CMD:-docker compose} -p asterisk-ai-voice-agent up -d --build --force-recreate local_ai_server"
        echo ""
        echo "╔═══════════════════════════════════════════════════════════════════════════╗"
        echo "║  ⚠️  SECURITY NOTICE                                                       ║"
        echo "╠═══════════════════════════════════════════════════════════════════════════╣"
        echo "║  Admin UI binds to 0.0.0.0:3003 by default (accessible on network).       ║"
        echo "║                                                                           ║"
        echo "║  REQUIRED ACTIONS:                                                        ║"
        echo "║    1. Change default password (admin/admin) on first login                ║"
        echo "║    2. Restrict port 3003 via firewall, VPN, or reverse proxy              ║"
        echo "╚═══════════════════════════════════════════════════════════════════════════╝"
        echo ""
        echo "Next steps:"
        echo ""
        echo "  Tip (file playback):"
        echo "     If Asterisk file playback fails with 'File ... does not exist' and your project is under /root,"
        echo "     run: sudo ./preflight.sh --apply-fixes"
        echo "     (For troubleshooting, add: --persist-media-mount)"
        echo ""
        echo "  1. Start the Admin UI:"
        echo "     ${COMPOSE_CMD:-docker compose} -p asterisk-ai-voice-agent up -d admin_ui"
        echo ""
        if [ -n "${SSH_CONNECTION:-}" ] || [ -n "${SSH_TTY:-}" ]; then
            echo "  2. Access the Admin UI:"
            echo "     http://<server-ip>:3003"
        else
            echo "  2. Open: http://localhost:3003"
        fi
        echo ""
        echo "  3. Complete the Setup Wizard, then start ai_engine:"
        echo "     ${COMPOSE_CMD:-docker compose} -p asterisk-ai-voice-agent up -d ai_engine"
        echo ""
        echo "  4. For local_hybrid or local_only pipeline, also start:"
        echo "     ${COMPOSE_CMD:-docker compose} -p asterisk-ai-voice-agent up -d local_ai_server"
        echo ""
    else
        echo -e "${RED}Cannot proceed - fix failures above first.${NC}"
        echo ""
        echo "After fixing failures:"
        echo "  1. Re-run: ./preflight.sh"
        echo "  2. Then run: agent check"
    fi
}

# ============================================================================
# Main
# ============================================================================
main() {
    echo ""
    echo "========================================"
    echo "AAVA Pre-flight Checks"
    echo "========================================"
    echo ""
    
    detect_os
    check_ipv6
    check_docker
    check_compose
    check_directories
    check_project_permissions
    check_data_permissions  # AAVA-150: Always runs, regardless of Asterisk location
    check_selinux
    check_env
    check_docker_gid
    check_asterisk
    check_asterisk_uid_gid
    check_asterisk_config
    check_gpu
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
