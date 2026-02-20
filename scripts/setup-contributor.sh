#!/bin/bash
# ============================================================================
# AAVA Contributor Setup
# ============================================================================
# One-time setup for contributing to the Asterisk AI Voice Agent.
# Run this on your LOCAL machine after cloning the repo.
#
# Prerequisites:
#   git clone -b develop https://github.com/hkjarral/Asterisk-AI-Voice-Agent.git
#   cd Asterisk-AI-Voice-Agent
#   ./scripts/setup-contributor.sh
# ============================================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo ""
echo "============================================"
echo "  AAVA Contributor Setup"
echo "============================================"
echo ""
echo "This sets up your local machine for contributing."
echo "You'll need a GitHub account (free) to continue."
echo ""

# -----------------------------------------------
# 1. Check / install GitHub CLI
# -----------------------------------------------
if command -v gh &> /dev/null; then
    echo -e "${GREEN}✓${NC} GitHub CLI (gh) is installed: $(gh --version | head -1)"
else
    echo -e "${YELLOW}GitHub CLI (gh) not found. Let's install it.${NC}"
    echo ""

    if [[ "$OSTYPE" == "darwin"* ]]; then
        if command -v brew &> /dev/null; then
            echo "Installing via Homebrew..."
            brew install gh
        else
            echo -e "${RED}Homebrew not found.${NC}"
            echo "Install gh manually: https://cli.github.com/"
            exit 1
        fi
    elif [[ -f /etc/debian_version ]]; then
        echo "Installing via apt..."
        (type -p wget >/dev/null || (sudo apt update && sudo apt-get install wget -y)) \
            && sudo mkdir -p -m 755 /etc/apt/keyrings \
            && wget -qO- https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null \
            && sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
            && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
            && sudo apt update \
            && sudo apt install gh -y
    elif [[ -f /etc/redhat-release ]]; then
        echo "Installing via dnf..."
        sudo dnf install 'dnf-command(config-manager)' -y
        sudo dnf config-manager --add-repo https://cli.github.com/packages/rpm/gh-cli.repo
        sudo dnf install gh -y
    else
        echo -e "${RED}Could not auto-install gh for your OS.${NC}"
        echo "Install manually: https://cli.github.com/"
        exit 1
    fi

    echo -e "${GREEN}✓${NC} GitHub CLI installed."
fi

# -----------------------------------------------
# 2. Authenticate with GitHub
# -----------------------------------------------
echo ""
if gh auth status &> /dev/null; then
    echo -e "${GREEN}✓${NC} Already authenticated with GitHub."
else
    echo "Let's connect to your GitHub account..."
    echo "When prompted:"
    echo "  - Choose HTTPS"
    echo "  - Choose 'Login with a web browser'"
    echo ""
    gh auth login
    echo -e "${GREEN}✓${NC} GitHub authentication complete."
fi

# -----------------------------------------------
# 3. Fork the repo and add remote
# -----------------------------------------------
echo ""
echo "Forking the project to your GitHub account..."
if git remote | grep -q "^fork$"; then
    echo -e "${GREEN}✓${NC} Fork remote already configured."
else
    gh repo fork --remote --remote-name fork 2>/dev/null || true
    if git remote | grep -q "^fork$"; then
        echo -e "${GREEN}✓${NC} Fork created and remote added."
    else
        echo -e "${YELLOW}!${NC} Could not auto-fork. You may need to fork manually on GitHub."
    fi
fi

# -----------------------------------------------
# 4. Set up SSH to AAVA server (optional)
# -----------------------------------------------
echo ""
echo "============================================"
echo "  Server Connection (Optional)"
echo "============================================"
echo ""
echo "If you have an AAVA server (Asterisk + AAVA deployed),"
echo "we can set up SSH so you can deploy and test changes."
echo ""
read -p "Enter your AAVA server IP/hostname (or press Enter to skip): " SERVER_HOST

if [ -n "$SERVER_HOST" ]; then
    echo ""
    echo "Testing SSH connection to $SERVER_HOST..."
    if ssh -o ConnectTimeout=5 -o BatchMode=yes "root@$SERVER_HOST" "echo 'OK'" 2>/dev/null; then
        echo -e "${GREEN}✓${NC} SSH connection to $SERVER_HOST verified."
        echo "CONTRIBUTOR_SERVER=$SERVER_HOST" > .env.contributor
        echo -e "${GREEN}✓${NC} Server saved to .env.contributor"
    else
        echo -e "${YELLOW}!${NC} Could not connect. You may need to set up SSH keys:"
        echo ""
        echo "  1. Generate a key (press Enter 3 times):"
        echo "     ssh-keygen"
        echo ""
        echo "  2. Copy key to server:"
        echo "     ssh-copy-id root@$SERVER_HOST"
        echo ""
        echo "  3. Test:"
        echo "     ssh root@$SERVER_HOST"
        echo ""
        echo "Once SSH works, re-run this script or manually create .env.contributor:"
        echo "  echo 'CONTRIBUTOR_SERVER=$SERVER_HOST' > .env.contributor"
    fi
else
    echo "Skipping server setup. You can set this up later."
fi

# -----------------------------------------------
# 5. Verify setup
# -----------------------------------------------
echo ""
echo "============================================"
echo "  Setup Summary"
echo "============================================"
echo ""
echo -e "  GitHub CLI:     $(command -v gh &>/dev/null && echo "${GREEN}✓ installed${NC}" || echo "${RED}✗ missing${NC}")"
echo -e "  GitHub Auth:    $(gh auth status &>/dev/null 2>&1 && echo "${GREEN}✓ authenticated${NC}" || echo "${RED}✗ not authenticated${NC}")"
echo -e "  Fork Remote:    $(git remote | grep -q '^fork$' && echo "${GREEN}✓ configured${NC}" || echo "${YELLOW}! not configured${NC}")"
echo -e "  AAVA Server:    $([ -f .env.contributor ] && echo "${GREEN}✓ $(grep CONTRIBUTOR_SERVER .env.contributor | cut -d= -f2)${NC}" || echo "${YELLOW}! not configured (optional)${NC}")"
echo ""
echo "============================================"
echo -e "  ${GREEN}Setup Complete!${NC}"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Open this folder in Windsurf (or your preferred AI IDE)"
echo "  2. In the chat, type: 'I want to contribute'"
echo "  3. AVA will guide you through everything else!"
echo ""
