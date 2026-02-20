# Operator Contributor Guide

**You don't need to know how to code.** Our AI assistant AVA writes the code for you. This guide gets you from zero to your first contribution in about 15 minutes.

<!-- TODO: Add YouTube video link once recorded -->
<!-- **Watch the 5-minute walkthrough:** [YouTube Video](https://youtube.com/...) -->

---

## What You Need

You need **two things**:

1. **Your AAVA server** — where Asterisk + AAVA are running (your FreePBX box, VPS, etc.)
2. **Your local computer** — where you'll run the AI IDE and write code (Mac, Windows, or Linux)

---

## Part 1: One-Time Setup (15 minutes)

### Step 1: Create a GitHub Account

Go to [github.com/signup](https://github.com/signup) and create a free account. You just need an email address.

### Step 2: Install Windsurf on Your Computer

Download [Windsurf](https://codeium.com/windsurf) (free AI-powered code editor). Install it like any other app — click Next, Next, Install.

> **Why Windsurf?** It has a built-in AI assistant that reads our project rules and knows how to build things correctly. [Cursor](https://cursor.sh/) and [Claude Code](https://claude.ai/code) also work.

### Step 3: Clone the Project

Open a terminal on your local computer and run:

```bash
git clone -b develop https://github.com/hkjarral/Asterisk-AI-Voice-Agent.git
cd Asterisk-AI-Voice-Agent
```

This downloads the project to your computer. We use the `develop` branch — that's where all contributor tools live.

### Step 4: Run the Setup Script

```bash
./scripts/setup-contributor.sh
```

This script:
- Installs the GitHub CLI (`gh`) if you don't have it
- Connects your GitHub account
- Forks the project to your account
- Optionally connects to your AAVA server via SSH

Follow the prompts. When it asks how to authenticate, pick **HTTPS** and **Login with browser**.

### Step 5: Set Up SSH to Your Server (Optional)

If you want to deploy and test on your AAVA server:

```bash
# Generate SSH key (press Enter 3 times)
ssh-keygen

# Copy key to your server (replace with your server IP)
ssh-copy-id root@your-server-ip

# Test — should connect without asking for password
ssh root@your-server-ip
```

The setup script handles this if you provide your server IP when prompted.

### Step 6: Open in Windsurf

1. Open Windsurf
2. Go to **File → Open Folder**
3. Select the `Asterisk-AI-Voice-Agent` folder you cloned
4. Windsurf automatically loads the project rules — AVA is ready to help

---

## Part 2: Contributing (The Fun Part)

### Tell AVA What You Want to Do

Open the Windsurf chat panel and type:

> "I want to contribute"

AVA will:
- Ask about your comfort level and interests
- Suggest tasks from the project roadmap that match your skills
- Guide you step by step

Or be specific:

> "I want to add an SMS notification tool"

> "I want to write about my deployment setup"

> "I want to add a Google Cloud STT adapter"

### AVA Builds It for You

AVA writes the code, creates files, modifies configs — all on your local machine. You can review what AVA wrote, or just trust it. AVA follows the project's coding guidelines automatically.

### Deploy to Your Server and Test

For telephony changes, you'll want to test with a real call:

```bash
# Push changes and deploy to your server
git push
ssh root@your-server-ip "cd /path/to/AAVA && git pull && docker compose up -d --build"
```

Then:
1. Make a test call from your phone to your Asterisk system
2. Run `agent doctor` on the server to check health
3. Tell AVA the results — it helps interpret them

### Submit Your Contribution

When you're happy with the changes, tell AVA:

> "Submit my changes as a PR"

AVA runs the entire GitHub flow automatically:
1. Creates a feature branch
2. Commits your changes
3. Pushes to your fork
4. Opens a Pull Request on the main project

You get a link to your PR on GitHub.

---

## Part 3: What Happens Next

1. **A maintainer reviews your PR** — usually within a few days
2. **CI checks run automatically** — tests, linting, etc.
3. **If changes are needed** — the maintainer comments on the PR. Just tell AVA: "The reviewer asked for XYZ, can you fix it?" AVA pushes the fixes automatically.
4. **When merged** — your name appears in CONTRIBUTORS.md. Welcome to open source!

---

## What Can You Contribute?

### No-Code Contributions (Just Writing)

| Task | What You Do |
|------|-------------|
| Write a deployment case study | Share how you deployed AAVA on your server |
| Document your FreePBX dialplan | Copy your working dialplan and explain it |
| Share your `ai-agent.yaml` config | Copy your working config as an example |
| Report call flow edge cases | Document weird things you noticed during calls |
| Translate a guide | Help non-English speakers get started |

### AI-Assisted Code Contributions (AVA Writes the Code)

| Task | Contribution Area |
|------|-------------------|
| Add a new STT/TTS/LLM adapter | Modular Providers |
| Add a pre-call CRM lookup | Pre-Call Hooks |
| Add a post-call webhook (Slack, Discord, n8n) | Post-Call Hooks |
| Add an in-call appointment checker | In-Call Hooks |
| Write tests for voicemail tools | Unit Tests |
| Improve `agent doctor` error messages | CLI |

**You know which providers work best for telephony.** You know what integrations your business needs. AVA turns that knowledge into code.

---

## Appendix: Alternative IDEs

### Cursor

1. Download [Cursor](https://cursor.sh/)
2. Open the project folder — `.cursor/rules/` auto-loads
3. Use the AI chat the same way as Windsurf

### Claude Code (CLI)

1. Install: `npm install -g @anthropic-ai/claude-code`
2. Run `claude` in the project directory — `AVA.mdc` auto-loads
3. Type your contribution request in the terminal

---

## Troubleshooting

**"gh: command not found"**
→ Run the setup script again: `./scripts/setup-contributor.sh`

**"Permission denied" when connecting to server**
→ Set up SSH keys: `ssh-keygen` then `ssh-copy-id root@your-server-ip`

**"Not a git repository"**
→ Make sure you're in the `Asterisk-AI-Voice-Agent` folder

**PR creation fails**
→ Run `gh auth status` to check authentication. If expired, run `gh auth login` again.

**Need help?**
→ Join our [Discord](https://discord.gg/ysg8fphxUe) and ask in #contributing
