# AudioSocket upstream squelch

Issue [#799](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues/799)
identified a caller-muting defect: when quiet onset frames were missing, the
first speech frame initialized the noise floor at speech energy. Multiplying
that estimate by the noise factor could suppress the entire utterance.

## Scope

| Provider/mode | AudioSocket | ExternalMedia RTP / Media WebSocket |
| --- | --- | --- |
| OpenAI Realtime, Deepgram, Google Live/Vertex, Grok, ElevenLabs Agent | Squelch applies when enabled and caller capture is enabled | Existing continuous-input path; no upstream squelch |
| Local full agent | Bypasses squelch: does not advertise native VAD | Unchanged |
| Modular STT/LLM/TTS pipelines | Pipeline routing bypasses squelch | Unchanged |

Eligibility uses provider capabilities, including for named provider instances;
it does not depend on a provider name allowlist. Existing provider-specific TTS
gating and barge-in ownership remain in place. This fix does not add squelch to
other transports or qualify previously unverified provider/transport combinations.

## Classification and compatibility

The initial floor is bounded so its multiplied threshold cannot exceed
`upstream_squelch_base_rms`. With defaults, initial RMS must exceed **200**,
not 500. Each frame is classified against the prior noise floor. Only
below-threshold frames while not speaking update the EMA; speech candidates
cannot raise the floor while onset confirmation is pending.

The configured minimum speech frames and end-silence frames are preserved.
With the default two-frame onset, the first speech frame is still replaced
with silence; no audio buffering or replay is introduced. Existing learned
floors survive output gating and belong to their call's session. New calls
start fresh; there is no persistent state migration or database change.

**Tradeoff:** energy alone cannot distinguish immediate speech from equally
loud background noise. Startup now favors hearing the caller. Sustained loud
noise may open the squelch and remain forwarded until enough below-threshold
frames arrive. The provider's native VAD still owns turn detection. This is
consistent with loud noise arriving after an established quiet floor; this
change does not promise acoustic speech/noise discrimination. Learned dynamic
thresholds can still suppress quiet callers. Test noisy environments before
rollout; disabling squelch restores continuous provider input, not the old bug.

All six existing settings and their defaults remain unchanged. Use **Advanced →
VAD → Upstream Squelch**, save, then restart AI Engine after calls drain. The
backend validates/persists the same canonical VAD fields and recommends restart.
No new credentials, Agent settings, wizard steps or provider API changes are needed.
A code rollback requires restoring the prior engine source/image and restarting;
operator configuration and databases do not need conversion.

## Diagnostics

The existing rate-limited DEBUG forwarding event now reports
`squelch_applicable` and `squelch_suppressed` separately from TTS gating. The
`RCA_CALL_END` INFO event includes `upstream_squelch_frames` and
`upstream_squelch_suppressed_frames`, counting evaluated and substituted frames,
including ordinary silence and onset delay. These totals do not prove speech
recognition, provider delivery or caller audibility. `media_rx_confirmed` still
means transport audio arrived. Existing transport/provider RMS diagnostics are
also available. No new per-frame log events or audio capture are enabled.

## Acceptance and regression checks

Automated tests exercise the real ingress handlers with synthetic PCM and real
provider capability declarations. They cover missing quiet onset, speech near
the base threshold, configured smoothing/hysteresis, all AudioSocket signed-linear
rates and legacy μ-law decoding, noise and isolated spikes, subsequent turns,
greeting release, capability/disabled/pipeline bypasses, and unchanged RTP/WebSocket
continuous-input behavior. These are mock-provider checks, not live provider proof.

For live acceptance, confirm the deployed revision and effective squelch settings:

1. On an affected AudioSocket provider, speak immediately as the greeting ends;
   verify the question is heard and answered. Repeat with an ordinary pause.
2. Continue with a second question, interrupt a response, then request hangup.
   Verify farewell completion and zero remaining sessions/channels/playbacks.
3. Repeat with quiet speech and realistic background noise. Confirm noise does
   not cause unacceptable false turns or interfere with end-of-turn detection.
4. Smoke-test another native-VAD provider, Local and a modular pipeline. Use an
   existing supported RTP configuration for an unchanged-transport comparison.
   Media WebSocket needs its own qualified PBX/version configuration.

Record provider/model, transport, revision, call window, call IDs and caller
observations. Archive raw evidence privately before analysis. Logging and audio
capture remain user-controlled; a support package or synthetic test does not
replace the caller's listening observations.

The similar noise-floor initialization in the independent no-input detector is
separate follow-up work: changing it affects inactivity/hangup policy on all
transports and requires its own acceptance evidence.
