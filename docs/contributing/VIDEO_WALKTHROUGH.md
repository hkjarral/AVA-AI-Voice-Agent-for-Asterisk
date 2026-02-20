# Video Walkthrough Script

**Title:** "How to Contribute to AAVA — Zero Coding Required"
**Length:** ~5-7 minutes
**Platform:** YouTube (embedded in README and CONTRIBUTING.md)

---

## Intro (30 seconds)

> "You use AAVA on your Asterisk server. You make calls with it every day. Here's how to contribute back to the project — even if you've never written code or used GitHub before. Everything is handled by our AI assistant, AVA."

**Screen:** AAVA logo / project GitHub page

---

## Part 1: One-Time Setup (2 minutes)

### Scene 1: Clone the Repo (30s)

Show terminal:

```bash
git clone -b develop https://github.com/hkjarral/Asterisk-AI-Voice-Agent.git
cd Asterisk-AI-Voice-Agent
```

> "First, clone the project. We use the develop branch — that's where all the contributor tools live."

### Scene 2: Run Setup Script (45s)

```bash
./scripts/setup-contributor.sh
```

> "Run our setup script. It installs the GitHub CLI, connects your GitHub account, and forks the project. Just follow the prompts."

Show: gh auth login browser flow, fork creation, SSH test.

### Scene 3: Open in Windsurf (45s)

Show: Opening Windsurf → File → Open Folder → selecting the project.

> "Open the project in Windsurf. It automatically loads our AI rules — AVA is now ready to help you."

---

## Part 2: Your First Contribution (3 minutes)

### Scene 4: Ask AVA (30s)

Show: Windsurf chat panel.

Type: "I want to contribute"

> "Just type 'I want to contribute' in the chat. AVA asks what you're interested in and suggests tasks from our roadmap."

### Scene 5: AVA Suggests a Task (30s)

Show: AVA responding with suggestions from ROADMAP.md Good First Issues.

> "AVA suggests tasks based on your interests. Let's pick: 'Write a case study about my deployment.'"

### Scene 6: AVA Does the Work (60s)

Show: AVA creating a file, writing content, asking clarifying questions.

> "AVA creates the file, writes the content, and formats everything. You just describe your setup — AVA does the rest."

### Scene 7: Submit as PR (60s)

Type: "Submit my changes as a PR"

Show: AVA running gh commands, PR appearing on GitHub.

> "Tell AVA to submit your changes. It creates a branch, commits, pushes to your fork, and opens a Pull Request — all automatically. You get a link to your PR on GitHub."

---

## Part 3: What Happens Next (30 seconds)

> "A maintainer reviews your PR, usually within a few days. If they ask for changes, just tell AVA — it'll fix them and push automatically. When merged, your name appears in our Contributors list."

**Screen:** CONTRIBUTORS.md with the user's name added.

> "That's it. You just contributed to an open source project. Welcome to the community!"

---

## Outro (30 seconds)

> "Links to everything are in the video description. Join our Discord if you have questions. And if you want to build something bigger — a new provider, a webhook integration, a tool — AVA can handle that too. Just describe what you want."

**Screen:** Links overlay:
- GitHub: github.com/hkjarral/Asterisk-AI-Voice-Agent
- Discord: discord.gg/ysg8fphxUe
- Guide: docs/contributing/OPERATOR_CONTRIBUTOR_GUIDE.md

---

## Production Notes

- Record on macOS with Windsurf visible (clean desktop, large font size)
- Use a real project clone — show actual AVA responses
- Keep terminal text large and readable
- Add captions for accessibility
- Background music: subtle, non-distracting
- Export at 1080p minimum

## Thumbnail

- Text: "Contribute to Open Source — No Coding Required"
- Image: Split screen of Windsurf + GitHub PR
- AAVA logo in corner
