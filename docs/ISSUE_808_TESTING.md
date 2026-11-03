# Issue #808: reporter testing and capture guide

Status: testing branch; production defaults are unchanged. Live acoustic
acceptance remains pending. The two fixes are separate commits:

| Ref | Purpose |
| --- | --- |
| `99560e166f109de68385158f5fb959aa83d953da` | Original main baseline used in the report |
| `114413ce` | A: opt-in Asterisk-owned WebSocket output pacing |
| `c17da6c7` | B: OpenAI natural-completion tail, layered on A |

Branch: `codex/issue-808-websocket-pacing-openai-tail`. Record the exact full
SHA used for each run rather than assuming a moving branch still has the same
contents. B can be cherry-picked onto the baseline independently; the branch
also supports testing B with A's pacing mode left disabled.

## What changes

A adds `websocket_media.output_pacing: asterisk`. The streaming manager
forwards available converted media ahead instead of holding each frame for a
local 20 ms timer. Existing XOFF/XON backpressure and correlated completion
remain authoritative. The OpenAI/Grok call instance bypasses its optional
provider pacer in this mode, even when the saved provider setting is true.
Saved provider configuration is not rewritten. Other full-agent providers and
streaming pipeline TTS share the WebSocket sender; they require qualification
too. AudioSocket/RTP scheduling and ARI file playback remain unchanged.

B stops the OpenAI pacer, waits briefly for an in-flight admission to finish,
hands its remaining bytes to the downstream playback manager, and only then
publishes AgentAudioDone. It does not wait for the tail's speech duration in
the provider pacer. Warm-up and sub-frame clips are included. A stuck admission
fails visibly after a bounded wait rather than publishing successful completion.
Cancellation, failed/incomplete responses, disconnect and shutdown discard
the tail. Grok's completion implementation is not changed by B.

## Enable on an existing AVA installation

Use a separate checkout or test installation. Preserve the previous image and
configuration, drain active calls, and build the engine from the exact testing
SHA using the installation's normal build/apply procedure. Do not accidentally
pull a published main image after building the testing branch.

Merge this field into the existing authenticated WebSocket block:

```yaml
audio_transport: websocket
websocket_media:
  output_pacing: asterisk
```

Retain the rest of the listener/authentication configuration. Keep
`providers.openai_realtime.egress_pacer_enabled: true` for the main comparison;
the log should then show `Provider output pacing selected` with
`configured_pacer=true`, `effective_pacer=false`, and `output_clock=asterisk`.
For a named provider instance, check that instance's log and configuration.
Changing `output_pacing` requires an AI Engine restart; hot reload rejects
transport changes. Apply only after active calls have drained.

The default is `output_pacing: legacy`. With legacy mode and provider pacing
enabled, B remains active and should preserve full response tails, while the
WebSocket starvation addressed by A may remain.

## Reproduce with rtpbench 0.4.0

Use the same mock, clip, seed, Asterisk certified 22.8-cert4, profiles and machine
as the original report. Start with two repeats as a smoke check, then use ten
repeats for the comparison. Keep the original audio profile and conditioning
settings constant; do not tune the normalizer/resampler while testing pacing.

**Important:** rtpbench's `--source` selects AVA's code/Dockerfile. Its runtime
configuration still comes from **rtpbench's** `targets/ava/ai-agent.yaml`, not
the AVA checkout. The published 0.4.0 harness has no `AVA_OUTPUT_PACING` switch.
Editing only AVA's YAML or exporting that variable will not enable this fix.
Use an editable checkout of the pinned harness:

```bash
git clone --branch v0.4.0 --depth 1 https://github.com/jjaroztegi/rtpbench.git rtpbench-808
cd rtpbench-808
```

In `targets/ava/ai-agent.yaml`, add `output_pacing: asterisk` under the existing
`websocket_media` block for new-mode runs, or use `legacy` for legacy runs on
the testing branch. **Remove the field completely for baseline runs:** main's
strict schema does not recognize it, even when its value is legacy.

Point `AVA_SOURCE` at the absolute path of the AVA checkout and use a distinct
absolute output directory per setting. From the harness checkout:

```bash
AVA_SOURCE=/absolute/path/to/AVA-AI-Voice-Agent-for-Asterisk
RUN_DIR=/absolute/path/to/results/808-combined-websocket
mkdir -p "$RUN_DIR"

AVA_TRANSPORT=websocket AVA_EGRESS_PACER=true \
  uv run --extra vad rtpbench target ava --mock --source "$AVA_SOURCE" \
  --only latency,barge-in --profiles clean --runs 10 --seed 1 --out "$RUN_DIR"

git -C "$AVA_SOURCE" rev-parse HEAD > "$RUN_DIR/ava-sha.txt"
git -C "$AVA_SOURCE" status --porcelain > "$RUN_DIR/ava-status.txt"
git rev-parse HEAD > "$RUN_DIR/rtpbench-sha.txt"
git diff -- targets/ava/ai-agent.yaml > "$RUN_DIR/rtpbench-config.patch"
```

The command exercises ten latency calls and ten barge-in calls. For quick
smoke runs, use `--runs 2` and another output directory. The harness rebuilds
from the checkout; verify `conditions.json` identifies the intended SHA and
image, not a cached image from another ref. See the harness's
[pinned source/configuration behavior](https://github.com/jjaroztegi/rtpbench/blob/v0.4.0/docs/targets.md#built-from-a-checkout).

## Comparison matrix

| Run | AVA ref | Transport | output_pacing | AVA_EGRESS_PACER | Main question |
| --- | --- | --- | --- | --- | --- |
| Baseline | `99560e1` | websocket | field absent | true | Reproduce gaps and short tail |
| A only | `114413ce` | websocket | asterisk | true | Does transport-owned pacing remove the gaps? |
| B with A disabled | `c17da6c7` or testing branch | websocket | legacy | true | Is the full tail preserved even if gaps remain? |
| Combined | testing branch | websocket | asterisk | true | Gap removal plus complete speech |
| Pacer-off control | testing branch | websocket | legacy | false | Compare with original workaround |
| Tail control | testing branch | audiosocket | legacy | true | Verify B beyond the WebSocket bypass |

For AudioSocket, change `AVA_TRANSPORT=audiosocket` in the command. For the
pacer-off control, change `AVA_EGRESS_PACER=false`. Always adjust and archive
the harness YAML separately as described above. Do not combine these results
into one unnamed directory. Compare each control against its original/baseline
setting, not just against another transport's absolute timing.

## What to capture

Preserve the complete output directory from each setting, including:

- `conditions.json`, `summary.md`, `results.json`, `report.html`, and `target.log`.
- `realtime.jsonl`, every `caller.pcap`, `caller.json` and `turns.jsonl`.
- AVA/harness full SHAs, dirty-state record, exact command, config patch, image
  IDs/digests, Asterisk version and loaded WebSocket/timing modules.
- The exact mock reference clip, its sample rate/encoding and checksum if
  redistributable, or its pinned corpus identifier/checksum otherwise.
- Full engine and Asterisk logs for the call window with call IDs and UTC
  timestamps. Keep original raw logs in the private capture archive; do not
  upload `.env` files or credentials. Share sanitized configuration separately.

For one failing and one passing call, also capture the **AVA-to-Asterisk media
WebSocket** alongside caller RTP. Capture inside Asterisk/engine's shared
network namespace (including loopback), not only the host's external interface.
Include TCP media port 8787, the actual RTP ports from SDP, control messages,
and the whole response. This distinguishes AVA filler/late writes from silence
generated by Asterisk. With WSS, use endpoint logs or a suitable local test
capture; do not weaken authentication/TLS on an exposed installation.

Record these log markers when present: `Provider output pacing selected`,
`OpenAI pacer tail admitted`, transport completion/FLUSH timeouts, queue drops,
reconnects, and RCA call start/end. Note whether the symptom was a gap,
truncated tail, delayed interruption, repeated speech, or premature hangup.

## Acceptance and follow-up

- Align received speech to the original mock clip. There should be no newly
  inserted 20 ms zero-audio gaps inside speech on the combined WebSocket path.
  Exclude the clip's natural silent intervals and actual packet loss.
- Natural responses should include the full source tail, with at most the
  normal final transport-frame padding. Compare samples/duration rather than
  treating an RTP packet count alone as proof of completeness.
- Interrupted responses should stop promptly and must not resume in the next
  answer. Compare interruption latency distributions and first-audio latency
  with baseline; report any degradation rather than hiding it in medians.
- Exercise a greeting, sub-320 ms clip, long response crossing XOFF/XON,
  interruption during backpressure, repeated responses, farewell, and transfer.
  Farewell/transfer must follow genuine playback completion. No unexpected
  queue growth, failed fences, leaked sessions, or reconnect loops are accepted.
- If practical, repeat on a newer FreePBX 17/Asterisk build and record its
  exact version separately from certified 22.8-cert4. OpenAI mock acceptance
  does not qualify all real providers, pipelines, codecs, or concurrent load.

The earlier pacer-off residual metric was still below the AudioSocket result.
Gap removal alone is not a claim that every remaining fidelity difference has
been fixed. Evaluate residual distortion separately after timing alignment.

## Rollback

On the testing build, select `websocket_media.output_pacing: legacy`, drain
calls and restart the engine to disable A. B remains active; restore the
previous image for a full rollback. Before running an older build, remove
`output_pacing` from every YAML/override file entirely and restore the matching
configuration/image together. No database migration is involved.

Keep the PR in draft while reporter validation is pending. After results are
reviewed, batch any fixes, re-run affected tests, freeze the exact head and use
the repository's final CI/review gate. Promote only qualified combinations to
canary use before considering a default change.
