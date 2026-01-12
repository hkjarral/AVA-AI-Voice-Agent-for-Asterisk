#!/bin/bash
# Install script for Asterisk AI Voice Agent CLI tools
# Usage: curl -sSL https://raw.githubusercontent.com/hkjarral/Asterisk-AI-Voice-Agent/main/scripts/install-cli.sh | bash

set -e

# Configuration
REPO="hkjarral/Asterisk-AI-Voice-Agent"
INSTALL_DIR="${INSTALL_DIR:-/usr/local/bin}"
VERSION="${AGENT_VERSION:-latest}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

success() {
    echo -e "${GREEN}✓${NC} $1"
}

error() {
    echo -e "${RED}✗${NC} $1"
    exit 1
}

warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

# Detect platform
detect_platform() {
    OS=$(uname -s | tr '[:upper:]' '[:lower:]')
    ARCH=$(uname -m)
    
    case $ARCH in
        x86_64)
            ARCH="amd64"
            ;;
        aarch64|arm64)
            ARCH="arm64"
            ;;
        *)
            error "Unsupported architecture: $ARCH"
            ;;
    esac
    
    case $OS in
        linux)
            PLATFORM="linux"
            ;;
        darwin)
            PLATFORM="darwin"
            ;;
        mingw*|msys*|cygwin*)
            PLATFORM="windows"
            ;;
        *)
            error "Unsupported OS: $OS"
            ;;
    esac
    
    BINARY_NAME="agent-${PLATFORM}-${ARCH}"
    if [ "$PLATFORM" = "windows" ]; then
        BINARY_NAME="${BINARY_NAME}.exe"
    fi
    
    info "Detected platform: ${PLATFORM}-${ARCH}"
}

# Get latest version from GitHub
get_latest_version() {
    if [ "$VERSION" = "latest" ]; then
        info "Fetching latest version..."
        VERSION=$(curl -sSL "https://api.github.com/repos/${REPO}/releases/latest" | grep '"tag_name":' | sed -E 's/.*"([^"]+)".*/\1/')
        
        if [ -z "$VERSION" ]; then
            error "Failed to fetch latest version"
        fi
        
        info "Latest version: $VERSION"
    fi
}

# Download binary
download_binary() {
    URL="https://github.com/${REPO}/releases/download/${VERSION}/${BINARY_NAME}"
    TEMP_FILE="/tmp/agent-${PLATFORM}-${ARCH}"
    
    info "Downloading from: $URL"
    
    if command -v curl >/dev/null 2>&1; then
        curl -L -f -o "$TEMP_FILE" "$URL" || error "Download failed"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$TEMP_FILE" "$URL" || error "Download failed"
    else
        error "Neither curl nor wget is available. Please install one of them."
    fi
    
    success "Downloaded successfully"
}

# Verify checksum
verify_checksum() {
    info "Verifying checksum..."
    
    CHECKSUM_URL="https://github.com/${REPO}/releases/download/${VERSION}/SHA256SUMS"
    CHECKSUM_FILE="/tmp/SHA256SUMS"
    
    if command -v curl >/dev/null 2>&1; then
        curl -L -f -o "$CHECKSUM_FILE" "$CHECKSUM_URL" 2>/dev/null || warning "Could not download checksums"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$CHECKSUM_FILE" "$CHECKSUM_URL" 2>/dev/null || warning "Could not download checksums"
    fi
    
    if [ -f "$CHECKSUM_FILE" ]; then
        EXPECTED_CHECKSUM=$(grep "$BINARY_NAME" "$CHECKSUM_FILE" | awk '{print $1}')
        
        if command -v sha256sum >/dev/null 2>&1; then
            ACTUAL_CHECKSUM=$(sha256sum "$TEMP_FILE" | awk '{print $1}')
        elif command -v shasum >/dev/null 2>&1; then
            ACTUAL_CHECKSUM=$(shasum -a 256 "$TEMP_FILE" | awk '{print $1}')
        else
            warning "No checksum tool available, skipping verification"
            return
        fi
        
        if [ "$EXPECTED_CHECKSUM" = "$ACTUAL_CHECKSUM" ]; then
            success "Checksum verified"
        else
            error "Checksum mismatch! Expected: $EXPECTED_CHECKSUM, Got: $ACTUAL_CHECKSUM"
        fi
    else
        warning "Checksum file not available, skipping verification"
    fi
}

# Install binary
install_binary() {
    chmod +x "$TEMP_FILE"
    
    # Check if we need sudo
    if [ -w "$INSTALL_DIR" ]; then
        mv "$TEMP_FILE" "${INSTALL_DIR}/agent"
    else
        info "Installing to $INSTALL_DIR requires sudo"
        sudo mv "$TEMP_FILE" "${INSTALL_DIR}/agent"
    fi
    
    success "Installed to ${INSTALL_DIR}/agent"
}

# Main installation flow
main() {
    echo ""
    echo "╔═══════════════════════════════════════════════════════════╗"
    echo "║     Asterisk AI Voice Agent CLI Installer                ║"
    echo "╚═══════════════════════════════════════════════════════════╝"
    echo ""
    
    detect_platform
    get_latest_version
    download_binary
    verify_checksum
    install_binary
    
    echo ""
    success "Installation complete!"
    echo ""
    
    # Test installation
    if command -v agent >/dev/null 2>&1; then
        info "Testing installation..."
        agent version
        echo ""
        info "Get started with:"
        echo "  agent setup          # Setup wizard"
        echo "  agent check          # Standard diagnostics report"
        echo "  agent rca            # Post-call root cause analysis"
        echo "  agent --help         # Show all commands"
    else
        warning "Installation succeeded but 'agent' is not in PATH"
        info "Add ${INSTALL_DIR} to your PATH or run: export PATH=\$PATH:${INSTALL_DIR}"
    fi
    
    echo ""
    info "Documentation: https://github.com/${REPO}/blob/main/docs/TROUBLESHOOTING_GUIDE.md"
    echo ""
}

# Run installer
main
