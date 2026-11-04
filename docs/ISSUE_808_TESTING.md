# Issue #808: reporter testing and capture guide

Historical reporter comparison points are listed below. The reporter qualified
the original fixes with the OpenAI mock on certified 22.8-cert4; later hardening
and other provider/pipeline combinations require their own qualification.
Production defaults are unchanged. The original fixes were separate commits:

| Ref | Purpose |
| --- | --- |
| `99560e166f109de68385158f5fb959aa83d953da` | Original main baseline used in the report |
| `114413ce` | A: opt-in Asterisk-owned WebSocket output pacing |
| `c17da6c7` | B: OpenAI natural-completion tail, layered on A |

Historical comparison branch: `codex/issue-808-websocket-pacing-openai-tail`.
These refs document the reported experiment; they are not current rollout
targets and may not remain reachable after squash merge. Pin the release or
PR head to its exact full SHA for new runs. B was independently cherry-picked
onto the baseline, and was also tested with A's pacing mode disabled.

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

A full provider output queue in Asterisk pacing mode is an explicit call
failure: the engine logs an error, aborts queued output and terminates the call
rather than delivering silently truncated speech or claiming transfer audio
completed. The 256-entry provider queue remains bounded; stress qualification
must include bursts beyond that budget. Legacy overflow policy is unchanged.
Failed engine-requested announcements return failure, including a failure that
races transport drain; aborted audio is not recorded as a spoken apology.

## UI selection and final-candidate qualification

On builds including the UI hardening, choose **Advanced Settings → Audio
Transport → WebSocket → Output pacing**. The options are **Legacy — default**
and **Asterisk pacing — experimental**. Save, verify that the readiness card
shows the saved mode separately from the running mode, drain active calls,
and use **Recreate AI Engine**. Confirm running pacing after apply. Returning
to the page or switching transports must preserve the saved choice. Raw YAML
remains an alternative; edit the existing authenticated block rather than
replacing it with the minimal example above.

The reporter qualified `2ea738b0` on certified 22.8-cert4 with the pinned mock:
[comparison and follow-up](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues/808#issuecomment-6624744622).
The follow-up confirms the full tail, including all 13 namespace-captured legacy
answers ending on the clip's last non-zero sample. The earlier 60 ms deficit
was a reference/voiced-threshold comparison error. Normalization-off runs used
a separate schema-only diagnostic patch; that patch is not part of this fix.
The existing normalizer configuration and caller-input gate remain separate
issues. Injected `speech_started` events verify cancellation, not acoustic VAD.

The final hardening adds UI selection, file-mode protection, response failure
recovery, and JSON playback progress marks. Record the exact new SHA when
qualifying it. Check that `MARK_MEDIA`/`MEDIA_MARK_PROCESSED` progress does not
change media order, XOFF/XON behavior, or completion/FLUSH fences. For Grok,
confirm `conversation.item.truncate.audio_end_ms` follows acknowledged playback,
not seconds of queued audio. Queue a following response behind older speech
and interrupt before its first frame: it must retain zero, never inherit the
older item's acknowledgment. Before the first mark, zero is intentional; plain
controls retain zero until a segment completion is acknowledged.

Repeat a long/bursty answer **without interrupting it** through natural
completion: the prior 1,261-frame burst was admitted completely but the caller
flushed playback after roughly 14 seconds. Automated regressions now exercise
full natural drain through repeated flow-control cycles. Retain the previous
short-clip, next-response, farewell and transfer checks on the final candidate.
Real providers, modular pipelines, other codecs/versions and load remain scoped
qualification work; the mock results do not certify every combination.

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

Promote only qualified combinations to canary use before considering a
default change. The branch and commit references above are historical reporter
comparison points, not a promise that later builds are acoustically qualified.
