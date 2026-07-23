# Release Checklist (Golden Baselines)

CI is the hard gate for code quality (unit tests + lint/compile checks). Real-call “golden baselines” are the hard gate for **behavior**.

## CI Gate (Must Pass)

- GitHub Actions `CI` workflow green
- Admin UI frontend lint, Vitest, and production build green on the pull request
- Admin UI backend test job green
- Docker image size checks green
- Trivy scan artifacts uploaded (critical/high/medium)
- Release-candidate revision recorded; live-call evidence must run on that exact
  revision (or be repeated after any call-path change)

## Manual Golden Baselines (Must Pass Before Tagging)

Run at least one successful call for each baseline you intend to claim as supported.

**A call “passes” if:**

- Greeting is played completely (no dead air / cut-off)
- At least 2 user turns are transcribed and responded to correctly
- No obvious audio corruption (robotic artifacts, repeated segments, severe clipping)
- Clean hangup (no orphan channels / stuck Stasis sessions)
- No new `ERROR` spam in `ai_engine` during the call

**Record for each call:**

- Host OS + version
- Asterisk/FreePBX version
- Provider + transport
- Config snippet (redacted)
- Any warnings in logs
- Matrix row in `docs/baselines/golden/` — refresh per release: copy the most recent `v*-validation-matrix.md` to `v<NEW>-validation-matrix.md` and fill in the row for each provider/transport pair you validated. The on-disk format is pinned by the existing files in that directory.
- Structured `RCA_CALL_START` and `RCA_CALL_END` events, media-RX confirmation,
  revision, and post-call health. Archive raw evidence locally and record its
  non-sensitive evidence label in the matrix.

### Providers (AudioSocket)

- Deepgram Voice Agent (AudioSocket)
- OpenAI Realtime (AudioSocket)
- Google Live (AudioSocket)
- ElevenLabs Agent (AudioSocket)
- Local full (AudioSocket) OR Local core profile

### Providers (ExternalMedia RTP)

- Revalidate every provider/pipeline pair still claimed by
  `docs/Transport-Mode-Compatibility.md` and the provider setup guides. A
  historical pass is not sufficient for a new release candidate.

## v7.5.0 Audio Transport Gate

- Use `docs/baselines/golden/v7.5.0-validation-matrix.md`; record the exact
  candidate revision, Audio Profile, provider/pipeline, transport, call ID,
  archive label, objective media result, lifecycle result, and maintainer verdict.
- Confirm `telephony_ulaw_8k` remains the compatibility default and that
  `telephony_enhanced_8k` is opt-in, per-call, and immediately reversible by
  Agent reassignment.
- Validate every provider/pipeline claimed for the enhanced profile. Mark
  unavailable or waived lanes explicitly as untested; do not infer support from
  a different provider or from automated DSP tests alone.
- Remove diagnostic-only environment, tap, recording, and Compose overrides
  before the release-candidate freeze, then repeat the focused automated gate.
- Exercise a real update from the prior stable release through the Admin UI,
  including Compose validation, ownership recovery, service recreation, CLI
  update, post-update health, and at least one call on the deployed release head.

## v7.4.0 Agent Tools Gate

- Use `docs/baselines/golden/v7.4.0-validation-matrix.md`; retain exact call IDs,
  provider/Agent, tool generation, Call History evidence, cleanup, and post-call health.
- Verify upgrade from an actual v7.3.0–v7.3.3 checkout using the documented target-CLI
  bootstrap and each relevant local-change policy. Confirm the Admin UI planner no longer
  fails opaquely and updater backups contain both SQLite stores.
- Verify the atomic Context import with valid, invalid, empty, and already-populated Agent
  stores. Confirm `/contexts` shows its one-time notice and current navigation is Agent-only.
- Verify a genuinely empty setup creates only Receptionist, Sales, and Support, while an
  existing Agent store is never seeded or overwritten.
- Exercise allowed and denied/stale policies for transfer, Google Calendar, Microsoft
  Calendar, and voicemail. Confirm schema exposure, execution, audit metadata, and Call
  History agree on the effective Agent snapshot.
- Reload Tools during an active call. The active call must retain its captured generation,
  the next call must use the new generation, and invalid reload must preserve the last good
  generation without restarting AI Engine.
- Reproduce Tools Save & Apply from the deployed Admin UI container to prove temporary-file
  validation and atomic replacement permissions work on the supported checkout layout.
- Run provider/transport smoke calls after the tool work to exclude greeting, barge-in,
  farewell, transfer, cleanup, and post-call-hook regressions.

## v7.3.2 Stabilization Gate

- Use `docs/baselines/golden/v7.3.2-validation-matrix.md`; every required row
  must be `PASS`, `FAIL`, or explicitly removed from supported documentation.
- Verify the setup wizard can save a provider, create/select an agent, produce
  its dialplan snippet, and complete the first call without raw-YAML edits.
- Verify an invalid explicit pipeline records `pipeline_resolution_failed` and
  does not start the default provider.
- Verify `dialplan_redirect` provider-failure handling on the development PBX:
  continuation occurs once, auxiliary media is cleaned up, and the caller is
  not hung up. Also force continuation failure and confirm prompt/hangup fallback.
- Run updater update, validation-failure recovery, rollback, repeated rollback,
  and dirty-worktree/stash-conflict scenarios on the disposable development
  server. Do not use the production call host for destructive updater tests.
- Run `python3 scripts/index_call_archives.py --format markdown` and confirm the
  candidate revision has evidence for every matrix row without exposing caller
  identity, transcripts, prompts, or tool arguments.

### v7.3.2 release evidence status

- The supervised AudioSocket and ExternalMedia sweep has accepted evidence for
  every configured provider/pipeline pair, with exact call IDs and revisions in
  the matrix.
- Grok ExternalMedia has targeted current-candidate coverage for clean
  interruption, replacement-turn context, inactivity announcements, terminal
  drain, and `no_input_timeout` cleanup.
- Earlier accepted calls remain provisional until replayed on the frozen code
  candidate, as explicitly marked in the matrix.
- Setup-wizard first-run, deployed provider-failure redirect/fallback, and the
  final destructive updater/rollback sequence remain release-tag blockers.
- GitHub Actions remains authoritative for coverage, image-size, and security
  scanner jobs; local results do not replace green PR checks.

The `v7.3.2` tag is based on runtime merge `f49e35e0`; its release-prep commit
changes documentation metadata only. Outstanding strict-candidate replay debt
remains visible in the validation matrix rather than being rewritten as a pass.

## Post-release Hygiene

- Update `CHANGELOG.md`
- Ensure `docs/baselines/golden/` matches current known-good behavior
- Update `docs/SUPPORTED_PLATFORMS.md` if new Tier-2 platforms were verified

## Documentation Checklist (current release)

- [ ] `python3 scripts/check_release_docs.py` passes on the frozen release head
- [ ] `docs/INSTALLATION.md` contains the target release, exact pinned recovery commands, Compose preflight, and partial-deployment recovery path
- [ ] `docs/MIGRATION.md` marks the target release as shipped and describes its operator-visible migration/compatibility boundaries
- [ ] `SECURITY.md` lists the two most recent minor release trains as supported
- [ ] `docs/ROADMAP.md` and `docs/contributing/README.md` identify the target as latest stable
- [ ] `docs/README.md` links verify with no broken renamed/deleted files
- [ ] README, CHANGELOG, migration notes, and the release validation matrix describe the same scope and compatibility boundaries
- [ ] Provider, PBX, tool, and architecture examples use `AI_AGENT`; `AI_CONTEXT` remains only where deprecation/legacy behavior is being explained
- [ ] `AVA.mdc` is reviewed for Agent-only routing, tool generations, provider roster, guardrails, and its verification stamp
