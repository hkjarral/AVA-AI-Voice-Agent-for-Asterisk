<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/banner_dark_mode.png?v=9">
  <source media="(prefers-color-scheme: light)" srcset="assets/banner_light_mode.png?v=9">
  <img alt="AVA Core — AI Voice Agent for Asterisk" src="assets/banner_light_mode.png?v=9" width="100%">
</picture>

[![Version](https://img.shields.io/badge/version-7.6.4-blue.svg)](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/releases)
[![CI](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Discord](https://dcbadge.limes.pink/api/server/ysg8fphxUe?style=plastic)](https://discord.gg/ysg8fphxUe)

</div>

AVA Core is an open-source AI voice agent for Asterisk and FreePBX. Build phone
agents using realtime voice providers or modular speech-to-text, language-model,
and text-to-speech pipelines, managed through a self-hosted Admin UI.

> **Managing multiple PBXs or customer installations?** [Explore AVA Operator](https://operator.agent6789.com/?utm_source=github&utm_medium=readme&utm_campaign=ava_operator_preview&utm_content=core_readme) — the commercial multi-installation management layer built around AVA Core, currently available as an early-access preview. AVA Core remains MIT-licensed, free, and fully functional on its own.

<a id="-table-of-contents"></a>

[Quick Start](#quick-start) · [Capabilities](#capabilities) · [Demo](#demo) ·
[Documentation](#documentation) · [Contributing](#contributing) · [Community](#community-and-support)

<a id="-quick-start"></a>
<a id="-requirements"></a>

## Quick Start

For a fresh installation, use a Linux x86_64 host with Docker Compose v2 and an
Asterisk 18+ / FreePBX system with ARI enabled. Cloud configurations start with
2 CPU cores and 4 GB RAM; local inference needs additional resources. Check
[supported platforms](docs/SUPPORTED_PLATFORMS.md) and
[transport compatibility](docs/Transport-Mode-Compatibility.md) first; some
transports require newer Asterisk versions or additional modules.

**Already running AVA?** Follow the
[v7.6.4 upgrade guide](docs/INSTALLATION.md#upgrade-to-v764-existing-checkout)
instead of cloning another installation.

On the AVA host:

```bash
git clone --branch v7.6.4 https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk.git
cd AVA-AI-Voice-Agent-for-Asterisk
sudo ./preflight.sh --apply-fixes
docker compose -p asterisk-ai-voice-agent up -d --build admin_ui
```

Preflight prepares `.env`, generates the JWT secret, and checks PBX/media
permissions. Review its findings before continuing.

Open `http://<server-ip>:3003`. Retrieve the one-time admin password:

```bash
docker compose -p asterisk-ai-voice-agent logs admin_ui | grep -i password
```

Change the password at first login. **Restrict port 3003 to trusted access** using
a firewall, VPN, or an authenticated TLS reverse proxy; do not expose plain HTTP
admin access to the public internet.

Complete the Setup Wizard, configure the generated Asterisk dialplan, and start
the AI Engine as described in the [installation guide](docs/INSTALLATION.md).
Verify engine health and make a test call before routing callers to the agent.
The guide also covers CLI/manual installation, permissions, and recovery;
[local AI setup](docs/LOCAL_ONLY_SETUP.md) covers CPU/GPU configurations.

<a id="-features"></a>
<a id="-why-asterisk-ai-voice-agent"></a>
<a id="-ai-powered-actions"></a>
<a id="-admin-ui"></a>

## Capabilities

- **Realtime or modular voice:** use a full voice-agent provider, or select
  separate STT, LLM, and TTS backends.
- **Self-hosted management:** configure providers and Agents, inspect Call
  History, filter/export records, and manage updates through the Admin UI.
- **Telephony tools:** transfers, extension availability, voicemail, and hangup
  controls integrated with Asterisk.
- **Business integrations:** Google/Microsoft calendars, email, HTTP tools, and
  MCP tools with per-Agent access controls.
- **Cloud, hybrid, or local operation:** choose where audio and model inference
  run. Hybrid deployments still send the configured inputs to cloud services.
- **Operational diagnostics:** CLI health checks, sanitized support packages,
  metrics, and documented upgrade/recovery paths.

### Providers and deployment modes

| Mode | Options | Setup |
| --- | --- | --- |
| Realtime voice agents | Deepgram, OpenAI Realtime, Google Live/Vertex, ElevenLabs, xAI Grok | [Provider guides](docs/README.md#provider-setup-guides) |
| Modular pipelines | Mix supported STT, LLM and TTS adapters, including OpenAI-compatible endpoints | [Pipeline configuration](docs/Configuration-Reference.md#pipeline-configurations) |
| Local or hybrid | Local speech and/or LLM inference, optional GPU acceleration and remote local-model servers | [Local setup](docs/LOCAL_ONLY_SETUP.md) |

Model availability, tools, latency, and transport support vary by configuration.
Use the [compatibility matrix](docs/Transport-Mode-Compatibility.md) and
[release validation evidence](docs/baselines/golden/v7.6.4-validation-matrix.md)
rather than assuming every provider/transport combination is qualified.

AudioSocket and ExternalMedia RTP are configuration-dependent; Media WebSocket
is opt-in and has additional Asterisk requirements. See the
[WebSocket guide](docs/WebSocket-Transport.md).
[Outbound campaigns](docs/OUTBOUND_CALLING.md) and
[VICIdial integration](docs/Vicidial-Setup.md) remain alpha;
[FreeSWITCH setup](docs/FS-PBX-Setup-Instructions.md) is community-maintained.

<a id="-demo"></a>

## Demo

[![Watch the demo](https://img.youtube.com/vi/fDZ_yMNenJc/hqdefault.jpg)](https://youtu.be/fDZ_yMNenJc "Asterisk AI Voice Agent v6.1 Deep Dive")

[Demo homepage](https://demo.agent6789.com) ·
[Setup videos](https://www.youtube.com/@Jugaarooo)

Try the US demo lines and follow the current voice menu:

- **Standard voice:** (925) 736-6718
- **HD voice:** (909) 788-2282

HD voice requires a G.722-capable carrier/device path and the opt-in
`wideband_pcm_16k` Audio Profile; other calls fall back to standard telephony
audio. The video is a v6.1 walkthrough, not a description of every current UI
screen or model.

<a id="-documentation"></a>
<a id="-agent-cli-tools"></a>
<a id="-advanced-setup-cli"></a>
<a id="-configuration"></a>
<a id="-project-architecture"></a>

## Documentation

| I want to… | Start here |
| --- | --- |
| Install, upgrade, or recover | [Installation](docs/INSTALLATION.md) · [Migration notes](docs/MIGRATION.md) |
| Connect my PBX | [FreePBX guide](docs/FreePBX-Integration-Guide.md) · [Transport matrix](docs/Transport-Mode-Compatibility.md) |
| Configure providers and Agents | [Admin UI](docs/ADMIN_UI_GUIDE.md) · [Agents](docs/AGENTS.md) · [Configuration](docs/Configuration-Reference.md) |
| Add actions and integrations | [Tools](docs/TOOL_CALLING_GUIDE.md) · [MCP](docs/MCP_INTEGRATION.md) |
| Operate and troubleshoot | [CLI](docs/CLI_TOOLS_GUIDE.md) · [Troubleshooting](docs/TROUBLESHOOTING_GUIDE.md) · [Monitoring](docs/MONITORING_GUIDE.md) |
| Understand or extend the code | [Architecture](docs/contributing/architecture-quickstart.md) · [Developer guides](docs/contributing/README.md) |

The core services are `ai_engine` and `admin_ui`; `local_ai_server` and
`updater` are optional. Run only the services required by your deployment.

[All documentation](docs/README.md) · [Roadmap](docs/ROADMAP.md) ·
[Security policy](SECURITY.md) ·
[Ask DeepWiki](https://deepwiki.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk)

<a id="-whats-new"></a>

### Latest release: v7.6.4

- Safer HTTP-tool templates with single-pass substitution and protected call data.
- Farewell and transfer-recovery fixes that prevent stale hangups.
- Compatible Admin dependency updates and refreshed upgrade guidance.

See the [changelog](CHANGELOG.md) for detailed changes and contributor credit,
[GitHub Releases](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/releases)
for artifacts, and [migration notes](docs/MIGRATION.md#v763-to-v764) before upgrading.
Earlier release notes remain in the changelog and versioned repository history.

<a id="-contributing"></a>

## Contributing

Code, documentation, tests, bug reports, and real-world validation are welcome.
Start with [CONTRIBUTING.md](CONTRIBUTING.md) and the
[developer quickstart](docs/contributing/quickstart.md).
Include reproducible evidence and follow the
[PR review workflow](docs/contributing/PULL_REQUEST_WORKFLOW.md).
Documentation and many automated tests do not require a PBX.

If you use an AI coding assistant, [AVA.mdc](AVA.mdc) provides optional project
context. All contributions still require appropriate testing and review.

## Contributors

<table>
<tr>
<td align="center"><a href="https://github.com/hkjarral"><img src="https://github.com/hkjarral.png" width="60" alt="hkjarral"><br><sub><b>hkjarral</b></sub></a><br>Architecture, Code</td>
<td align="center"><a href="https://github.com/a692570"><img src="https://github.com/a692570.png" width="60" alt="a692570"><br><sub><b>Abhishek</b></sub></a><br>Telnyx LLM Provider</td>
<td align="center"><a href="https://github.com/turgutguvercin"><img src="https://github.com/turgutguvercin.png" width="60" alt="turgutguvercin"><br><sub><b>turgutguvercin</b></sub></a><br>NumPy Resampler</td>
<td align="center"><a href="https://github.com/Scarjit"><img src="https://github.com/Scarjit.png" width="60" alt="Scarjit"><br><sub><b>Scarjit</b></sub></a><br>Code</td>
<td align="center"><a href="https://github.com/egorky"><img src="https://github.com/egorky.png" width="60" alt="egorky"><br><sub><b>egorky</b></sub></a><br>Azure STT/TTS Provider</td>
</tr>
<tr>
<td align="center"><a href="https://github.com/alemstrom"><img src="https://github.com/alemstrom.png" width="60" alt="alemstrom"><br><sub><b>alemstrom</b></sub></a><br>Docs — PBX Setup</td>
<td align="center"><a href="https://github.com/gcsuri"><img src="https://github.com/gcsuri.png" width="60" alt="gcsuri"><br><sub><b>gcsuri</b></sub></a><br>Code — Google Calendar</td>
<td align="center"><a href="https://github.com/octo-patch"><img src="https://github.com/octo-patch.png" width="60" alt="octo-patch"><br><sub><b>octo-patch</b></sub></a><br>MiniMax LLM Provider</td>
<td align="center"><a href="https://github.com/neilruaro-camb"><img src="https://github.com/neilruaro-camb.png" width="60" alt="neilruaro-camb"><br><sub><b>neilruaro-camb</b></sub></a><br>CAMB AI TTS Provider</td>
<td align="center"><a href="https://github.com/Ai-chan-0411"><img src="https://github.com/Ai-chan-0411.png" width="60" alt="Ai-chan-0411"><br><sub><b>Ai-chan-0411</b></sub></a><br>Transcript Search, Health Badges</td>
</tr>
<tr>
<td align="center"><a href="https://github.com/exaland"><img src="https://github.com/exaland.png" width="60" alt="exaland"><br><sub><b>exaland</b></sub></a><br>Outbound .ULAW Compatibility</td>
<td align="center"><a href="https://github.com/YosefAdPro"><img src="https://github.com/YosefAdPro.png" width="60" alt="YosefAdPro"><br><sub><b>YosefAdPro</b></sub></a><br>Agents API/OpenAPI</td>
<td align="center"><a href="https://github.com/cswilsnetex"><img src="https://github.com/cswilsnetex.png" width="60" alt="cswilsnetex"><br><sub><b>Chris Wilson</b></sub></a><br>Google Live, Transfer Handoff, Admin UI</td>
<td align="center"><a href="https://github.com/Rea-PC08"><img src="https://github.com/Rea-PC08.png" width="60" alt="Rea-PC08"><br><sub><b>Rea-PC08</b></sub></a><br>Fish Audio TTS, Call History Filters</td>
<td align="center"><a href="https://github.com/arnavp27"><img src="https://github.com/arnavp27.png" width="60" alt="arnavp27"><br><sub><b>Arnav Patil</b></sub></a><br>Admin UI Accessibility, Numeric Alignment, Empty States</td>
</tr>
<tr>
<td align="center"><a href="https://github.com/alceops"><img src="https://github.com/alceops.png" width="60" alt="alceops"><br><sub><b>Alce</b></sub></a><br>OpenAI Streaming Timeouts</td>
<td align="center"><a href="https://github.com/Genmin"><img src="https://github.com/Genmin.png" width="60" alt="Genmin"><br><sub><b>Joey Roth</b></sub></a><br>OpenAI Tool-call Tests</td>
<td align="center"><a href="https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/commit/3053c616"><img src="https://img.shields.io/badge/contributor-Ivan%20Garcia-blue" alt="Ivan Garcia contributor badge"><br><sub><b>Ivan Garcia</b></sub></a><br>Azure STT/TTS, OpenAI GA Tools</td>
</tr>
<tr>
<td align="center"><a href="https://github.com/pi0n00r"><img src="https://github.com/pi0n00r.png" width="60" alt="pi0n00r"><br><sub><b>pi0n00r</b></sub></a><br>Local AI, Whisper, DTMF Privacy, MCP</td>
<td align="center"><a href="https://github.com/MVECoder"><img src="https://github.com/MVECoder.png" width="60" alt="MVECoder"><br><sub><b>MVECoder</b></sub></a><br>Startup Recovery, Farewell Investigation</td>
<td align="center"><a href="https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/commit/06f039dcf293128fa55478cb4bbd584fadf8f131"><img src="https://img.shields.io/badge/contributor-NC-blue" alt="NC contributor badge"><br><sub><b>NC</b></sub></a><br>Local AI Environment Parser</td>
<td align="center"><a href="https://github.com/theluckystrike"><img src="https://github.com/theluckystrike.png" width="60" alt="theluckystrike"><br><sub><b>Michael (theluckystrike)</b></sub></a><br>Farewell Hangup Fix</td>
<td align="center"><a href="https://github.com/kevinrossen"><img src="https://github.com/kevinrossen.png" width="60" alt="kevinrossen"><br><sub><b>Kevin Rossen</b></sub></a><br>HTTP Template Security</td>
</tr>
</table>

See [CONTRIBUTORS.md](CONTRIBUTORS.md) for contribution details and historical
credit. Commit-linked badges preserve attribution when no GitHub profile is
linked; they do not imply a verified account identity.

## AVA Operator

AVA Operator helps MSPs and operators manage AI voice across multiple Asterisk and FreePBX installations from one console. It builds on AVA Core while remaining a separate commercial product.

[Explore the early-access preview →](https://operator.agent6789.com/?utm_source=github&utm_medium=readme&utm_campaign=ava_operator_preview&utm_content=core_readme)

<a id="-community"></a>
<a id="-support-this-project"></a>

## Community and support

[Discord](https://discord.gg/ysg8fphxUe) ·
[Issues](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues) ·
[Discussions](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/discussions)

AVA Core is independently maintained. [GitHub Sponsors](https://github.com/sponsors/hkjarral)
and [Ko-fi](https://ko-fi.com/asteriskaivoiceagent) support compatibility testing,
maintenance, and release infrastructure.
[Book a consultation](https://meetify.com/aava1) for dedicated help.

<a id="star-history"></a>

[Product Hunt](https://www.producthunt.com/products/ava-ai-voice-agent-for-asterisk) ·
[Star history](https://www.star-history.com/?repos=hkjarral%2FAVA-AI-Voice-Agent-for-Asterisk&type=date&legend=top-left)

<a id="-license"></a>

## License

AVA Core is MIT-licensed. See [LICENSE](LICENSE).
