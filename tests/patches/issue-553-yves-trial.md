# Issue #553: Yves OpenAI resampler trial

> Experimental OpenAI-only test patch. Do not use this as a global provider fix.

Tracking issue: https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues/553

Yves has confirmed that he restored the working baseline. Verify these values; do not change them if they already match.

## 1. Verify the baseline in the Admin UI

1. **Audio Profiles**: default profile is `telephony_ulaw_8k`.
2. **Advanced Settings → Audio Transport**:
   - Transport Method: `AudioSocket (Default)`
   - Format: `slin`
3. **Advanced Settings → Streaming**: Sample Rate is `8000`.
4. **Providers → openai_france → Audio Configuration**:
   - Output Encoding: `Linear16`
   - Output Sample Rate: `24000`
   - Target Encoding: `mu-law`
   - Target Sample Rate: `8000`

Save only if a value needed correction. Do not press the orange Apply/Restart button while a call is active.

## 2. Download and check the patch

Run on the AAVA server:

```bash
cd /opt/AVA-AI-Voice-Agent-for-Asterisk

curl -fL \
  https://raw.githubusercontent.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/main/tests/patches/issue-553-openai-bandlimited-resampler.patch \
  -o /tmp/issue-553-openai-bandlimited-resampler.patch

git status --short -- \
  src/audio/resampler.py \
  src/config.py \
  src/providers/openai_realtime.py

git apply --check /tmp/issue-553-openai-bandlimited-resampler.patch
```

Stop and send us the output if `git status` reports changes to those three files or `git apply --check` fails.

## 3. Back up, apply, and enable diagnostics

```bash
cd /opt/AVA-AI-Voice-Agent-for-Asterisk

trial_stamp="$(date -u +%Y%m%d-%H%M%S)"
cp -p .env ".env.before-issue-553-${trial_stamp}"
echo "Environment backup: .env.before-issue-553-${trial_stamp}"

git apply /tmp/issue-553-openai-bandlimited-resampler.patch

if grep -q '^AAVA_OPENAI_OUTPUT_RESAMPLER=' .env; then
  sed -i 's/^AAVA_OPENAI_OUTPUT_RESAMPLER=.*/AAVA_OPENAI_OUTPUT_RESAMPLER=bandlimited/' .env
else
  printf '\nAAVA_OPENAI_OUTPUT_RESAMPLER=bandlimited\n' >> .env
fi

if grep -q '^AAVA_AUDIO_DIAGNOSTICS=' .env; then
  sed -i 's/^AAVA_AUDIO_DIAGNOSTICS=.*/AAVA_AUDIO_DIAGNOSTICS=true/' .env
else
  printf 'AAVA_AUDIO_DIAGNOSTICS=true\n' >> .env
fi

if grep -q '^DIAG_ENABLE_TAPS=' .env; then
  sed -i 's/^DIAG_ENABLE_TAPS=.*/DIAG_ENABLE_TAPS=true/' .env
else
  printf 'DIAG_ENABLE_TAPS=true\n' >> .env
fi

docker compose build ai_engine
docker compose up -d --force-recreate --no-build ai_engine
docker compose ps ai_engine
```

Do not use the Admin UI Apply Changes action for this experimental build.

## 4. Confirm the flag reached the container

```bash
docker inspect ai_engine --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E '^(AAVA_OPENAI_OUTPUT_RESAMPLER|AAVA_AUDIO_DIAGNOSTICS|DIAG_ENABLE_TAPS)=' \
  | sort
```

Expected:

```text
AAVA_AUDIO_DIAGNOSTICS=true
AAVA_OPENAI_OUTPUT_RESAMPLER=bandlimited
DIAG_ENABLE_TAPS=true
```

## 5. Make one OpenAI test call

Wait for every response to finish before speaking again. Say exactly:

> Repeat exactly: “Six sleek swans swam swiftly south.”
>
> Count from sixty through sixty-seven, one number at a time.
>
> Repeat exactly: “She saw a shiny silver shell on the seashore.”
>
> Say: “Sally sells seashells by the seashore.”
>
> That is all. Goodbye.

Immediately note whether the harsh `sss` sound is **improved**, **unchanged**, or **worse**.

## 6. Verify and collect the RCA

Replace `<CALL_ID>` with the Asterisk call ID:

```bash
docker logs --since 15m ai_engine 2>&1 \
  | grep 'OpenAI output resampler selected'

agent rca --call '<CALL_ID>' --no-llm --json \
  > "rca-<CALL_ID>-issue-553.json"
```

Every resampler line must show:

```text
configured_mode=bandlimited
active_mode=bandlimited
source_rate_hz=24000
target_rate_hz=8000
alias_safe=true
```

If `active_mode=linear` or any effective streaming/provider target is 16 kHz, stop: the test did not exercise the patch.

Send us:

- the call ID;
- `rca-<CALL_ID>-issue-553.json`;
- the debug-log ZIP or raw call archive;
- whether the `sss` sound was improved, unchanged, or worse.

## Rollback

Use the exact `.env.before-issue-553-*` backup printed during installation:

```bash
cd /opt/AVA-AI-Voice-Agent-for-Asterisk

curl -fL \
  https://raw.githubusercontent.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/main/tests/patches/issue-553-openai-bandlimited-resampler.patch \
  -o /tmp/issue-553-openai-bandlimited-resampler.patch

git apply -R --check /tmp/issue-553-openai-bandlimited-resampler.patch
git apply -R /tmp/issue-553-openai-bandlimited-resampler.patch
cp -p '<ENV_BACKUP_FROM_INSTALL_STEP>' .env

docker compose build ai_engine
docker compose up -d --force-recreate --no-build ai_engine
docker compose ps ai_engine
```

After rollback, `git status --short` must no longer list the three patched source files.