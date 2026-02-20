# Provider Templates

Templates for building new AI providers.

## Full Agent Provider

`template_full_agent.py` — a complete skeleton for providers that handle STT + LLM + TTS in one connection (like OpenAI Realtime).

**To use:** Open this file in Windsurf and tell AVA:

> "Help me build a full agent provider like this for [your provider name]"

**Guide:** [docs/contributing/adding-full-agent-provider.md](../../docs/contributing/adding-full-agent-provider.md)

**Reference implementation:** `src/providers/openai_realtime.py`
