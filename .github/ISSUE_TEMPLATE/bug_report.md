---
name: Bug Report
about: Report a bug or unexpected behavior
title: '[BUG] '
labels: bug
assignees: ''

---

## Bug Description
<!-- A clear and concise description of what the bug is -->

## Steps to Reproduce
1. 
2. 
3. 

## Expected Behavior
<!-- What you expected to happen -->

## Actual Behavior
<!-- What actually happened -->

## Environment
- **Version**: <!-- e.g., v6.3.1 -->
- **OS**: <!-- e.g., Ubuntu 22.04 -->
- **Docker Version**: <!-- e.g., 24.0.7 -->
- **Asterisk Version**: <!-- e.g., 18.20.0 -->
- **Configuration**: <!-- e.g., local_hybrid, openai_realtime -->
- **Transport**: <!-- AudioSocket or ExternalMedia RTP -->

## Logs
<!-- Do not paste recordings, phone numbers, credentials, or unredacted customer data. -->

**Call-specific issue (preferred):** In the Admin UI, open **Call History**,
select the affected call, click **Troubleshoot**, and attach the downloaded
**Support Package (.zip)**. It includes correlated lifecycle logs, pre/in/post-call
tool evidence, and provider/Audio Profile/transport settings when the call record
contains them. Older calls may omit effective settings if they predate diagnostic
snapshots. Recordings, phone numbers, prompts, and secrets are excluded.

**System-wide issue:** Open **System Logs → Export → System diagnostics**, choose
the shortest relevant time window, and attach that ZIP. It may include events
from multiple calls. To share only the filtered text currently on screen, choose
**Download current view** instead.

## Diagnostics (Recommended)

These make issues actionable and help us update the Supported Platforms matrix:

```bash
./preflight.sh
```

If the `agent` CLI is not installed:
```bash
curl -sSL https://raw.githubusercontent.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/main/scripts/install-cli.sh | bash
agent version
```

Note for forks: replace `hkjarral/AVA-AI-Voice-Agent-for-Asterisk` in the URL above with your fork owner/repo.

Then attach diagnostics:
```bash
agent check
```

If this is a call-specific issue, also attach:
```bash
# Most recent call
agent rca

# Or a specific call_id (preferred)
agent rca --call <call_id>
```

Optional (for automation / parsing):
```bash
agent check --json
agent rca --json
```

## Configuration
<!-- If relevant, share your config (REMOVE SECRETS!) -->
```yaml
# config/ai-agent.yaml (redacted)
```

## Additional Context
<!-- Add any other context, screenshots, or files -->

## Checklist
- [ ] I have searched existing issues for duplicates
- [ ] I have redacted all sensitive information (API keys, passwords)
- [ ] I have attached a call support package or the shortest relevant system diagnostics/current-view export
- [ ] I have specified my environment details
