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

## Post-release Hygiene

- Update `CHANGELOG.md`
- Ensure `docs/baselines/golden/` matches current known-good behavior
- Update `docs/SUPPORTED_PLATFORMS.md` if new Tier-2 platforms were verified

## Documentation Checklist

- [ ] Version references in `docs/INSTALLATION.md` updated to new version
- [ ] `SECURITY.md` supported versions table reflects current release series
- [ ] `docs/ROADMAP.md` "What's Next" section reflects current state
- [ ] `docs/README.md` links verified (no broken links to renamed/deleted files)
- [ ] `docs/contributing/README.md` "Current Version" updated
- [ ] `README.md` version badge updated
- [ ] `AVA.mdc` reviewed for drift (provider roster, architecture vocabulary, guardrails, "Last verified" stamp) — contributors load this file into their AI assistants first; stale content actively misleads
