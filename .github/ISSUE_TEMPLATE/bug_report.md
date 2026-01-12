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
- **Version**: <!-- e.g., v5.0.0 -->
- **OS**: <!-- e.g., Ubuntu 22.04 -->
- **Docker Version**: <!-- e.g., 24.0.7 -->
- **Asterisk Version**: <!-- e.g., 18.20.0 -->
- **Configuration**: <!-- e.g., local_hybrid, openai_realtime -->
- **Transport**: <!-- AudioSocket or ExternalMedia RTP -->

## Logs
<!-- Paste relevant logs here -->
```
docker logs ai_engine --tail=100
```

## Diagnostics (Recommended)

These make issues actionable and help us update the Supported Platforms matrix:

```bash
./preflight.sh
```

If the `agent` CLI is not installed:
```bash
curl -sSL https://raw.githubusercontent.com/hkjarral/Asterisk-AI-Voice-Agent/main/scripts/install-cli.sh | bash
agent version
```

Note for forks: replace `hkjarral/Asterisk-AI-Voice-Agent` in the URL above with your fork owner/repo.

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
- [ ] I have included relevant logs
- [ ] I have specified my environment details
