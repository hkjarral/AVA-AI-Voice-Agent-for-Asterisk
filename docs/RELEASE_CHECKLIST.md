# Release Checklist (Golden Baselines)

CI is the hard gate for code quality (unit tests + lint/compile checks). Real-call “golden baselines” are the hard gate for **behavior**.

## CI Gate (Must Pass)

- GitHub Actions `CI` workflow green
- Docker image size checks green
- Trivy scan artifacts uploaded (critical/high/medium)

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
- Matrix row in `docs/baselines/golden/v6.1.1-validation-matrix.md`

### Providers (AudioSocket)

- Deepgram Voice Agent (AudioSocket)
- OpenAI Realtime (AudioSocket)
- Google Live (AudioSocket)
- ElevenLabs Agent (AudioSocket)
- Local full (AudioSocket) OR Local core profile

### Providers (ExternalMedia RTP)

- Deepgram Voice Agent (ExternalMedia RTP)
- OpenAI Realtime (ExternalMedia RTP)
- Local hybrid pipeline (ExternalMedia RTP)

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
