#!/bin/bash

# Asterisk AI Voice Agent - Installation Script
# This script guides the user through the initial setup and configuration process.

# --- Colors for Output ---
COLOR_RESET='\033[0m'
COLOR_GREEN='\033[0;32m'
COLOR_YELLOW='\033[0;33m'
COLOR_RED='\033[0;31m'
COLOR_BLUE='\033[0;34m'

# --- Helper Functions ---
print_info() {
    echo -e "${COLOR_BLUE}INFO: $1${COLOR_RESET}"
}

# Determine sudo usable globally
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; else SUDO=""; fi

# --- Media path setup ---
setup_media_paths() {
    print_info "Setting up media directory and Asterisk symlink for file-based playback..."

    # Determine sudo
    if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; else SUDO=""; fi

    # Resolve asterisk uid/gid (fall back to 995 which is common on FreePBX)
    AST_UID=$(id -u asterisk 2>/dev/null || echo 995)
    AST_GID=$(id -g asterisk 2>/dev/null || echo 995)

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # This repo mounts ./asterisk_media into the ai_engine container at /mnt/asterisk_media.
    # Keep host and container aligned by using the repo-local directory by default.
    MEDIA_DIR="$SCRIPT_DIR/asterisk_media/ai-generated"
    MEDIA_PARENT="$(dirname "$MEDIA_DIR")"
    ASTERISK_SOUNDS_DIR="/var/lib/asterisk/sounds"
    ASTERISK_SOUNDS_LINK="${ASTERISK_SOUNDS_DIR}/ai-generated"

    $SUDO mkdir -p "$MEDIA_DIR" || true

    # Align group + permissions for container (appuser in asterisk group) and host Asterisk readability.
    $SUDO chgrp "$AST_GID" "$MEDIA_PARENT" 2>/dev/null || true
    $SUDO chgrp "$AST_GID" "$MEDIA_DIR" 2>/dev/null || true
    $SUDO chmod 2775 "$MEDIA_PARENT" 2>/dev/null || true
    $SUDO chmod 2775 "$MEDIA_DIR" 2>/dev/null || true

    # Prefer symlink when Asterisk can traverse MEDIA_DIR; otherwise use a bind mount (common when repo is under /root).
    local use_bind_mount=false
    if [ -d "$ASTERISK_SOUNDS_DIR" ] && id asterisk &>/dev/null; then
        if command -v sudo >/dev/null 2>&1; then
            if ! sudo -u asterisk test -x "$MEDIA_DIR" 2>/dev/null; then
                use_bind_mount=true
            fi
        else
            if ! su -s /bin/bash -c "test -x '$MEDIA_DIR'" asterisk 2>/dev/null; then
                use_bind_mount=true
            fi
        fi
    fi

    # Best-effort: create/update symlink or bind mount so `sound:ai-generated/...` resolves on the Asterisk host.
    if [ -d "$ASTERISK_SOUNDS_DIR" ]; then
        if [ "$use_bind_mount" = true ]; then
            print_warning "Asterisk user cannot access $MEDIA_DIR; using bind mount at $ASTERISK_SOUNDS_LINK (avoids /root traversal)"

            # Replace any existing symlink at the mountpoint.
            if [ -L "$ASTERISK_SOUNDS_LINK" ]; then
                $SUDO rm -f "$ASTERISK_SOUNDS_LINK" 2>/dev/null || true
            fi
            $SUDO mkdir -p "$ASTERISK_SOUNDS_LINK" 2>/dev/null || true

            if command -v mountpoint >/dev/null 2>&1 && mountpoint -q "$ASTERISK_SOUNDS_LINK" 2>/dev/null; then
                print_success "Bind mount already active: $ASTERISK_SOUNDS_LINK"
            else
                $SUDO mount --bind "$MEDIA_DIR" "$ASTERISK_SOUNDS_LINK" 2>/dev/null || true
                if command -v mountpoint >/dev/null 2>&1 && mountpoint -q "$ASTERISK_SOUNDS_LINK" 2>/dev/null; then
                    print_success "Bind mounted $ASTERISK_SOUNDS_LINK -> $MEDIA_DIR"
                else
                    print_warning "Could not create bind mount (may need sudo): $ASTERISK_SOUNDS_LINK"
                    print_info "  Try: $SUDO mount --bind '$MEDIA_DIR' '$ASTERISK_SOUNDS_LINK'"
                fi
            fi

	            # Persist bind mount across reboots via /etc/fstab (systemd-friendly when available).
	            if [ -f /etc/fstab ]; then
	                local options="bind,nofail"
	                if command -v systemctl >/dev/null 2>&1; then
	                    options="bind,nofail,x-systemd.automount"
	                fi
	                local fstab_line="$MEDIA_DIR $ASTERISK_SOUNDS_LINK none $options 0 0"
	                local existing_entry=""
	                existing_entry="$(awk -v mp="$ASTERISK_SOUNDS_LINK" '
	                    $0 ~ /^[[:space:]]*#/ { next }
	                    NF < 2 { next }
	                    $2 == mp { print; exit }
	                ' /etc/fstab 2>/dev/null || true)"

	                if [ -n "$existing_entry" ]; then
	                    local existing_src=""
	                    existing_src="$(echo "$existing_entry" | awk '{print $1}' 2>/dev/null || true)"
	                    if [ -n "$existing_src" ] && [ "$existing_src" != "$MEDIA_DIR" ]; then
	                        print_warning "/etc/fstab already has an entry for $ASTERISK_SOUNDS_LINK but points to a different source"
	                        print_info "  Existing: $existing_entry"
	                        print_info "  Desired:  $fstab_line"
	                        if [ -t 0 ]; then
	                            read -r -p "Update /etc/fstab to use the desired source? [y/N] " ans
	                            if [[ "$ans" =~ ^[Yy]$ ]]; then
	                                local backup="/etc/fstab.aava.bak.$(date +%Y%m%d_%H%M%S)"
	                                $SUDO cp /etc/fstab "$backup" 2>/dev/null || true
	                                tmpfile="$(mktemp 2>/dev/null || echo /tmp/aava-fstab.$$)"
	                                awk -v mp="$ASTERISK_SOUNDS_LINK" -v newline="$fstab_line" '
	                                    $0 ~ /^[[:space:]]*#/ { print; next }
	                                    NF < 2 { print; next }
	                                    $2 == mp && !replaced { print newline; replaced=1; next }
	                                    { print }
	                                ' /etc/fstab > "$tmpfile"
	                                if $SUDO cp "$tmpfile" /etc/fstab; then
	                                    print_success "Updated /etc/fstab (backup: $backup)"
	                                    if command -v systemctl >/dev/null 2>&1; then
	                                        $SUDO systemctl daemon-reload >/dev/null 2>&1 || true
	                                    fi
	                                else
	                                    print_warning "Failed to update /etc/fstab; bind mount may not persist after reboot"
	                                    print_info "  Add manually to /etc/fstab:"
	                                    print_info "    $fstab_line"
	                                fi
	                                rm -f "$tmpfile" 2>/dev/null || true
	                            fi
	                        fi
	                    fi
	                else
	                    if {
	                        echo ""
	                        echo "# AAVA: expose generated audio to Asterisk (bind mount)"
	                        echo "$fstab_line"
	                    } | $SUDO tee -a /etc/fstab >/dev/null; then
	                        print_success "Persisted bind mount in /etc/fstab"
	                        if command -v systemctl >/dev/null 2>&1; then
	                            $SUDO systemctl daemon-reload >/dev/null 2>&1 || true
	                        fi
	                    else
	                        print_warning "Failed to update /etc/fstab; bind mount may not persist after reboot"
	                        print_info "  Add manually to /etc/fstab:"
	                        print_info "    $fstab_line"
	                    fi
	                fi
	            else
	                print_warning "/etc/fstab not found; cannot persist bind mount after reboot"
	            fi
        elif [ -e "$ASTERISK_SOUNDS_LINK" ] && [ ! -L "$ASTERISK_SOUNDS_LINK" ]; then
            print_warning "Asterisk sounds path exists but is not a symlink: $ASTERISK_SOUNDS_LINK"
            print_info "  Fix manually: $SUDO mv '$ASTERISK_SOUNDS_LINK' '${ASTERISK_SOUNDS_LINK}.bak' && $SUDO ln -sf '$MEDIA_DIR' '$ASTERISK_SOUNDS_LINK'"
        else
            $SUDO ln -sfn "$MEDIA_DIR" "$ASTERISK_SOUNDS_LINK" || true
            print_success "Linked $ASTERISK_SOUNDS_LINK -> $MEDIA_DIR"
        fi
    else
        print_warning "Asterisk sounds directory not found at $ASTERISK_SOUNDS_DIR (skipping symlink)"
        print_info "  If Asterisk is installed elsewhere, ensure `ai-generated` is available under your sounds directory."
    fi

    # Quick verification
    if [ -d "$MEDIA_DIR" ]; then
        print_success "Media directory ready: $MEDIA_DIR"
    else
        print_warning "Media directory missing; please ensure permissions and rerun setup."
    fi
}

# --- Data directory setup (for call history DB) ---
setup_data_directory() {
    print_info "Setting up data directory for call history..."
    
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    DATA_DIR="$SCRIPT_DIR/data"
    
    if [ -d "$DATA_DIR" ] && [ -w "$DATA_DIR" ]; then
        print_success "Data directory ready: $DATA_DIR"
    else
        if [ ! -d "$DATA_DIR" ]; then
            mkdir -p "$DATA_DIR"
            chmod 775 "$DATA_DIR"
            print_success "Created data directory: $DATA_DIR"
        else
            chmod 775 "$DATA_DIR"
            print_success "Fixed data directory permissions: $DATA_DIR"
        fi
    fi
    
    # Ensure .gitkeep exists to maintain directory in git
    if [ ! -f "$DATA_DIR/.gitkeep" ]; then
        touch "$DATA_DIR/.gitkeep"
        print_info "Created .gitkeep placeholder in data directory"
    fi
    
    # Handle SELinux context on RHEL-family systems (Sangoma/FreePBX)
    if command -v getenforce &>/dev/null; then
        SELINUX_MODE=$(getenforce 2>/dev/null || echo "Disabled")
        if [ "$SELINUX_MODE" = "Enforcing" ]; then
            print_info "SELinux is Enforcing - setting container context for data directory..."
            if command -v semanage &>/dev/null; then
                # Add SELinux context for container access
                $SUDO semanage fcontext -a -t container_file_t "$DATA_DIR(/.*)?" 2>/dev/null || true
                $SUDO restorecon -Rv "$DATA_DIR" 2>/dev/null || true
                print_success "SELinux context applied to data directory"
            else
                print_warning "semanage not found - SELinux context not set"
                print_info "  Install with: $SUDO yum install -y policycoreutils-python-utils"
                print_info "  Then run: $SUDO semanage fcontext -a -t container_file_t '$DATA_DIR(/.*)?'"
                print_info "            $SUDO restorecon -Rv '$DATA_DIR'"
            fi
        fi
    fi
    
    # Verify
    if [ -d "$DATA_DIR" ] && [ -w "$DATA_DIR" ]; then
        print_success "Data directory ready for call history DB"
        # Best-effort: validate we can create an SQLite file inside the data directory.
        if command -v python3 >/dev/null 2>&1; then
            if python3 - "$DATA_DIR" <<'PY' 2>/dev/null; then
import os, sqlite3, sys
data_dir = sys.argv[1]
path = os.path.join(data_dir, ".call_history_sqlite_test.db")
conn = sqlite3.connect(path, timeout=1.0)
conn.execute("CREATE TABLE IF NOT EXISTS __install_test (id INTEGER PRIMARY KEY)")
conn.commit()
conn.close()
os.remove(path)
PY
                print_success "Call history DB: writable (SQLite test passed)"
            else
                print_warning "Call history DB: may fail (SQLite file test failed)"
                print_info "  Common causes: permissions, SELinux contexts, or non-local filesystems that break SQLite locking"
            fi
        fi
    else
        print_warning "Data directory may not be writable; call history will NOT be recorded!"
    fi
}

setup_models_directory() {
    print_info "Setting up models directory for local AI..."

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    MODELS_DIR="$SCRIPT_DIR/models"
    CONTAINER_UID=1000

    # Resolve asterisk group for shared access (best-effort).
    AST_GID=$(id -g asterisk 2>/dev/null || echo 995)

    # Create expected layout (Admin UI downloads expect these paths).
    $SUDO mkdir -p "$MODELS_DIR/stt" "$MODELS_DIR/tts" "$MODELS_DIR/llm" "$MODELS_DIR/kroko" || true

    # Ensure the appuser inside containers can write new model files.
    $SUDO chown "$CONTAINER_UID:$AST_GID" "$MODELS_DIR" "$MODELS_DIR/stt" "$MODELS_DIR/tts" "$MODELS_DIR/llm" "$MODELS_DIR/kroko" 2>/dev/null || true
    $SUDO chmod 2775 "$MODELS_DIR" "$MODELS_DIR/stt" "$MODELS_DIR/tts" "$MODELS_DIR/llm" "$MODELS_DIR/kroko" 2>/dev/null || true

    # Handle SELinux context on RHEL-family systems (Sangoma/FreePBX)
    if command -v getenforce &>/dev/null; then
        SELINUX_MODE=$(getenforce 2>/dev/null || echo "Disabled")
        if [ "$SELINUX_MODE" = "Enforcing" ]; then
            print_info "SELinux is Enforcing - setting container context for models directory..."
            if command -v semanage &>/dev/null; then
                # Add SELinux context for container access
                $SUDO semanage fcontext -a -t container_file_t "$MODELS_DIR(/.*)?" 2>/dev/null || true
                $SUDO restorecon -Rv "$MODELS_DIR" 2>/dev/null || true
                print_success "SELinux context applied to models directory"
            else
                print_warning "semanage not found - SELinux context not set"
                print_info "  Install with: $SUDO yum install -y policycoreutils-python-utils"
                print_info "  Then run: $SUDO semanage fcontext -a -t container_file_t '$MODELS_DIR(/.*)?'"
                print_info "            $SUDO restorecon -Rv '$MODELS_DIR'"
            fi
        fi
    fi

    if [ -d "$MODELS_DIR" ]; then
        print_success "Models directory ready: $MODELS_DIR"
    else
        print_warning "Models directory missing; local AI setup may fail (expected: $MODELS_DIR)"
        print_info "  Tip: Run: sudo ./preflight.sh --apply-fixes"
    fi
}

print_success() {
    echo -e "${COLOR_GREEN}SUCCESS: $1${COLOR_RESET}"
}

print_warning() {
    echo -e "${COLOR_YELLOW}WARNING: $1${COLOR_RESET}"
}

print_error() {
    echo -e "${COLOR_RED}ERROR: $1${COLOR_RESET}"
}

# --- ARI Validation ---
validate_ari_connection() {
    local host="$1"
    local port="${2:-8088}"
    local user="$3"
    local pass="$4"
    local scheme="${5:-http}"
    local ssl_verify="${6:-true}"
    
    local curl_ssl_flags=()
    if [ "$scheme" = "https" ] && [ "$ssl_verify" != "true" ]; then
        curl_ssl_flags=(-k)
    fi
    
    print_info "Testing ARI connection to ${scheme}://$host:$port..."
    
    local response
    response=$(curl -sf "${curl_ssl_flags[@]}" -u "$user:$pass" "${scheme}://$host:$port/ari/asterisk/info" 2>&1)
    local curl_exit=$?
    
    if [ $curl_exit -eq 0 ] && [ -n "$response" ]; then
        # Try to extract Asterisk version
        local version=$(echo "$response" | grep -o '"version":"[^"]*' | cut -d'"' -f4)
        print_success "ARI connection successful"
        if [ -n "$version" ]; then
            echo "  Asterisk version: $version"
        fi
        return 0
    else
        print_error "ARI connection failed"
        echo ""
        echo "Troubleshooting steps:"
        echo "  1. Check Asterisk is running:"
        echo "     systemctl status asterisk"
        echo "     (or: docker ps | grep asterisk)"
        echo ""
        echo "  2. Verify ARI is enabled in /etc/asterisk/ari.conf:"
        echo "     [general]"
        echo "     enabled = yes"
        echo ""
        echo "  3. Test connection manually:"
        echo "     curl -u $user:**** ${scheme}://$host:$port/ari/asterisk/info"
        if [ "$scheme" = "https" ] && [ "$ssl_verify" != "true" ]; then
            echo "     # (self-signed / hostname mismatch)"
            echo "     curl -k -u $user:**** ${scheme}://$host:$port/ari/asterisk/info"
        fi
        echo ""
        print_warning "Setup will continue, but calls may fail without working ARI"
        echo ""
        return 1
    fi
}

# --- System Checks ---
check_docker() {
    print_info "Checking for Docker..."
    if ! command -v docker &> /dev/null; then
        print_error "Docker is not installed. Please install Docker."
        exit 1
    fi
    if ! docker info &> /dev/null; then
        print_error "Docker daemon is not running. Please start Docker."
        exit 1
    fi
    print_success "Docker is installed and running."
}

choose_compose_cmd() {
    if command -v docker-compose >/dev/null 2>&1; then
        COMPOSE="docker-compose -p asterisk-ai-voice-agent"
    elif docker compose version >/dev/null 2>&1; then
        COMPOSE="docker compose -p asterisk-ai-voice-agent"
    else
        print_error "Neither 'docker-compose' nor 'docker compose' is available. Please install Docker Compose."
        exit 1
    fi
    print_info "Using Compose command: $COMPOSE"
}

check_asterisk_modules() {
    if ! command -v asterisk >/dev/null 2>&1; then
        print_warning "Asterisk CLI not found. Skipping Asterisk module checks."
        return
    fi
    print_info "Checking Asterisk modules (res_ari_applications, app_audiosocket)..."
    asterisk -rx "module show like res_ari_applications" || true
    asterisk -rx "module show like app_audiosocket" || true
    print_info "If modules are not Running, on FreePBX use: asterisk-switch-version (select 18+)."
}

maybe_run_preflight() {
    # Install.sh is an interactive wizard; preflight.sh is the canonical system readiness checker.
    if [ "${INSTALL_NONINTERACTIVE:-0}" = "1" ]; then
        return 0
    fi
    if [ ! -f "./preflight.sh" ]; then
        print_warning "preflight.sh not found; skipping preflight step"
        return 0
    fi

    echo ""
    print_info "Recommended: run preflight checks before install.sh (creates .env, fixes permissions, detects rootless Docker, etc.)"
    read -p "Run preflight now (recommended)? [Y/n]: " run_pf
    run_pf="${run_pf:-Y}"
    if [[ "$run_pf" =~ ^[Yy]$ ]]; then
        echo ""
        print_info "Running: ${SUDO} ./preflight.sh --apply-fixes"
        ${SUDO} ./preflight.sh --apply-fixes
        local rc=$?
        if [ "$rc" -eq 2 ]; then
            print_warning "Preflight reported failures (exit code 2). Fix the failures above for best results."
            read -p "Continue install.sh anyway? [y/N]: " continue_anyway
            if [[ ! "$continue_anyway" =~ ^[Yy]$ ]]; then
                exit 2
            fi
        fi
    else
        print_info "Skipping preflight. If you hit permission or Docker issues, run: sudo ./preflight.sh --apply-fixes"
    fi
}

# --- Env file helpers ---
ensure_env_file() {
    if [ ! -f .env ]; then
        if [ -f .env.example ]; then
            cp .env.example .env
            print_success "Created .env from .env.example"
        else
            print_error ".env.example not found. Cannot create .env"
            exit 1
        fi
    else
        print_info ".env already exists; values will be updated in-place."
    fi
}

upsert_env() {
    local KEY="$1"; shift
    local VAL="$1"; shift
    # Replace existing (even if commented) or append
    if grep -qE "^[# ]*${KEY}=" .env; then
        sed -i.bak -E "s|^[# ]*${KEY}=.*|${KEY}=${VAL}|" .env
    else
        echo "${KEY}=${VAL}" >> .env
    fi
}

ensure_docker_sock_env() {
    # Admin UI container management requires a Docker API socket mounted at /var/run/docker.sock
    # inside the admin_ui container. docker-compose.yml mounts:
    #   ${DOCKER_SOCK:-/var/run/docker.sock}:/var/run/docker.sock
    #
    # On rootless Docker/Podman, /var/run/docker.sock is usually absent; we must persist DOCKER_SOCK in .env.
    if [ ! -f .env ]; then
        return 0
    fi

    local current_sock=""
    current_sock="$(grep -E '^[# ]*DOCKER_SOCK=' .env 2>/dev/null | tail -n1 | sed -E 's/^[# ]*DOCKER_SOCK=//')"
    current_sock="$(echo "$current_sock" | tr -d '\r' | xargs 2>/dev/null || echo "$current_sock")"

    # If an explicit socket is already configured and valid, keep it.
    if [ -n "$current_sock" ] && [ -S "$current_sock" ]; then
        return 0
    fi

    local desired_sock=""
    if [ -n "${DOCKER_HOST:-}" ] && [[ "${DOCKER_HOST}" == unix://* ]]; then
        desired_sock="${DOCKER_HOST#unix://}"
    elif [ -n "${XDG_RUNTIME_DIR:-}" ] && [ -S "$XDG_RUNTIME_DIR/docker.sock" ]; then
        desired_sock="$XDG_RUNTIME_DIR/docker.sock"
    elif [ -S "/run/user/$(id -u)/docker.sock" ]; then
        desired_sock="/run/user/$(id -u)/docker.sock"
    elif command -v docker >/dev/null 2>&1 && docker --version 2>/dev/null | grep -qi "podman"; then
        if [ -n "${XDG_RUNTIME_DIR:-}" ] && [ -S "$XDG_RUNTIME_DIR/podman/podman.sock" ]; then
            desired_sock="$XDG_RUNTIME_DIR/podman/podman.sock"
        fi
    fi

    if [ -n "$desired_sock" ] && [ "$desired_sock" != "/var/run/docker.sock" ]; then
        upsert_env DOCKER_SOCK "$desired_sock"
        rm -f .env.bak 2>/dev/null || true
        print_info "Detected rootless container runtime; set DOCKER_SOCK in .env for Admin UI container control"
        print_info "  DOCKER_SOCK=$desired_sock"
        print_info "  If admin_ui is already running, recreate it to apply the mount:"
        print_info "  docker compose -p asterisk-ai-voice-agent up -d --force-recreate admin_ui"
    fi
}

# Ensure yq exists on Ubuntu/CentOS, otherwise try to install a static binary; fallback will be used if all fail.
ensure_yq() {
    if command -v yq >/dev/null 2>&1; then
        return 0
    fi
    print_info "yq not found; attempting installation..."
    if command -v apt-get >/dev/null 2>&1; then
        $SUDO apt-get update && $SUDO apt-get install -y yq || true
    elif command -v yum >/dev/null 2>&1; then
        $SUDO yum -y install epel-release || true
        $SUDO yum -y install yq || true
    elif command -v dnf >/dev/null 2>&1; then
        $SUDO dnf -y install yq || true
    elif command -v snap >/dev/null 2>&1; then
        $SUDO snap install yq || true
    fi
    if command -v yq >/dev/null 2>&1; then
        print_success "yq installed."
        return 0
    fi
    # Download static binary as last resort (detect OS/ARCH)
    print_info "Falling back to installing yq static binary..."
    ARCH=$(uname -m)
    OS=$(uname -s | tr '[:upper:]' '[:lower:]')
    case "${OS}-${ARCH}" in
        linux-x86_64|linux-amd64) YQ_BIN="yq_linux_amd64" ;;
        linux-aarch64|linux-arm64) YQ_BIN="yq_linux_arm64" ;;
        darwin-x86_64|darwin-amd64) YQ_BIN="yq_darwin_amd64" ;;
        darwin-arm64) YQ_BIN="yq_darwin_arm64" ;;
        *) YQ_BIN="yq_linux_amd64" ;;
    esac
    TMP_YQ="/tmp/${YQ_BIN}"
    if command -v curl >/dev/null 2>&1; then
        curl -L "https://github.com/mikefarah/yq/releases/latest/download/${YQ_BIN}" -o "$TMP_YQ" || true
    elif command -v wget >/dev/null 2>&1; then
        wget -O "$TMP_YQ" "https://github.com/mikefarah/yq/releases/latest/download/${YQ_BIN}" || true
    fi
    if [ -f "$TMP_YQ" ]; then
        $SUDO mv "$TMP_YQ" /usr/local/bin/yq && $SUDO chmod +x /usr/local/bin/yq || true
    fi
    if command -v yq >/dev/null 2>&1; then
        print_success "yq installed (static)."
        return 0
    fi
    print_warning "yq could not be installed; will use sed/awk fallback."
    return 1
}

# Update config/ai-agent.yaml llm block with GREETING and AI_ROLE.
update_yaml_llm() {
    local CFG_DST="config/ai-agent.yaml"
    if [ ! -f "$CFG_DST" ]; then
        print_warning "YAML not found at $CFG_DST; skipping llm update."
        return 0
    fi
    if command -v yq >/dev/null 2>&1; then
        # Use env() in yq to avoid quoting issues
        GREETING="${GREETING}"
        AI_ROLE="${AI_ROLE}"
        export GREETING AI_ROLE
        
        # Update fields separately for better error handling
        if yq -i '.llm.initial_greeting = env(GREETING)' "$CFG_DST" 2>/dev/null && \
           yq -i '.llm.prompt = env(AI_ROLE)' "$CFG_DST" 2>/dev/null && \
           yq -i '.llm.model //= "gpt-4o"' "$CFG_DST" 2>/dev/null; then
            print_success "Updated llm.* in $CFG_DST via yq."
            return 0
        else
            print_warning "yq update failed (check yq version >= 4.x). Using fallback method..."
        fi
    fi
    # Fallback: append an llm block at end (last key wins in PyYAML)
    local G_ESC
    local R_ESC
    G_ESC=$(printf '%s' "$GREETING" | sed 's/"/\\"/g')
    R_ESC=$(printf '%s' "$AI_ROLE" | sed 's/"/\\"/g')
    cat >> "$CFG_DST" <<EOF

# llm block inserted by install.sh (fallback path)
llm:
  initial_greeting: "$G_ESC"
  prompt: "$R_ESC"
  model: "gpt-4o"
EOF
    print_success "Appended llm block to $CFG_DST (fallback)."
}

# --- Local model helpers ---
autodetect_local_models() {
    print_info "Auto-detecting local model artifacts under ./models to set .env paths..."
    local stt="" llm="" tts=""

    local has_gpu=0
    if command -v nvidia-smi >/dev/null 2>&1; then
        if nvidia-smi -L >/dev/null 2>&1; then
            has_gpu=1
        fi
    elif command -v rocm-smi >/dev/null 2>&1; then
        if rocm-smi -i >/dev/null 2>&1; then
            has_gpu=1
        fi
    fi
    # STT preference: 0.22 > small 0.15
    if [ -d models/stt/vosk-model-en-us-0.22 ]; then
        stt="/app/models/stt/vosk-model-en-us-0.22"
    elif [ -d models/stt/vosk-model-small-en-us-0.15 ]; then
        stt="/app/models/stt/vosk-model-small-en-us-0.15"
    fi
    # LLM preference: favor smaller GGUFs on CPU-only hosts for responsiveness
    if [ "$has_gpu" -eq 1 ]; then
        if [ -f models/llm/llama-2-13b-chat.Q4_K_M.gguf ]; then
            llm="/app/models/llm/llama-2-13b-chat.Q4_K_M.gguf"
        elif [ -f models/llm/llama-2-7b-chat.Q4_K_M.gguf ]; then
            llm="/app/models/llm/llama-2-7b-chat.Q4_K_M.gguf"
        elif [ -f models/llm/phi-3-mini-4k-instruct.Q4_K_M.gguf ]; then
            llm="/app/models/llm/phi-3-mini-4k-instruct.Q4_K_M.gguf"
        elif [ -f models/llm/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf ]; then
            llm="/app/models/llm/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf"
        fi
    else
        # Prefer TinyLlama first on CPU-only systems for best responsiveness.
        if [ -f models/llm/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf ]; then
            llm="/app/models/llm/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf"
        elif [ -f models/llm/phi-3-mini-4k-instruct.Q4_K_M.gguf ]; then
            llm="/app/models/llm/phi-3-mini-4k-instruct.Q4_K_M.gguf"
        elif [ -f models/llm/llama-2-7b-chat.Q4_K_M.gguf ]; then
            llm="/app/models/llm/llama-2-7b-chat.Q4_K_M.gguf"
        elif [ -f models/llm/llama-2-13b-chat.Q4_K_M.gguf ]; then
            llm="/app/models/llm/llama-2-13b-chat.Q4_K_M.gguf"
        fi
    fi
    # TTS preference: high > medium
    if [ -f models/tts/en_US-lessac-high.onnx ]; then
        tts="/app/models/tts/en_US-lessac-high.onnx"
    elif [ -f models/tts/en_US-lessac-medium.onnx ]; then
        tts="/app/models/tts/en_US-lessac-medium.onnx"
    fi

    if [ -n "$stt" ]; then upsert_env LOCAL_STT_MODEL_PATH "$stt"; fi
    if [ -n "$llm" ]; then upsert_env LOCAL_LLM_MODEL_PATH "$llm"; fi
    if [ -n "$tts" ]; then upsert_env LOCAL_TTS_MODEL_PATH "$tts"; fi

    # Set performance parameters based on detected tier
    set_performance_params_for_llm "$llm"

    # Clean sed backup if created
    [ -f .env.bak ] && rm -f .env.bak || true
    print_success "Local model paths and performance tuning updated in .env (if detected)."
}

set_performance_params_for_llm() {
    local llm_path="$1"
    
    # Skip if no LLM detected
    [ -z "$llm_path" ] && return 0
    
    # Determine tier based on model name
    local tier="LIGHT_CPU"
    if echo "$llm_path" | grep -q "tinyllama"; then
        tier="LIGHT_CPU"
    elif echo "$llm_path" | grep -q "phi-3-mini"; then
        tier="MEDIUM_CPU"
    elif echo "$llm_path" | grep -q "llama-2-7b"; then
        tier="HEAVY_CPU"
    elif echo "$llm_path" | grep -q "llama-2-13b"; then
        tier="HEAVY_GPU"
    fi
    
    print_info "Setting performance parameters for tier: $tier"
    
    # Set tier-appropriate parameters
    case "$tier" in
        LIGHT_CPU)
            upsert_env LOCAL_LLM_CONTEXT "512"
            upsert_env LOCAL_LLM_BATCH "512"
            upsert_env LOCAL_LLM_MAX_TOKENS "24"
            upsert_env LOCAL_LLM_TEMPERATURE "0.3"
            upsert_env LOCAL_LLM_INFER_TIMEOUT_SEC "45"
            print_info "  → Context: 512, Max tokens: 24, Timeout: 45s (conservative for older CPUs)"
            ;;
        MEDIUM_CPU)
            upsert_env LOCAL_LLM_CONTEXT "512"
            upsert_env LOCAL_LLM_BATCH "512"
            upsert_env LOCAL_LLM_MAX_TOKENS "32"
            upsert_env LOCAL_LLM_TEMPERATURE "0.3"
            upsert_env LOCAL_LLM_INFER_TIMEOUT_SEC "30"
            print_info "  → Context: 512, Max tokens: 32, Timeout: 30s (optimized for Phi-3-mini)"
            ;;
        HEAVY_CPU)
            # Conservative settings - use Phi-3 params even for HEAVY_CPU
            # Llama-2-7B often too slow without modern CPU features (AVX-512)
            upsert_env LOCAL_LLM_CONTEXT "512"
            upsert_env LOCAL_LLM_BATCH "512"
            upsert_env LOCAL_LLM_MAX_TOKENS "28"
            upsert_env LOCAL_LLM_TEMPERATURE "0.3"
            upsert_env LOCAL_LLM_INFER_TIMEOUT_SEC "35"
            print_info "  → Context: 512, Max tokens: 28, Timeout: 35s (conservative for reliability)"
            ;;
        HEAVY_GPU)
            upsert_env LOCAL_LLM_CONTEXT "1024"
            upsert_env LOCAL_LLM_BATCH "512"
            upsert_env LOCAL_LLM_MAX_TOKENS "48"
            upsert_env LOCAL_LLM_TEMPERATURE "0.3"
            upsert_env LOCAL_LLM_INFER_TIMEOUT_SEC "20"
            print_info "  → Context: 1024, Max tokens: 48, Timeout: 20s (optimized for GPU acceleration)"
            ;;
    esac
}

wait_for_local_ai_health() {
    print_info "Waiting for local_ai_server to become ready (port 8765)..."
    echo ""
    echo "⏳ First-run model download may take 5-10 minutes..."
    echo "📋 Monitor progress in another terminal:"
    echo "   $COMPOSE logs -f local_ai_server | grep -E 'model|Server started'"
    echo ""
    
    # Ensure service started (build if needed)
    print_info "Starting local_ai_server container..."
    $COMPOSE up -d --build local_ai_server
    echo ""
    
    # Wait up to 10 minutes (60 iterations * 10s)
    # We actively check if WebSocket is responding, not just Docker health status
    local max_wait=60
    local check_interval=10
    
    print_info "🔍 Checking local AI server status..."
    echo ""

    local i
    for ((i=1; i<=max_wait; i++)); do
        # Check 1: Container running
        if ! docker ps --format '{{.Names}}' | grep -qx "local_ai_server"; then
            echo ""  # Clear the progress line
            print_warning "local_ai_server container is not running yet..."
            sleep $check_interval
            continue
        fi

        # Check 2: Port is open AND logs indicate the service has started
        # We use a lightweight port check first, then corroborate with logs.
        local port_open="false"
        if command -v nc >/dev/null 2>&1; then
            nc -z 127.0.0.1 8765 >/dev/null 2>&1 && port_open="true"
        else
            (echo > /dev/tcp/127.0.0.1/8765) >/dev/null 2>&1 && port_open="true"
        fi

        if [ "$port_open" = "true" ] && docker logs local_ai_server 2>&1 | tail -100 | grep -Eqi "listening on|server started|uvicorn running|application startup complete"; then
            echo ""  # Clear the progress line
            print_success "✅ local_ai_server is ready and listening on port 8765"
            return 0
        fi
        
        # Check 3: Fallback to Docker health status (in case log format changed)
        local status=$(docker inspect -f '{{.State.Health.Status}}' local_ai_server 2>/dev/null || echo "starting")
        if [ "$status" = "healthy" ]; then
            echo ""  # Clear the progress line
            print_success "✅ local_ai_server is healthy (Docker health check passed)"
            return 0
        fi
        
        # Show progress every iteration (every 10s) with live log hints
        local elapsed=$((i * check_interval))
        local last_log=$(docker logs local_ai_server 2>&1 | tail -1 | cut -c1-80)
        
        if docker logs local_ai_server 2>&1 | tail -3 | grep -qi "loading\|model"; then
            echo -n "📥 Loading models... (${elapsed}s) - $(echo "$last_log" | grep -o "model\|STT\|LLM\|TTS" | head -1)"
        elif docker logs local_ai_server 2>&1 | tail -3 | grep -qi "error"; then
            echo -n "⚠️  Checking status... (${elapsed}s) - check logs for errors"
        else
            echo -n "⏳ Waiting for models to load... (${elapsed}s)"
        fi
        echo -ne "\r"
        
        # Detailed progress every minute
        if (( i % 6 == 0 )); then
            echo ""  # New line for cleaner output
            local elapsed_min=$((elapsed / 60))
            print_info "Still waiting (${elapsed_min} min elapsed)..."
            
            # Show last few log lines for context
            echo "   Recent activity:"
            docker logs local_ai_server 2>&1 | tail -3 | sed 's/^/   /' | cut -c1-100
            echo ""
        fi
        
        sleep $check_interval
    done
    
    # Timeout after 10 minutes
    echo ""
    print_warning "⚠️  local_ai_server did not become ready within 10 minutes"
    echo ""
    echo "Last 20 log lines:"
    docker logs local_ai_server 2>&1 | tail -20
    echo ""
    echo "Common issues:"
    echo "  • Models still downloading (first run: check models/ directory size)"
    echo "  • Insufficient RAM (requires 8GB+, 16GB recommended)"
    echo "  • Model files corrupted (rm -rf models/; re-run install.sh)"
    echo ""
    echo "Debug commands:"
    echo "  $COMPOSE logs local_ai_server | grep -E 'model|error|ERROR'"
    echo "  docker stats local_ai_server --no-stream"
    echo "  du -sh models/*"
    echo ""
    
    read -p "Continue anyway? [y/N]: " continue_anyway
    if [[ "$continue_anyway" =~ ^[Yy]$ ]]; then
        print_warning "Continuing without confirmed local_ai_server health..."
        return 0
    fi
    
    return 1
}

# --- Configuration ---
configure_env() {
    # Support non-interactive mode for CI/CD
    if [ "${INSTALL_NONINTERACTIVE:-0}" = "1" ]; then
        print_info "Running in non-interactive mode (INSTALL_NONINTERACTIVE=1)"
        ensure_env_file
        print_info "Using existing .env configuration or defaults"
        return 0
    fi
    
    print_info "Starting interactive configuration (.env updates)..."
    ensure_env_file
    ensure_docker_sock_env

    # Ensure JWT_SECRET exists for remotely accessible Admin UI (binds to 0.0.0.0 by default).
    local CURRENT_JWT_SECRET
    CURRENT_JWT_SECRET=$(grep -E '^[# ]*JWT_SECRET=' .env 2>/dev/null | tail -n1 | sed -E 's/^[# ]*JWT_SECRET=//')
    if [ -z "$CURRENT_JWT_SECRET" ] || [ "$CURRENT_JWT_SECRET" = "change-me-please" ] || [ "$CURRENT_JWT_SECRET" = "changeme" ]; then
        local NEW_JWT_SECRET=""
        if command -v openssl >/dev/null 2>&1; then
            NEW_JWT_SECRET=$(openssl rand -hex 32 2>/dev/null || true)
        fi
        if [ -z "$NEW_JWT_SECRET" ] && command -v python3 >/dev/null 2>&1; then
            NEW_JWT_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))' 2>/dev/null || true)
        fi
        if [ -n "$NEW_JWT_SECRET" ]; then
            upsert_env JWT_SECRET "$NEW_JWT_SECRET"
            print_success "Generated JWT_SECRET for Admin UI"
            print_warning "SECURITY: Admin UI is accessible on :3003 by default; change admin password on first login and restrict port 3003 (firewall/VPN)"
        else
            print_warning "Could not auto-generate JWT_SECRET; set it in .env (openssl rand -hex 32)"
        fi
    fi

    # Prefill from existing .env if present
    local ASTERISK_HOST_DEFAULT="" ASTERISK_ARI_USERNAME_DEFAULT="" ASTERISK_ARI_PASSWORD_DEFAULT=""
    local ASTERISK_ARI_PORT_DEFAULT="" ASTERISK_ARI_SCHEME_DEFAULT="" ASTERISK_ARI_SSL_VERIFY_DEFAULT=""
    # API key defaults need to be GLOBAL so prompt_required_api_keys() can access them
    OPENAI_API_KEY_DEFAULT=""
    DEEPGRAM_API_KEY_DEFAULT=""
    if [ -f .env ]; then
        ASTERISK_HOST_DEFAULT=$(grep -E '^[# ]*ASTERISK_HOST=' .env | tail -n1 | sed -E 's/^[# ]*ASTERISK_HOST=//')
        ASTERISK_ARI_USERNAME_DEFAULT=$(grep -E '^[# ]*ASTERISK_ARI_USERNAME=' .env | tail -n1 | sed -E 's/^[# ]*ASTERISK_ARI_USERNAME=//')
        ASTERISK_ARI_PASSWORD_DEFAULT=$(grep -E '^[# ]*ASTERISK_ARI_PASSWORD=' .env | tail -n1 | sed -E 's/^[# ]*ASTERISK_ARI_PASSWORD=//')
        ASTERISK_ARI_PORT_DEFAULT=$(grep -E '^[# ]*ASTERISK_ARI_PORT=' .env | tail -n1 | sed -E 's/^[# ]*ASTERISK_ARI_PORT=//')
        ASTERISK_ARI_SCHEME_DEFAULT=$(grep -E '^[# ]*ASTERISK_ARI_SCHEME=' .env | tail -n1 | sed -E 's/^[# ]*ASTERISK_ARI_SCHEME=//')
        ASTERISK_ARI_SSL_VERIFY_DEFAULT=$(grep -E '^[# ]*ASTERISK_ARI_SSL_VERIFY=' .env | tail -n1 | sed -E 's/^[# ]*ASTERISK_ARI_SSL_VERIFY=//')
        OPENAI_API_KEY_DEFAULT=$(grep -E '^[# ]*OPENAI_API_KEY=' .env | tail -n1 | sed -E 's/^[# ]*OPENAI_API_KEY=//')
        DEEPGRAM_API_KEY_DEFAULT=$(grep -E '^[# ]*DEEPGRAM_API_KEY=' .env | tail -n1 | sed -E 's/^[# ]*DEEPGRAM_API_KEY=//')
    fi
    [ -z "$ASTERISK_ARI_PORT_DEFAULT" ] && ASTERISK_ARI_PORT_DEFAULT="8088"
    [ -z "$ASTERISK_ARI_SCHEME_DEFAULT" ] && ASTERISK_ARI_SCHEME_DEFAULT="http"
    [ -z "$ASTERISK_ARI_SSL_VERIFY_DEFAULT" ] && ASTERISK_ARI_SSL_VERIFY_DEFAULT="true"

    # Asterisk Connection Details
    echo ""
    echo "Asterisk Connection Configuration"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "ASTERISK_HOST determines how ai_engine connects to Asterisk ARI:"
    echo "  • 127.0.0.1 or localhost  - Asterisk on the SAME host (default)"
    echo "  • IP address              - Asterisk on a remote host (e.g., 192.168.1.100)"
    echo "  • Hostname/FQDN           - Remote via DNS (e.g., asterisk.example.com)"
    echo "  • Container name          - Containerized Asterisk on same Docker network"
    echo ""
    read -p "Enter Asterisk Host [${ASTERISK_HOST_DEFAULT:-127.0.0.1}]: " ASTERISK_HOST_INPUT
    ASTERISK_HOST=${ASTERISK_HOST_INPUT:-${ASTERISK_HOST_DEFAULT:-127.0.0.1}}
    
    read -p "Enter ARI Username [${ASTERISK_ARI_USERNAME_DEFAULT:-asterisk}]: " ASTERISK_ARI_USERNAME_INPUT
    ASTERISK_ARI_USERNAME=${ASTERISK_ARI_USERNAME_INPUT:-${ASTERISK_ARI_USERNAME_DEFAULT:-asterisk}}
    
    read -s -p "Enter ARI Password [unchanged if blank]: " ASTERISK_ARI_PASSWORD_INPUT
    echo
    if [ -n "$ASTERISK_ARI_PASSWORD_INPUT" ]; then
        ASTERISK_ARI_PASSWORD="$ASTERISK_ARI_PASSWORD_INPUT"
    else
        ASTERISK_ARI_PASSWORD="$ASTERISK_ARI_PASSWORD_DEFAULT"
    fi

    read -p "Enter ARI Port [${ASTERISK_ARI_PORT_DEFAULT}]: " ASTERISK_ARI_PORT_INPUT
    ASTERISK_ARI_PORT="${ASTERISK_ARI_PORT_INPUT:-$ASTERISK_ARI_PORT_DEFAULT}"

    read -p "Enter ARI Scheme (http/https) [${ASTERISK_ARI_SCHEME_DEFAULT}]: " ASTERISK_ARI_SCHEME_INPUT
    ASTERISK_ARI_SCHEME="${ASTERISK_ARI_SCHEME_INPUT:-$ASTERISK_ARI_SCHEME_DEFAULT}"
    if [ "$ASTERISK_ARI_SCHEME" != "http" ] && [ "$ASTERISK_ARI_SCHEME" != "https" ]; then
        print_warning "Invalid scheme '$ASTERISK_ARI_SCHEME' (expected http or https); defaulting to http"
        ASTERISK_ARI_SCHEME="http"
    fi

    ASTERISK_ARI_SSL_VERIFY="$ASTERISK_ARI_SSL_VERIFY_DEFAULT"
    if [ "$ASTERISK_ARI_SCHEME" = "https" ]; then
        read -p "Verify ARI SSL certificate? (true/false) [${ASTERISK_ARI_SSL_VERIFY_DEFAULT}]: " ASTERISK_ARI_SSL_VERIFY_INPUT
        ASTERISK_ARI_SSL_VERIFY="${ASTERISK_ARI_SSL_VERIFY_INPUT:-$ASTERISK_ARI_SSL_VERIFY_DEFAULT}"
        if [ "$ASTERISK_ARI_SSL_VERIFY" != "true" ] && [ "$ASTERISK_ARI_SSL_VERIFY" != "false" ]; then
            print_warning "Invalid ASTERISK_ARI_SSL_VERIFY '$ASTERISK_ARI_SSL_VERIFY' (expected true/false); defaulting to true"
            ASTERISK_ARI_SSL_VERIFY="true"
        fi
    fi

    # Validate ARI connection before proceeding
    echo ""
    validate_ari_connection "$ASTERISK_HOST" "$ASTERISK_ARI_PORT" "$ASTERISK_ARI_USERNAME" "$ASTERISK_ARI_PASSWORD" "$ASTERISK_ARI_SCHEME" "$ASTERISK_ARI_SSL_VERIFY"
    echo ""

    # API Keys are now handled by prompt_required_api_keys() based on chosen provider
    # This avoids duplicate prompts and only asks for what's needed
    
    upsert_env ASTERISK_HOST "$ASTERISK_HOST"
    upsert_env ASTERISK_ARI_PORT "$ASTERISK_ARI_PORT"
    upsert_env ASTERISK_ARI_SCHEME "$ASTERISK_ARI_SCHEME"
    upsert_env ASTERISK_ARI_SSL_VERIFY "$ASTERISK_ARI_SSL_VERIFY"
    upsert_env ASTERISK_ARI_USERNAME "$ASTERISK_ARI_USERNAME"
    upsert_env ASTERISK_ARI_PASSWORD "$ASTERISK_ARI_PASSWORD"
    # API keys are now set by prompt_required_api_keys() after provider selection

    # Greeting and AI Role prompts (idempotent; prefill from .env if present)
    local GREETING_DEFAULT AI_ROLE_DEFAULT
    if [ -f .env ]; then
        GREETING_DEFAULT=$(grep -E '^[# ]*GREETING=' .env | tail -n1 | sed -E 's/^[# ]*GREETING=//' | sed -E 's/^"(.*)"$/\1/')
        AI_ROLE_DEFAULT=$(grep -E '^[# ]*AI_ROLE=' .env | tail -n1 | sed -E 's/^[# ]*AI_ROLE=//' | sed -E 's/^"(.*)"$/\1/')
    fi
    [ -z "$GREETING_DEFAULT" ] && GREETING_DEFAULT="Hello, how can I help you today?"
    [ -z "$AI_ROLE_DEFAULT" ] && AI_ROLE_DEFAULT="You are a concise and helpful voice assistant. Keep replies under 20 words unless asked for detail."

    read -p "Enter initial Greeting [${GREETING_DEFAULT}]: " GREETING
    GREETING=${GREETING:-$GREETING_DEFAULT}
    read -p "Enter AI Role/Persona [${AI_ROLE_DEFAULT}]: " AI_ROLE
    AI_ROLE=${AI_ROLE:-$AI_ROLE_DEFAULT}

    # Escape quotes for .env
    local G_ESC R_ESC
    G_ESC=$(printf '%s' "$GREETING" | sed 's/"/\\"/g')
    R_ESC=$(printf '%s' "$AI_ROLE" | sed 's/"/\\"/g')
    upsert_env GREETING "\"$G_ESC\""
    upsert_env AI_ROLE "\"$R_ESC\""
    
    # Set proper default logging levels (console with colors for better out-of-box UX)
    upsert_env LOG_LEVEL "info"
    upsert_env STREAMING_LOG_LEVEL "info"
    upsert_env LOG_FORMAT "console"
    upsert_env LOG_COLOR "1"

    # Clean sed backup if created
    [ -f .env.bak ] && rm -f .env.bak || true

    print_success ".env updated."
    print_info "If you don't have API keys now, you can add them later to .env and then recreate containers: 'docker compose -p asterisk-ai-voice-agent up -d' (use '--build' if images changed). Note: simple 'restart' will not pick up new .env values."
}

select_config_template() {
    echo ""
    echo "╔═══════════════════════════════════════════════════════════╗"
    echo "║   Asterisk AI Voice Agent v4.6.0 - Configuration Setup   ║"
    echo "╚═══════════════════════════════════════════════════════════╝"
    echo ""
    echo "✨ ALL 3 AI voice pipelines will be enabled:"
    echo ""
    echo "  [1] OpenAI Realtime (Cloud)"
    echo "  [2] Deepgram Voice Agent (Cloud)"
    echo "  [3] Local Hybrid (Privacy-Focused)"
    echo ""
    echo "Select which pipeline should be ACTIVE by default:"
    echo ""
    echo "  [1] OpenAI Realtime"
    echo "      • Fastest setup, natural conversations"
    echo "      • Uses: OPENAI_API_KEY"
    echo ""
    echo "  [2] Deepgram Voice Agent"
    echo "      • Enterprise-grade with Think stage"
    echo "      • Uses: DEEPGRAM_API_KEY + OPENAI_API_KEY"
    echo ""
    echo "  [3] Local Hybrid"
    echo "      • Audio privacy, cost control"
    echo "      • Uses: OPENAI_API_KEY + local_ai_server"
    echo ""
    echo "💡 You can switch pipelines anytime by editing ai-agent.yaml"
    echo ""
    read -p "Enter your default pipeline [3]: " cfg_choice
    
    # Map choices to profiles and config files
    CFG_DST="config/ai-agent.yaml"
    # Always prompt for both cloud API keys since all pipelines are enabled
    NEEDS_OPENAI=1
    NEEDS_DEEPGRAM=1
    NEEDS_LOCAL=0
    
    case "$cfg_choice" in
        1)
            PROFILE="openai_realtime"
            ACTIVE_PROVIDER="openai_realtime"
            print_info "Default pipeline: OpenAI Realtime"
            ;;
        2)
            PROFILE="deepgram"
            ACTIVE_PROVIDER="deepgram"
            print_info "Default pipeline: Deepgram Voice Agent"
            ;;
        3|"")
            PROFILE="local_hybrid"
            ACTIVE_PROVIDER="local_hybrid"
            NEEDS_LOCAL=1  # Need local AI server setup
            print_info "Default pipeline: Local Hybrid"
            ;;
        *)
            print_error "Invalid choice. Please run ./install.sh again."
            exit 1
            ;;
    esac
    
    # Get full config from main branch baseline
    if [ ! -f "config/ai-agent.yaml" ]; then
        print_error "config/ai-agent.yaml not found. This indicates a corrupted installation."
        print_error "Please re-clone the repository."
        exit 1
    fi
    
    # Backup existing config if present
    if [ -f "$CFG_DST" ]; then
        cp "$CFG_DST" "${CFG_DST}.backup.$(date +%s)"
        print_info "Backed up existing config to ${CFG_DST}.backup.*"
    fi
    
    print_success "✅ All 3 pipelines enabled in ai-agent.yaml (default: $ACTIVE_PROVIDER)"
    
    # Smart API key prompting based on profile needs
    prompt_required_api_keys
    
    # Ensure yq is available and configure the chosen provider
    ensure_yq || true
    update_yaml_llm || true
    enable_chosen_provider
    
    # Handle local AI server setup (always ask, regardless of choice)
    prompt_local_ai_setup
}

# Smart API key prompting based on profile requirements
prompt_required_api_keys() {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "API Key Configuration (All Pipelines)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    print_info "Collecting API keys for all 3 enabled pipelines..."
    print_info "You can skip any key now and add it to .env later."
    
    # Check for OpenAI API key if needed
    if [ "$NEEDS_OPENAI" -eq 1 ]; then
        if [ -z "$OPENAI_API_KEY_DEFAULT" ] || [ "$OPENAI_API_KEY_DEFAULT" = "your-openai-api-key-here" ]; then
            echo ""
            print_warning "⚠️  OpenAI API Key Required"
            if [ "$PROFILE" = "local_hybrid" ]; then
                print_info "   (Used for LLM only - STT/TTS are local)"
            fi
            print_info "   Get your key at: https://platform.openai.com/api-keys"
            read -p "Enter your OpenAI API Key (or leave blank to skip): " OPENAI_API_KEY_INPUT
            if [ -n "$OPENAI_API_KEY_INPUT" ]; then
                upsert_env OPENAI_API_KEY "$OPENAI_API_KEY_INPUT"
                OPENAI_API_KEY_DEFAULT="$OPENAI_API_KEY_INPUT"  # Update in-memory variable
                print_success "✓ OpenAI API key configured"
            else
                print_warning "⚠️  Skipped. Add OPENAI_API_KEY to .env file later"
                print_warning "   Without it, $PROFILE will not work"
            fi
        else
            print_info "✓ Using existing OpenAI API key from .env"
        fi
    fi
    
    # Check for Deepgram API key if needed
    if [ "$NEEDS_DEEPGRAM" -eq 1 ]; then
        if [ -z "$DEEPGRAM_API_KEY_DEFAULT" ] || [ "$DEEPGRAM_API_KEY_DEFAULT" = "your-deepgram-api-key-here" ]; then
            echo ""
            print_warning "⚠️  Deepgram API Key Required"
            print_info "   Get your key at: https://console.deepgram.com/"
            read -p "Enter your Deepgram API Key (or leave blank to skip): " DEEPGRAM_API_KEY_INPUT
            if [ -n "$DEEPGRAM_API_KEY_INPUT" ]; then
                upsert_env DEEPGRAM_API_KEY "$DEEPGRAM_API_KEY_INPUT"
                DEEPGRAM_API_KEY_DEFAULT="$DEEPGRAM_API_KEY_INPUT"  # Update in-memory variable
                print_success "✓ Deepgram API key configured"
            else
                print_warning "⚠️  Skipped. Add DEEPGRAM_API_KEY to .env file later"
                print_warning "   Without it, Deepgram provider will not work"
            fi
        else
            print_info "✓ Using existing Deepgram API key from .env"
        fi
    fi
    
    # Info message for local-only setup
    if [ "$NEEDS_LOCAL" -eq 1 ]; then
        echo ""
        print_info "ℹ️  Local Hybrid mode selected"
        print_info "   • Audio stays local (privacy)"
        print_info "   • Only LLM calls use cloud API"
        print_info "   • Cost: ~$0.001-0.003 per minute"
    fi
}

# Enable the chosen provider and disable others in YAML
enable_chosen_provider() {
    local cfg="config/ai-agent.yaml"
    
    if ! command -v yq >/dev/null 2>&1; then
        print_warning "yq not available - skipping provider enable/disable"
        print_info "You can manually edit $cfg to enable your chosen provider"
        return 0
    fi
    
    echo ""
    print_info "Configuring $ACTIVE_PROVIDER as active provider..."
    
    # Set default_provider based on choice
    case "$ACTIVE_PROVIDER" in
        openai_realtime)
            yq -i '.default_provider = "openai_realtime"' "$cfg"
            yq -i '.providers.openai_realtime.enabled = true' "$cfg"
            yq -i '.providers.deepgram.enabled = false' "$cfg"
            # local provider state depends on local AI setup choice
            print_success "✓ OpenAI Realtime enabled"
            ;;
        deepgram)
            yq -i '.default_provider = "deepgram"' "$cfg"
            yq -i '.providers.deepgram.enabled = true' "$cfg"
            yq -i '.providers.openai_realtime.enabled = false' "$cfg"
            # local provider state depends on local AI setup choice
            print_success "✓ Deepgram Voice Agent enabled"
            ;;
        local_hybrid)
            yq -i '.active_pipeline = "local_hybrid"' "$cfg"
            yq -i '.default_provider = "local_hybrid"' "$cfg"
            yq -i '.providers.openai_realtime.enabled = false' "$cfg"
            yq -i '.providers.deepgram.enabled = false' "$cfg"
            yq -i '.providers.local.enabled = true' "$cfg"
            print_success "✓ Local Hybrid pipeline enabled"
            ;;
    esac
    
    echo ""
    print_info "ℹ️  Other providers are configured but disabled in ai-agent.yaml"
    print_info "   To switch providers later:"
    print_info "   1. Edit config/ai-agent.yaml"
    print_info "   2. Set providers.<provider>.enabled: true"
    print_info "   3. Ensure API keys are in .env file"
    print_info "   4. Run: docker compose -p asterisk-ai-voice-agent restart ai_engine"
}

# Prompt for local AI server setup
prompt_local_ai_setup() {
    echo ""
    echo "╔═══════════════════════════════════════════════════════════╗"
    echo "║          Local AI Server Setup (Optional)                ║"
    echo "╚═══════════════════════════════════════════════════════════╝"
    echo ""
    echo "The Local AI Server provides:"
    echo "  • Vosk STT (speech-to-text) - Privacy-focused transcription"
    echo "  • Piper TTS (text-to-speech) - Natural voice synthesis"
    echo "  • Phi-3 LLM (language model) - Local intelligence (optional)"
    echo ""
    echo "Required for:"
    echo "  • local_hybrid pipeline (Vosk + OpenAI + Piper)"
    echo "  • local_only pipeline (fully offline)"
    echo ""
    echo "System Requirements:"
    echo "  • 8GB+ RAM (16GB recommended)"
    echo "  • First startup: ~5-10 minutes for model download (~200MB)"
    echo ""
    
    if [ "$NEEDS_LOCAL" -eq 1 ]; then
        print_warning "⚠️  Your chosen configuration (${PROFILE}) REQUIRES local AI server"
    else
        print_info "ℹ️  Your chosen configuration (${PROFILE}) doesn't require this,"
        print_info "   but setting it up now enables local_hybrid pipeline later"
    fi
    
    echo ""
    read -p "Set up local AI server now? [Y/n]: " setup_local
    
    if [[ "$setup_local" =~ ^[Yy]$|^$ ]]; then
        LOCAL_AI_SETUP=1
        print_info "Will set up local AI server..."
        
        # Language selection
        echo ""
        echo "╔═══════════════════════════════════════════════════════════╗"
        echo "║          🌍 Language Selection                            ║"
        echo "╚═══════════════════════════════════════════════════════════╝"
        echo ""
        echo "Select your primary language for STT and TTS models:"
        echo ""
        echo "=== Popular ==="
        echo "  1) English (US) [default]"
        echo "  2) Spanish"
        echo "  3) French"
        echo "  4) German"
        echo ""
        echo "=== European ==="
        echo "  5) Italian"
        echo "  6) Portuguese (Brazil)"
        echo "  7) Dutch"
        echo "  8) Russian"
        echo "  9) Polish"
        echo ""
        echo "=== Asian ==="
        echo "  10) Chinese (Mandarin)"
        echo "  11) Japanese"
        echo "  12) Korean"
        echo "  13) Hindi"
        echo ""
        echo "=== Other ==="
        echo "  14) Arabic"
        echo "  15) Turkish"
        echo ""
        read -p "Enter choice [1-15, default=1]: " lang_choice
        
        case "$lang_choice" in
            2) LANG_CODE="es-ES"; LANG_NAME="Spanish" ;;
            3) LANG_CODE="fr-FR"; LANG_NAME="French" ;;
            4) LANG_CODE="de-DE"; LANG_NAME="German" ;;
            5) LANG_CODE="it-IT"; LANG_NAME="Italian" ;;
            6) LANG_CODE="pt-BR"; LANG_NAME="Portuguese" ;;
            7) LANG_CODE="nl-NL"; LANG_NAME="Dutch" ;;
            8) LANG_CODE="ru-RU"; LANG_NAME="Russian" ;;
            9) LANG_CODE="pl-PL"; LANG_NAME="Polish" ;;
            10) LANG_CODE="zh-CN"; LANG_NAME="Chinese" ;;
            11) LANG_CODE="ja-JP"; LANG_NAME="Japanese" ;;
            12) LANG_CODE="ko-KR"; LANG_NAME="Korean" ;;
            13) LANG_CODE="hi-IN"; LANG_NAME="Hindi" ;;
            14) LANG_CODE="ar"; LANG_NAME="Arabic" ;;
            15) LANG_CODE="tr-TR"; LANG_NAME="Turkish" ;;
            *) LANG_CODE="en-US"; LANG_NAME="English (US)" ;;
        esac
        
        print_success "✓ Selected language: ${LANG_NAME}"
        echo ""
        
        # Download models if script exists
        if [ -f scripts/model_setup.sh ]; then
            echo ""
            print_info "Downloading AI models for ${LANG_NAME}..."
            print_info "This may take 5-10 minutes depending on your connection"
            if bash scripts/model_setup.sh --assume-yes --language="${LANG_CODE}"; then
                print_success "✓ Models downloaded successfully"
                autodetect_local_models
            else
                print_warning "⚠️  Model download had issues. Models will be downloaded on first container start."
            fi
        else
            print_warning "Model setup script not found. Models will download on first start."
        fi
        
        # Notify about additional models
        echo ""
        print_info "💡 Tip: You can download additional models and voices later from"
        print_info "   System → Models in the Admin UI."
        
        # Enable local provider in YAML
        if command -v yq >/dev/null 2>&1; then
            yq -i '.providers.local.enabled = true' "config/ai-agent.yaml"
            print_success "✓ Local provider enabled in configuration"
        fi
    else
        LOCAL_AI_SETUP=0
        echo ""
        print_warning "⚠️  Skipped local AI server setup"
        echo ""
        echo "To set up later, run these commands:"
        echo "  1. Download models:"
        echo "     bash scripts/model_setup.sh"
        echo ""
        echo "  2. Start local AI server:"
        echo "     docker compose -p asterisk-ai-voice-agent up -d local_ai_server"
        echo ""
        echo "  3. Enable in config/ai-agent.yaml:"
        echo "     providers:"
        echo "       local:"
        echo "         enabled: true"
        echo ""
        
        if [ "$NEEDS_LOCAL" -eq 1 ]; then
            print_error "⚠️  WARNING: ${PROFILE} pipeline will NOT work without local AI server!"
            print_error "   You must set it up before using this configuration."
        else
            print_info "ℹ️  local_hybrid and local_only pipelines won't be available"
            print_info "   until you complete the setup steps above."
        fi
        
        # Disable local provider in YAML if skipped
        if command -v yq >/dev/null 2>&1; then
            yq -i '.providers.local.enabled = false' "config/ai-agent.yaml"
        fi
    fi
}

# Post-start validation (cross-platform compatible)
validate_services() {
    local validation_failed=0
    
    echo ""
    print_info "Validating services..."
    
    # Check ai_engine container is running
    if docker ps --filter "name=ai_engine" --filter "status=running" | grep -q "ai_engine"; then
        print_success "✓ ai_engine container running"
    else
        print_warning "✗ ai_engine container not running"
        validation_failed=1
    fi
    
    # Check health endpoint (wait up to 10 seconds)
    print_info "Checking health endpoint (may take a few seconds)..."
    local health_available=0
    for i in 1 2 3 4 5; do
        if command -v curl >/dev/null 2>&1; then
            if curl -s -f http://127.0.0.1:15000/health >/dev/null 2>&1; then
                health_available=1
                break
            fi
        elif command -v wget >/dev/null 2>&1; then
            if wget -q -O- http://127.0.0.1:15000/health >/dev/null 2>&1; then
                health_available=1
                break
            fi
        else
            # No curl/wget, skip health check
            print_info "  (curl/wget not available, skipping HTTP check)"
            break
        fi
        sleep 2
    done
    
    if [ "$health_available" -eq 1 ]; then
        print_success "✓ Health endpoint responding at :15000"
    elif command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1; then
        print_warning "✗ Health endpoint not yet responding (may still be starting)"
        print_info "   Check: $COMPOSE logs ai_engine"
    fi
    
    # For local_ai_server, check if user set it up
    if [ "${LOCAL_AI_SETUP:-0}" -eq 1 ]; then
        if docker ps --filter "name=local_ai_server" --filter "status=running" | grep -q "local_ai_server"; then
            print_success "✓ local_ai_server container running"
        else
            print_warning "✗ local_ai_server container not running"
            validation_failed=1
        fi
    fi
    
    echo ""
    if [ "$validation_failed" -eq 0 ]; then
        print_success "🎉 All validation checks passed!"
    else
        print_warning "⚠️  Some validation checks failed. Review logs:"
        echo "   $COMPOSE logs ai_engine"
        if [ "${LOCAL_AI_SETUP:-0}" -eq 1 ]; then
            echo "   $COMPOSE logs local_ai_server"
        fi
    fi
}

start_services() {
    echo ""
    echo "╔═══════════════════════════════════════════════════════════╗"
    echo "║              Starting Services                            ║"
    echo "╚═══════════════════════════════════════════════════════════╝"
    echo ""
    
    # Support non-interactive mode
    if [ "${INSTALL_NONINTERACTIVE:-0}" = "1" ]; then
        print_info "Non-interactive mode: starting services automatically"
        start_service="y"
    else
        read -p "Build and start services now? [Y/n]: " start_service
    fi
    
    if [[ "$start_service" =~ ^[Yy]$|^$ ]]; then
        # Start local_ai_server if user opted in
        if [ "${LOCAL_AI_SETUP:-0}" -eq 1 ]; then
            print_info "Starting local_ai_server (STT/TTS)..."
            print_info "Note: First startup may take 5-10 minutes to load models"
            print_info "Monitor progress: $COMPOSE logs -f local_ai_server"
            echo ""
            wait_for_local_ai_health
        fi
        
        # Always start ai_engine
        print_info "Starting ai_engine (orchestrator)..."
        echo ""
        $COMPOSE up -d --build ai_engine
        
        # Post-start validation
        validate_services
        
        # Show health & monitoring endpoints
        echo ""
        echo "╔═══════════════════════════════════════════════════════════╗"
        echo "║          📊 Health & Monitoring Endpoints                 ║"
        echo "╚═══════════════════════════════════════════════════════════╝"
        echo ""
        print_success "🎉 Installation complete!"
        echo ""
        
        echo "🏥 Health Check:"
        if command -v curl >/dev/null 2>&1; then
            echo "   curl http://127.0.0.1:15000/health"
        else
            echo "   wget -qO- http://127.0.0.1:15000/health"
        fi
        echo ""
        
        echo "📊 Active Configuration:"
        echo "   Provider: $ACTIVE_PROVIDER"
        if [ "${LOCAL_AI_SETUP:-0}" -eq 1 ]; then
            echo "   Local AI: Enabled"
        else
            echo "   Local AI: Not configured"
        fi
        echo ""
        
        echo "📋 View Logs:"
        echo "   $COMPOSE logs -f ai_engine"
        if [ "${LOCAL_AI_SETUP:-0}" -eq 1 ]; then
            echo "   $COMPOSE logs -f local_ai_server"
        fi
        echo ""
        
        echo "🔧 Container Status:"
        echo "   $COMPOSE ps"
        echo "   docker stats --no-stream ai_engine"
        echo ""

        echo "🖥️ Admin UI (recommended):"
        if [ "${INSTALL_NONINTERACTIVE:-0}" = "1" ]; then
            start_admin_ui="y"
        else
            read -p "Start Admin UI now? [Y/n]: " start_admin_ui
        fi

        if [[ "$start_admin_ui" =~ ^[Yy]$|^$ ]]; then
            print_info "Starting Admin UI..."
            $COMPOSE up -d --build admin_ui
        else
            print_info "Skipped starting Admin UI. You can start it later with:"
            print_info "  $COMPOSE up -d admin_ui"
        fi

        echo ""
        if [ -n "${SSH_CONNECTION:-}" ] || [ -n "${SSH_TTY:-}" ]; then
            echo "Admin UI access (remote server):"
            echo "  - SSH tunnel: ssh -L 3003:127.0.0.1:3003 <user>@<server>"
            echo "    then open: http://localhost:3003"
            echo "  - Or set UVICORN_HOST=0.0.0.0 + a strong JWT_SECRET (and firewall)"
        else
            echo "Admin UI access:"
            echo "  http://localhost:3003"
        fi
        echo "Login: admin / admin (change on first login)"
        echo ""
        
        if [ "${LOCAL_AI_SETUP:-0}" -eq 1 ]; then
            echo "🤖 Local AI Models:"
            echo "   $COMPOSE logs local_ai_server | grep -i 'model.*loaded'"
            echo ""
        fi
        
        echo "🔄 Switching Providers:"
        echo "   All 3 providers are configured in config/ai-agent.yaml"
        echo "   To switch: Edit the file, set providers.<name>.enabled: true"
        echo "   Then: docker compose -p asterisk-ai-voice-agent restart ai_engine"
        echo ""
        
        print_info "Next step: Configure Asterisk dialplan (see below)"
    else
        echo ""
        print_info "Setup complete. Start services later with:"
        print_info "  $COMPOSE up --build -d"
    fi

    # Always print recommended Asterisk dialplan snippet
    print_asterisk_dialplan_snippet
}

# --- Output recommended Asterisk dialplan for the chosen profile ---
print_asterisk_dialplan_snippet() {
    echo ""
    echo "╔═══════════════════════════════════════════════════════════╗"
    echo "║            Asterisk Dialplan Configuration                ║"
    echo "╚═══════════════════════════════════════════════════════════╝"
    echo ""

    APP_NAME="asterisk-ai-voice-agent"
    
    # Determine configuration based on profile
    case "$PROFILE" in
        openai_realtime)
            DISPLAY_NAME="OpenAI Realtime"
            TRANSPORT="AudioSocket or ExternalMedia RTP"
            ;;
        deepgram)
            DISPLAY_NAME="Deepgram Voice Agent"
            TRANSPORT="AudioSocket or ExternalMedia RTP"
            ;;
        local_hybrid)
            DISPLAY_NAME="Local Hybrid Pipeline"
            TRANSPORT="ExternalMedia RTP (recommended)"
            ;;
        *)
            DISPLAY_NAME="AI Voice Agent"
            TRANSPORT="AudioSocket or ExternalMedia RTP"
            ;;
    esac

    echo "Active Configuration: $DISPLAY_NAME"
    echo "Transport: $TRANSPORT"
    echo ""
    echo "ℹ️  All 3 provider configurations are available in config/ai-agent.yaml"
    echo "   Switch by editing the file and restarting: docker compose -p asterisk-ai-voice-agent restart ai_engine"
    echo ""
    echo "Add this to extensions_custom.conf (or via FreePBX GUI):"
    echo ""
    cat <<'EOF'
[from-ai-agent]
exten => s,1,NoOp(Asterisk AI Voice Agent)
 same => n,Answer()
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
EOF
    
    echo ""
    echo "Then create a FreePBX Custom Destination:"
    echo "  • Target: from-ai-agent,s,1"
    echo "  • Route an inbound route or extension to this destination"
    echo ""
    echo "Verify Asterisk modules are loaded:"
    echo "  asterisk -rx 'module show like res_ari'"
    echo "  asterisk -rx 'module show like app_audiosocket'"
    echo ""
    echo "For detailed integration steps, see:"
    echo "  docs/FreePBX-Integration-Guide.md"
    echo ""
    
    # Call final summary which includes monitoring and CLI installation
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "📊 OPTIONAL: Monitoring & Email Summary Setup"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    print_final_summary
}

# --- Offer CLI Installation ---
offer_cli_installation() {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "🛠️  Agent CLI Tools"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    
    # Check if agent CLI already exists
    if command -v agent >/dev/null 2>&1; then
        local version=$(agent version 2>/dev/null | head -1 || echo "unknown")
        print_success "agent CLI already installed: $version"
        echo ""
        print_info "Available commands:"
        print_info "  • agent setup     - Interactive setup wizard"
        print_info "  • agent check     - Standard diagnostics report"
        print_info "  • agent rca       - Post-call root cause analysis"
        print_info "  • agent version   - Version information"
        echo ""
        return 0
    fi
    
    # Detect platform
    local OS=$(uname -s | tr '[:upper:]' '[:lower:]')
    local ARCH=$(uname -m)
    
    case $ARCH in
        x86_64) ARCH="amd64" ;;
        aarch64|arm64) ARCH="arm64" ;;
        *)
            print_warning "Unsupported architecture: $ARCH"
            print_info "You can build from source: make cli-build"
            echo ""
            return 1
            ;;
    esac
    
    case $OS in
        linux|darwin) ;;
        *)
            print_warning "Unsupported OS: $OS"
            print_info "You can build from source: make cli-build"
            echo ""
            return 1
            ;;
    esac
    
    # Offer installation
    echo "The agent CLI provides helpful tools for setup and troubleshooting:"
    echo "  • agent setup     - Interactive setup wizard"
    echo "  • agent check     - Standard diagnostics report"
    echo "  • agent rca       - Post-call root cause analysis"
    echo ""
    
    read -p "Install agent CLI tool? [Y/n]: " install_cli
    
    if [[ "$install_cli" =~ ^[Nn]$ ]]; then
        print_info "Skipping CLI installation"
        echo ""
        print_info "You can install it later with:"
        print_info "  make cli-build && sudo cp bin/agent /usr/local/bin/"
        echo ""
        print_manual_next_steps
        return 0
    fi
    
    # Download and install
    print_info "Installing agent CLI for $OS/$ARCH..."
    
    local BINARY_URL="https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/latest/download/agent-${OS}-${ARCH}"
    local TEMP_FILE="/tmp/agent-cli-$$"
    
    if curl -sfL "$BINARY_URL" -o "$TEMP_FILE"; then
        chmod +x "$TEMP_FILE"
        
        # Try to install to /usr/local/bin
        if $SUDO mv "$TEMP_FILE" /usr/local/bin/agent 2>/dev/null; then
            print_success "agent CLI installed to /usr/local/bin/agent"
        else
            # Fallback to local bin
            mkdir -p "$HOME/.local/bin"
            mv "$TEMP_FILE" "$HOME/.local/bin/agent"
            print_success "agent CLI installed to $HOME/.local/bin/agent"
            
            # Check if in PATH
            if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
                print_warning "$HOME/.local/bin is not in your PATH"
                print_info "Add to ~/.bashrc or ~/.zshrc:"
                print_info "  export PATH=\"\$HOME/.local/bin:\$PATH\""
            fi
        fi
        
        # Verify installation
        local agent_path=$(command -v agent 2>/dev/null)
        if [ -n "$agent_path" ]; then
            local version=$(agent version 2>/dev/null | head -1 || echo "installed")
            print_success "Verified: $version"
            echo ""
            print_info "Next steps:"
            print_info "  1) Run setup: agent setup"
            print_info "  2) Run diagnostics: agent check"
            print_info "  3) After a test call: agent rca"
        fi
    else
        print_warning "Could not download agent CLI (network issue or release not available)"
        echo ""
        print_info "You can build from source:"
        print_info "  make cli-build && sudo cp bin/agent /usr/local/bin/"
        echo ""
        print_manual_next_steps
    fi
    
    echo ""
}

# --- Print Manual Next Steps ---
print_manual_next_steps() {
    echo "Manual Configuration Steps:"
    echo ""
    echo "1. Add dialplan configuration:"
    echo "   See the snippet printed above"
    echo "   File: /etc/asterisk/extensions_custom.conf (or via FreePBX GUI)"
    echo ""
    echo "2. Create FreePBX Custom Destination:"
    echo "   Admin → Custom Destination → Add"
    echo "   Target: from-ai-agent,s,1"
    echo ""
    echo "3. For detailed steps, see:"
    echo "   docs/FreePBX-Integration-Guide.md"
}

# --- Print Monitoring Instructions ---
print_monitoring_instructions() {
    echo "To enable email summaries and enhanced monitoring:"
    echo ""
    echo "1. Get a Resend API key:"
    echo "   • Sign up at https://resend.com"
    echo "   • Create an API key in your dashboard"
    echo ""
    echo "2. Add to .env file:"
    echo "   RESEND_API_KEY=re_your_actual_key_here"
    echo ""
    echo "3. Configure email settings in config/ai-agent.yaml:"
    echo "   monitoring:"
    echo "     email:"
    echo "       enabled: true"
    echo "       from: 'ai-agent@yourdomain.com'"
    echo "       to: 'admin@yourdomain.com'"
    echo "       summary_interval: daily  # or hourly, weekly"
    echo ""
    echo "4. Restart ai_engine to apply:"
    echo "   docker compose -p asterisk-ai-voice-agent restart ai_engine"
    echo ""
    echo "For Grafana/Prometheus integration, see:"
    echo "  docs/MONITORING_GUIDE.md"
    echo ""
}

# --- Final Summary ---
print_final_summary() {
    # Print monitoring instructions
    print_monitoring_instructions
    
    # Offer CLI installation and quickstart
    offer_cli_installation
    
    print_success "Installation complete! 🎉"
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
    print_info "🔍 Next steps:"
    print_info "  1. Access Admin UI: http://<server-ip>:3003"
    print_info "  2. Configure dialplan (see snippet above or run: agent setup)"
    print_info "  3. Make a test call to verify everything works"
    print_info "  4. Check logs: docker compose -p asterisk-ai-voice-agent logs -f ai_engine"
    print_info "  5. Switch pipelines: Edit config/ai-agent.yaml (change default_provider)"
    print_info "  6. Optional: Set up monitoring (see instructions above)"
}

# --- Main ---
main() {
    echo "=========================================="
    echo " Asterisk AI Voice Agent Installation"
    echo "=========================================="

    maybe_run_preflight
    check_docker
    choose_compose_cmd
    check_asterisk_modules
    configure_env
    select_config_template
    setup_media_paths
    setup_data_directory
    setup_models_directory
    start_services
}

main
