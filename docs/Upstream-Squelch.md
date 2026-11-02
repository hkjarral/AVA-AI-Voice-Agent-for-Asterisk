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
not 500. Each frame is classified against the prior noise floor. Independently
detected speech does not train the noise floor during onset confirmation or
speaking. Independently detected **non-speech** can update the EMA even while
the energy squelch is open, allowing sustained startup noise to raise the
threshold and close the squelch again. Detected speech still has to exceed the
configured dynamic energy threshold; a classifier false positive alone cannot
reopen noise below a learned threshold. Zero EMA alpha disables adaptation.

The existing WebRTC dependency supplies this evidence in a separate call-local
detector. It is not shared with another call, local barge-in or no-input policy.
It runs independently of the selected VAD mode and does not own provider turn
detection. Real PCM is framed into 10 ms windows, with less than one window
buffered; unsupported WebRTC rates are resampled only in the detector copy.
No zero padding, buffering or replay is applied to provider audio. Observation
work is bounded to 120 ms per packet; oversized/malformed evidence is unknown.
Partial samples and resampler/detector state reset across output gating and rate
changes, while the learned energy floor remains call-scoped.

Missing or failed classification preserves caller audio instead of applying an
unreliable squelch; a once-per-call warning reports this bypass. Standard engine
requirements include `webrtcvad==2.0.10` and compatible setuptools, which supplies
that version's `pkg_resources` dependency. The [binding documentation](https://github.com/wiseman/py-webrtcvad/tree/2.0.10)
defines supported rates and frame sizes. No new external API/service is involved.

The configured minimum speech frames and end-silence frames are preserved.
With the default two-frame onset, the first speech frame is still replaced
with silence; no audio buffering or replay is introduced. Existing learned
floors survive output gating and belong to their call's session. New calls
start fresh; there is no persistent state migration or database change.

**Limits:** startup still favors hearing the caller, so noise may briefly pass
before the floor adapts and end-silence hysteresis closes the squelch. Speech
classifiers are imperfect: music, background voices and speech-like noise may
still pass, and learned thresholds can suppress quiet callers. Unknown evidence
does not establish non-speech. The provider's native VAD still owns turn detection.
Test realistic noise before rollout; disabling squelch restores continuous input.

All six existing settings and their defaults remain unchanged. Use **Advanced →
VAD → Upstream Squelch**, save, then restart AI Engine after calls drain. The
backend validates/persists the same canonical VAD fields and recommends restart.
No new credentials, Agent settings, wizard steps or provider API changes are needed.
A code rollback requires restoring the prior engine source/image and restarting;
operator configuration and databases do not need conversion.

## Diagnostics

The existing rate-limited DEBUG forwarding event now reports
`squelch_applicable`, `squelch_suppressed` and `squelch_speech_evidence` separately
from TTS gating. Evidence is true/false/unknown, not a new provider VAD event. The
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
continuous-input behavior. Real WebRTC checks add sustained deterministic noise,
tone and white-noise recovery, speech after noise, and a licensed public speech
sample starting mid-utterance. Detector framing covers 5/10/20/30/60 ms packets
at every AudioSocket rate, including fractional resampling, unknown/failure,
rate change and output-gating resets. These are mock-provider checks, not live
provider proof.

For live acceptance, confirm the deployed revision and effective squelch settings:

1. On an affected AudioSocket provider, speak immediately as the greeting ends;
   verify the question is heard and answered. Repeat with an ordinary pause.
2. Continue with a second question, interrupt a response, then end the call
   normally. Verify zero remaining sessions/channels/playbacks. Terminal farewell
   interruption handling is separate provider behavior and is not changed here.
3. Repeat with quiet speech and realistic background noise. Confirm noise does
   not cause unacceptable false turns or interfere with end-of-turn detection.
4. Smoke-test another native-VAD provider, Local and a modular pipeline. Use an
   existing supported RTP configuration for an unchanged-transport comparison.
   Media WebSocket needs its own qualified PBX/version configuration.

A repeatable two-call operator test:

- **Quiet call:** immediately after the greeting, ask “What is the cheapest way
  to run this voice agent?” After the answer, ask in a soft natural voice “Can I
  run it locally?” Expected: both questions are heard and answered, including
  their opening words.
- **Noise call:** start steady fan/air-conditioner noise before dialing and keep
  its volume and phone position steady. After the greeting, remain silent for
  ten seconds. Then ask the same normal-volume cost question while noise
  continues; interrupt the answer with “Actually, compare OpenAI and Gemini.”
  Expected: steady noise does not keep triggering replies, speech is heard,
  interruption works, and the agent finishes a response after speech stops.
  Turn noise off, wait two seconds, then repeat the soft local-running question.
  End normally. Record the call times/provider and any missed words/false turns.

Record provider/model, transport, revision, call window, call IDs and caller
observations. Archive raw evidence privately before analysis. Logging and audio
capture remain user-controlled; a support package or synthetic test does not
replace the caller's listening observations.

The similar noise-floor initialization in the independent no-input detector is
separate follow-up work: changing it affects inactivity/hangup policy on all
transports and requires its own acceptance evidence.
