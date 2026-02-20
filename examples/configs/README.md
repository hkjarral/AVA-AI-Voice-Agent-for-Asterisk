# Example Configurations

Share your working `ai-agent.yaml` configuration here to help other operators get started.

## How to Contribute Your Config

1. Copy your working `config/ai-agent.yaml` to this directory
2. Rename it descriptively: `ai-agent-<your-setup>.yaml` (e.g., `ai-agent-deepgram-openai.yaml`)
3. **Remove all API keys and secrets** — replace with `${ENV_VAR}` placeholders
4. Add a comment at the top describing your setup
5. Tell AVA: "Submit my changes as a PR"

## What to Include

A brief comment at the top of your config file:

```yaml
# Example: Deepgram STT + OpenAI GPT-4 LLM + Google TTS
# Use case: Small business receptionist
# Server: FreePBX 18 on Ubuntu 22.04
# Provider costs: ~$0.02/minute
```

## Existing Golden Baselines

For reference, see the project's validated configs in `docs/baselines/golden/`.
