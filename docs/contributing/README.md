# Developer Documentation

Welcome to Asterisk AI Voice Agent development! This directory contains everything you need to contribute features, fix bugs, and extend the project.

## 🚀 Getting Started

**New to the project?** Choose your path:

### For Operators (No Coding Required)
1. **[Operator Contributor Guide](OPERATOR_CONTRIBUTOR_GUIDE.md)** - Zero-knowledge contributor walkthrough
2. Run `scripts/setup-contributor.sh` and open in Windsurf
3. Tell AVA: "I want to contribute"

### For Developers
1. **[Quick Start Guide](quickstart.md)** - Set up your dev environment in 15 minutes
2. **[Architecture Overview](architecture-quickstart.md)** - Understand the system (10-minute read)
3. **[Common Pitfalls](COMMON_PITFALLS.md)** - Avoid these mistakes

## 🔧 Development Guides

### Core Development

- **[Tool Development](tool-development.md)** - Create telephony/business tools (hangup, transfer, email)
- **[Provider Development](provider-development.md)** - Add STT/LLM/TTS providers (Azure, Google, etc.)
- **[Pipeline Development](pipeline-development.md)** - Build custom audio processing pipelines
- **[Coding Guidelines](CODING_GUIDELINES.md)** - Code standards for all contributions

### Contribution Area Guides

- **[Full Agent Providers](adding-full-agent-provider.md)** - Build monolithic STT+LLM+TTS providers
- **[Pipeline Adapters](adding-pipeline-adapter.md)** - Build modular STT, LLM, or TTS adapters
- **[Pre-Call Hooks](pre-call-hooks-development.md)** - Enrich calls with CRM/database lookups
- **[In-Call Hooks](in-call-hooks-development.md)** - AI-invoked tools during conversation
- **[Post-Call Hooks](post-call-hooks-development.md)** - Webhooks to Slack, Discord, CRM, etc.
- **[Testing Guide](testing-guide.md)** - Test your changes with real calls
- **[Testing Develop Branch](testing-develop-branch.md)** - Try new features while preserving your configs
- **[Debugging Guide](debugging-guide.md)** - Debug with logs, RCA scripts, and agent CLI
- **[Code Style](code-style.md)** - Project conventions and best practices

### Technical Deep Dives

- **[Architecture Deep Dive](architecture-deep-dive.md)** - Complete technical architecture
- **[Schema Reference](schema-reference.md)** - Tool schema formats by provider
- **[API Reference](api-reference.md)** - Core API documentation

## 📚 References

### Provider Implementation Details

Technical specs for each provider:

- **[Provider-Google-Implementation.md](references/Provider-Google-Implementation.md)** - Google Live API internals
- **[Provider-Deepgram-Implementation.md](references/Provider-Deepgram-Implementation.md)** - Deepgram Voice Agent internals
- **[Provider-OpenAI-Implementation.md](references/Provider-OpenAI-Implementation.md)** - OpenAI Realtime API internals

### Case Studies

- **[Local Hybrid Pipeline Implementation](references/Pipeline-Local_Hybrid-Implementation.md)** - Tool execution for pipelines (real-world example)
- **[Team Setup Guide](references/team-setup.md)** - Linear MCP integration

### Development History

- **[Milestones](milestones/)** - Major features and architectural decisions

## 🧪 Code Examples

- **[Tool Examples](../../examples/)** - Creating custom tools
- **[Provider Examples](../../examples/)** - Implementing custom providers
- **[Testing Examples](../../tests/README.md)** - Integration/unit test patterns

## 🛠️ Tools & Workflows

### Development Workflow

1. Fork and clone the repository
2. Create a feature branch from `develop`
3. Make changes following our code style
4. Test with real calls using `agent rca`
5. Submit PR with testing evidence

### Using Agent CLI

```bash
agent check           # Standard diagnostics report
agent rca             # Post-call root cause analysis (most recent call)
agent setup           # Interactive setup wizard (if needed)
agent update          # Pull latest code + apply updates
```

See [cli/README.md](../../cli/README.md) for complete CLI reference.

### Using RCA Scripts

```bash
./scripts/rca_collect.sh              # Collect full diagnostic bundle
./scripts/analyze_logs.py --call-id   # Analyze specific call
```

## 📖 User Documentation

For end-user and operator documentation, see the parent [/docs](../) directory:

- **[Installation Guide](../INSTALLATION.md)** - Setup instructions
- **[FreePBX Integration](../FreePBX-Integration-Guide.md)** - Dialplan configuration
- **[Production Deployment](../PRODUCTION_DEPLOYMENT.md)** - Production best practices
- **[Monitoring Guide](../MONITORING_GUIDE.md)** - Prometheus + Grafana
- **[Tool Calling Guide](../TOOL_CALLING_GUIDE.md)** - Using tools from caller perspective

## 🤝 Contributing Guidelines

- Read the root [CONTRIBUTING.md](../../CONTRIBUTING.md) for Git workflow and PR process
- Follow the code style in [code-style.md](code-style.md)
- Add tests for new features
- Update documentation as needed
- Make at least one test call for telephony changes

## 🆘 Getting Help

- **Join our Discord:** [https://discord.gg/ysg8fphxUe](https://discord.gg/ysg8fphxUe) - Community support and discussions
- **Stuck on something?** Check [Common Pitfalls](COMMON_PITFALLS.md)
- **Architecture questions?** See [Architecture Deep Dive](architecture-deep-dive.md)
- **Tool issues?** See [Tool Development](tool-development.md)
- **Provider issues?** See [Provider Development](provider-development.md)

## 📅 Project Status

- **Current Version:** 6.2.0
- **Active Branch:** `develop`
- **Roadmap:** See [/docs/ROADMAP.md](../ROADMAP.md)
- **Community Features:** GitHub Issues + Linear integration

---

**Ready to contribute?** Start with the [Quick Start Guide](quickstart.md)! 🚀
