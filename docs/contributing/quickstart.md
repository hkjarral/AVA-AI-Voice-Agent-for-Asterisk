# Your First AI-Assisted AVA Contribution

Start with a concrete goal: explain a feature, fix a confusing screen, investigate
a call, improve a guide, or add a capability. Your assistant can help with code and
Git; you supply the intended behavior and real-world observations. Tests and review
establish whether the contribution is ready.

## Open the Project and Load Its Context

Use a repository-aware coding assistant such as Codex, Claude Code, Cursor,
Windsurf, or an IDE with an assistant that can read project files. Open the actual
AVA checkout, attach/reference [AVA.mdc](../../AVA.mdc), and send:

> Read AVA.mdc and the applicable instructions in this project folder. I'm new to
> contributing and want to [describe the goal]. Inspect the relevant files, explain
> unfamiliar terms, and guide me through implementation, testing, and a contribution.
> Ask only for information you cannot discover, and identify what needs live testing.

The assistant should read the root [AGENTS.md](../../AGENTS.md), applicable scoped
instructions, and any existing `CLAUDE.md`. Preserve personal rules. The unrelated
[docs/AGENTS.md](../AGENTS.md) explains AVA's runtime Agents/personas.

| Assistant | How to supply context |
| --- | --- |
| Codex | Root `AGENTS.md` points to `AVA.mdc`; ask the assistant to read it and the relevant guides. |
| Claude Code | Explicitly reference `AVA.mdc`. If you use a local `CLAUDE.md`, preserve it and optionally add a root-relative `@AVA.mdc` import in that root file; verify the loaded context. |
| Cursor / Windsurf / other IDE | Attach/reference the root file explicitly. Do not assume a root `.mdc` file is automatically registered as a native IDE rule. |
| Chat without repository access | Ask where AVA is deployed and move to a repository-aware IDE or establish supported remote access. Supplied files/support packages can support a limited diagnosis meanwhile. |

Tool discovery differs by version/settings. See official
[Codex instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md),
[Claude Code memory/imports](https://code.claude.com/docs/en/memory), and
[Cursor rules](https://cursor.com/docs/rules). Confirm what the assistant actually
read; do not rely on it saying that it “knows AVA” without inspecting the checkout.
This repository intentionally keeps personal `CLAUDE.md` and native IDE rules
local; no additional rule-file installation is required for explicit loading.

## Find Your Deployment

Tell the assistant where AVA runs if it cannot discover that information. The usual
setup is an IDE on your laptop and AVA on a Linux server accessible through SSH.
An existing production server is supported; you do not need to move it to a lab
to obtain help. The assistant establishes access, current state, authorized work,
and interruption timing before making disruptive changes.

Follow [SSH access and development workflow](ai-assisted-workflow.md). Keep passwords,
private keys, and API credentials on your machine/server, outside chat and Git.
If the repository is already installed, inspect it first; do not rerun installation
or overwrite working configuration merely to follow this tutorial.

## Git in Plain Language

- A **fork** is your GitHub copy of the project, where you can publish your work.
- A **clone** is the working copy on your machine.
- A **branch** keeps one proposed change separate from other work.
- A **commit** records a set of changes; **push** sends those commits to your fork.
- A **pull request (PR)** asks the project to review and accept your branch.
- A **draft PR** shows work in progress or requests specific help, such as live testing.

Use [GitHub Issues](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues)
and [Discussions](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/discussions)
to find or propose work. `good first issue` is reserved for first-time contributors.
You can also contribute a reproducible report or clearer documentation.

For a new contributor checkout, fork the project in GitHub, then have your assistant
help clone **your fork** and add the project as `upstream`. Example commands;
replace `YOUR_GITHUB_NAME` and the branch name:

```bash
git clone https://github.com/YOUR_GITHUB_NAME/AVA-AI-Voice-Agent-for-Asterisk.git
cd AVA-AI-Voice-Agent-for-Asterisk
git remote add upstream https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk.git
git fetch upstream main
git switch -c codex/my-change upstream/main
```

For an existing checkout, inspect `git status` and `git remote -v` first; do not
repeat setup blindly or discard unrelated changes. The assistant can use an isolated
worktree when necessary. Start new PRs from current upstream `main`, one branch
per PR. The former `develop` workflow is no longer the contribution path.

## Build, Test, and Contribute

1. Explain the goal and desired result. The assistant inspects the code and official
   API docs where needed, scopes all affected surfaces, and asks meaningful design
   questions with recommendations.
2. Implement on the focused branch and run relevant automated tests. Use the
   [workflow's test entry points](ai-assisted-workflow.md#3-implement-and-verify-locally).
3. For runtime changes, deploy the intended revision to the agreed target, preserving
   settings and data. Browser-check affected UI/backend flows. For call behavior,
   follow an explicit call script and retain observations and private evidence.
4. Update docs/changelog and inspect the exact diff for unrelated or private content.
   Stage only intended files, commit, and push to your fork within the authorized task.
5. Open a coherent draft against the project's `main` using the
   [PR template](../../.github/pull_request_template.md). Include the issue, tests,
   sanitized evidence, and gaps. The assistant can help with each GitHub step.
6. Follow the [PR workflow](PULL_REQUEST_WORKFLOW.md) to finish reviews and checks.
   Report readiness on the final commit; a maintainer merges when authorized.

Documentation-only changes do not require a live call or server deployment.
For a call problem, begin with the [diagnostic playbook](debugging-guide.md), which
explains support packages, user-controlled DEBUG logging, archives, and retesting.

## No PBX Yet?

Choose either route, or combine them:

- **Build a lab:** use the PBX/AVA setup walkthroughs on
  [Jugaarooo's YouTube channel](https://www.youtube.com/@Jugaarooo), then check the
  current [installation](../INSTALLATION.md) and [FreePBX integration](../FreePBX-Integration-Guide.md)
  docs for version-specific requirements. Follow the selected transport's supported
  Asterisk versions/modules. Install only the services your provider/pipeline needs.
- **Request live validation:** implement a coherent change with automated tests
  and open a draft explicitly stating “live validation needed.” Provide revision,
  provider/transport configuration without secrets, exact call steps, expected
  results, and what remains unverified. Ask a willing maintainer or community tester;
  testing availability is not guaranteed.

For example, a laptop contributor can fix a farewell-drain regression and add unit
tests. A tester then verifies the final word is audible, hangup completes, and no
session remains. The draft stays pending until required live evidence arrives.

For a first successful call, verify a greeting, two-way conversation, farewell,
clean hangup, and a Call History record. Use `agent check` for health and
`agent rca <call_id>` as a diagnostic report; archive evidence separately when needed.
Ask for help in [Discord](https://discord.gg/ysg8fphxUe) or the relevant GitHub thread.
