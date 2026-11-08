# First successful call

This walkthrough applies to source checkouts containing the onboarding improvements
under **Unreleased**. Older releases may still skip the wizard after preflight or
emit `AI_AGENT=default`; open `/wizard` explicitly and use the actual active Agent
slug shown in **Agents**. Do not assume an Agent named `default` exists.

## 1. Prepare and sign in

Follow the [installation guide](INSTALLATION.md) to clone the repository and run:

```bash
sudo ./preflight.sh --apply-fixes
./install.sh --ui
docker logs admin_ui | grep -i password
```

Review preflight findings. Open `http://<ava-host>:3003` through trusted access,
sign in as **admin** with the one-time password from the logs, and change it.
Preflight creates `.env`; this does **not** complete onboarding. A fresh installation
opens the wizard. Existing active Agents or pending legacy Context migration are
recognized as an existing installation. **Skip Setup** explicitly selects manual setup.

Have your chosen provider credentials, Asterisk ARI credentials, PBX configuration
access, and a phone ready. Local model downloads/builds may take substantially longer
than cloud configuration; the wizard reports those separately.

## 2. Choose the provider and connect Asterisk

Choose OpenAI Realtime, Google Live, Deepgram, ElevenLabs Agent, Grok, Local Hybrid,
or Fully Local. Cloud keys are validated when you advance. Local Hybrid uses the
selected Groq, OpenAI or Ollama reasoning backend; it must not be overridden by a
different pipeline in the dialplan. Download the selected local models as prompted.

Enter the ARI host, port, HTTP/HTTPS scheme, username and password. These are ARI
credentials, not the Admin UI login. Keep the Stasis application name consistent
with the generated dialplan. **Test Connection** checks ARI access; it cannot prove
that audio reaches your phone. Saving is permitted before connectivity succeeds so
you can return to complete installation.

Select the deployment topology and audio transport:

| Deployment | Settings and checks |
| --- | --- |
| Asterisk and AVA on one machine | Use the appropriate local ARI address; the shipped AudioSocket listener uses loopback. |
| Separate machines, AudioSocket | Enter the PBX's ARI address and the **AVA IPv4 address reachable from the PBX**. The wizard sets the AVA listener to `0.0.0.0` and its advertised address. Permit TCP to the configured AudioSocket port (default 8090) only from the PBX. |
| Separate machines, RTP | Enter the reachable AVA IPv4 address and the PBX source IP. A hostname needs an explicit source IP for the RTP allowlist. Permit the configured UDP range (default 18080–18099) only from the PBX. |
| Existing Media WebSocket | Existing authentication/listener configuration is retained. Follow the [WebSocket guide](WebSocket-Transport.md); new WebSocket provisioning is not performed by this wizard. |

The AVA media address is **not** the PBX address. A working ARI connection only
proves the control direction. The PBX must also reach AVA's media listener.
For file playback, including pipeline streaming fallback, arrange the shared sounds
directory on the PBX; a symlink created on AVA does not provision a remote PBX.
See [transport compatibility](Transport-Mode-Compatibility.md) and
[FreePBX integration](FreePBX-Integration-Guide.md).

## 3. Save, start and check

**Finish Setup** saves secrets to `.env`, operator settings to
`config/ai-agent.local.yaml`, and creates Receptionist, Sales and Support only on
a genuinely empty installation. Receptionist becomes the default. Existing Agents
are preserved; change their provider, prompt and greeting in **Agents**.

“Configuration saved” is not a readiness claim. Use **Start AI Engine** to build/start
the container. If it is already running, use **Apply & Restart Engine** to load
changed environment settings, respecting the active-call warning. Fully Local also
needs its Local AI server ready. A readiness timeout remains pending instead of
being reported as successful setup.

The final step refreshes these checks:

- Engine responding.
- ARI connected.
- Engine media listener ready.
- Engine default provider or pipeline ready.
- Active default Agent found.

These are configuration/service checks. They do not certify provider audio quality,
remote firewall reachability, or what the caller hears.

## 4. Install the generated dialplan and route a test call

Copy the **actual** snippet shown by the wizard. It uses the active default Agent
and effective Stasis application name. For a fresh standard installation:

```asterisk
[from-ai-agent]
exten => s,1,NoOp(AVA agent receptionist)
 same => n,Set(AI_AGENT=receptionist)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
```

There is deliberately no `AI_PROVIDER` override: the Agent owns its provider/pipeline.
`AI_AGENT=default` selects a literal slug; omitting `AI_AGENT` selects the database
default. Unknown/deactivated Agents fail closed. Do not add `AudioSocket()` here;
the engine creates the media channel through ARI.

### FreePBX

1. Preserve existing dialplan and add the generated context to
   `/etc/asterisk/extensions_custom.conf`. Resolve any duplicate context first.
2. Save and **Apply Config**.
3. Under **Admin → Custom Destinations**, create destination
   `from-ai-agent,s,1`, named **AVA test**.
4. Select it on a test inbound route, an IVR option, or a Misc Application with an
   unused dialable feature code. Apply Config again.
5. Dial the number, IVR option or feature code you configured. The context's `s`
   extension is not itself a phone number.

### Plain Asterisk

Add the generated context to `extensions.conf` or an included custom file. In the
context used by your test phone, choose an unused extension and route it with
`Goto(from-ai-agent,s,1)`. Then run:

```bash
asterisk -rx "dialplan reload"
asterisk -rx "dialplan show from-ai-agent"
```

Dial that test extension from the phone. For either PBX type, the generated Stasis
name must match the engine's effective `asterisk.app_name`.

## 5. Verify and finish

Listen for the greeting, speak a simple question and hear a relevant reply, then
hang up. Inspect **Call History** for that call. Check all four observations in the
wizard and choose **Confirm successful test call** once readiness checks pass.
Completion records your observation; it is not an automated acoustic test.

| Symptom | Next check |
| --- | --- |
| No greeting / immediate failure | Test route, Stasis app name, active Agent slug, then provider error in Call History/logs. |
| Greeting but no reply / one-way audio | Media bind and advertised addresses, PBX-to-AVA firewall, codec settings and shared media for file playback. |
| Engine check stays pending | ARI credentials/connection, selected provider/pipeline, Local AI models and engine logs. |
| Copy unavailable over HTTP | The snippet is selectable; copy manually. The UI also tries a browser-compatible fallback. |

Use `agent check` for installation diagnostics and `agent rca` for the most recent
call. See [troubleshooting](TROUBLESHOOTING_GUIDE.md). Configure optional transfers,
calendar booking and notifications in **Tools** after the first conversation works.

## Resume, existing installations and rollback

Unsaved non-secret choices survive refresh within the same browser tab. Unsaved
API keys and passwords are never written to browser storage and must be reentered.
Saved onboarding resumes at the first-call step after a restart/login; the Dashboard
also offers **Resume setup**. Effective base + local YAML and the current default
Agent greeting are loaded when reopening. Do not save over a configuration-load error.

Non-secret progress lives in `data/operator/onboarding.json`, independently of
`.env` and `agents.db`. Back up operator data and configuration normally. Older
releases ignore this file; it is not an Agent migration. A failed reconfiguration
invalidates prior onboarding acceptance and can be retried. Multi-file configuration
writes are not a transactional rollback; preserve the existing configuration backups.
Removing the progress file does not delete Agents or reset their settings.
