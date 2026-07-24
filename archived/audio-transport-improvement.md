# Plan

Validate audio quality and transport integrity across the maintainer-approved release matrix while implementing the approved change behind explicit rollback controls. The GA wire contract remains `telephony_ulaw_8k`; provider-native 16/24 kHz processing remains internal, while alternate wideband profiles are an explicitly experimental test lane. Local Full, `hybrid_elevenlabs`, and `telnyx_hybrid` are explicitly untested/non-blocking for this release and must not be represented as validated.

## Progress

- **Diagnostics removed from the release candidate (2026-07-22):** every
  diagnostics-only change introduced during this investigation was reverted to
  `origin/main`. PR #555 does not change diagnostic YAML/environment
  precedence, tap paths, ARI recording behavior, Asterisk module/spool checks,
  installer/preflight behavior, or Admin UI diagnostics controls. Diagnostic
  calls and captures retained below are historical RCA evidence only. The
  post-removal gate passed 1,888 core tests (18 skipped), 484 Admin backend
  tests, and 219 frontend tests; frontend lint/build, Python compilation, shell
  syntax, and whitespace validation also passed.

- **Codex Deepgram full-agent follow-up (2026-07-22):** generic full-agent
  providers now receive the same per-call profile/provider output-resampler
  resolution as providers with native conversion code. When Deepgram is
  configured for PCM output above the 8 kHz wire rate, its existing Engine
  conversion now uses `bandlimited` under `telephony_enhanced_8k`; the
  post-conversion playback metadata records the actual wire rate so the
  streaming manager cannot resample the buffer a second time. Deepgram's GA
  native mu-law 8 kHz contract remains a no-op and is unchanged. The focused
  resampler/provider suites passed 85 tests, and the complete draft core gate
  passed 1,780 tests (6 skipped, 139 deselected).

- **Profile-owned rollout decision (2026-07-22):** the candidate is now exposed
  as the opt-in `telephony_enhanced_8k` Audio Profile rather than being enabled
  provider by provider. It retains the stable 8 kHz telephony contract and
  selects `bandlimited` output downsampling. Existing profiles remain on
  `linear`; assigning an Agent back to `telephony_ulaw_8k` is the rollback.
  Provider and pipeline controls default to `inherit`, with explicit values
  retained only as narrower diagnostic overrides. Resolution is per call so
  Agents using different profiles do not mutate shared state. The Admin UI
  surfaces the enhanced profile and effective downsampling mode. The
  authoritative release result is the 2026-07-22 PR freeze gate recorded below.
  The preceding draft checkpoint (2026-07-22, before the second review-fix
  batch) passed 1,831 core tests with 18 skips, all 481 Admin backend tests, and
  all 218 frontend tests; it is retained as historical evidence rather than the
  release criterion. The frontend lint, production builds, source compilation,
  schema sync, shell syntax, and secret scan gates were also clean. The first
  draft CodeRabbit review was triaged as one
  cohesive batch, including the pipeline greeting cleanup gate, stream cleanup
  ownership/deadline, diagnostic path permissions, Asterisk module/spool
  discovery, and protocol-contract corrections. Exact-head voiprnd deployment
  and the first profile-owned OpenAI/Google live canaries are complete.

- **Profile-owned voiprnd canary and golden calls (2026-07-22):** commit
  `644c061ee11357d33e7435d5f5943633fcb92aad` is deployed through the isolated
  `/root/aava-pr-555` checkout. The AI Engine image is
  `sha256:467414c16a09628731b03ed5e5588f8a7020b2016262c28dae66a58e03c64df9`
  and the Admin UI image is
  `sha256:480c7cef7da79c764dde5412f4df6a1e6ec501e863b68bf43ec7b1f007a3dc52`.
  A reversible Compose override pins AI source mounts to that isolated checkout;
  the dirty production source tree was not overwritten. Provider-specific
  output-resampler environment canaries were removed, so these calls prove
  profile inheritance rather than a hidden provider override. Existing defaults
  remain unchanged and only `demo_openai` and `demo_google_live` were assigned
  `telephony_enhanced_8k` for the canary.
  - Google Live call `1784749299.1139`, archived at
    `logs/archived/rca-20260722-194139`: **PASS**. Every one of seven response
    starts logged `active_mode=bandlimited`, 24→8 kHz, `alias_safe=true`; media
    RX, playback, four local-VAD barge-ins, agent hangup, and the post-call tool
    completed normally. The caller rated the call great. Both the 73.94-second
    MixMonitor recording and 71.04-second ARI bridge recording are archived.
  - OpenAI Realtime call `1784749387.1145`, archived at
    `logs/archived/rca-20260722-194307`: **PASS**. Every one of seven response
    starts logged `active_mode=bandlimited`, 24→8 kHz, `alias_safe=true`; media
    RX, playback, six provider-event barge-ins, agent hangup, and the post-call
    tool completed normally. The caller rated the call great. Both the
    91.20-second MixMonitor recording and 88.28-second ARI bridge recording are
    archived. No call-scoped error or exception occurred in either golden call.
  - Diagnostic follow-up: the legacy PCM tap accumulator reported zero bytes on
    both full-agent paths even though diagnostics were enabled. Complete normal
    and ARI bridge recordings succeeded, so this does not block the audio
    candidate; PCM-tap coverage should be tracked as a separate observability
    issue.

- **Maintainer acceptance decision (finalized 2026-07-22):** Grok, OpenAI,
  Google, Deepgram, and ElevenLabs are accepted as good to go from the completed
  live-call evidence, including the ElevenLabs final lifecycle retest below.
  Existing completed calls will serve as the golden dataset rather than adding
  replacement calls solely for omitted script items. The experimental-profile
  lane is also accepted as already executed. Local Full,
  `hybrid_elevenlabs`, and `telnyx_hybrid` are classified as untested and are
  non-blocking for this release; reported issues on those paths will be
  investigated separately. The historical Local Full delivery failure remains
  archived and must not be relabeled as a pass.

- **Implementation branch:** `codex/audio-transport-improvement`, tracked by
  PR #555 and based on `origin/main` at
  `981612c68c5de49232755e2f2305030bf338f401` in a clean, separate worktree.
  The prior dirty worktree is untouched.
- **Pre-profile voiprnd runtime (historical):** AudioSocket, stream playback, `slin@8000`,
  `telephony_ulaw_8k`, diagnostics enabled, Local AI image
  `sha256:db5494e8fe6df15394668fddf20c3f9d4bf1e30717b20c779f4352f3b608044f`
  and then-current generalized AI-engine candidate image
  `sha256:545924b1d98ef490b44a2f63ac16c6d3171af5e271b77a67b013b5be82eb2f6a`.
  OpenAI, Google, ElevenLabs, and Grok were independently canaried through
  `AAVA_OPENAI_OUTPUT_RESAMPLER=bandlimited` and
  their corresponding provider-scoped output-resampler flags. Those flags were
  removed for the profile-owned deployment described above; this entry is kept
  only as the historical pre-profile freeze point.
- **OpenAI control/proof:** call `1784681097.971`, archived at
  `logs/archived/rca-20260722-004538`; clean 24→8 kHz bandlimited AudioSocket
  call. This proves the originate route and pilot but is not the generalized
  candidate result.
- **Google pre-change control:** call `1784681467.975`, archived at
  `logs/archived/rca-20260722-005227`; PASS on the legacy 24→8 kHz resampler,
  complete two-way conversation, correct transcripts, caller-rated good, zero
  RTP loss, four WAV legs captured.
- **Deepgram attempt:** `1784681707.979`, archived at
  `logs/archived/rca-20260722-005622`; excluded because the outbound SIP call
  was unanswered and never entered AAVA.
- **Deepgram pre-change control:** call `1784682103.980`, archived at
  `logs/archived/rca-20260722-010255`; PASS on native μ-law 8 kHz with no
  resampling, correct sixty-through-sixty-seven sequence, sibilant phrase
  repeated twice, successful barge-in and drain, zero clipping/impairment
  flags, and caller verdict "That was good."
- **ElevenLabs partial attempt:** call `1784682313.984`, archived at
  `logs/archived/rca-20260722-010700`; excluded because the first duration
  guard counted 34 seconds of ringing and forced hangup after only 25 seconds
  in AAVA. It still confirmed correct 16 kHz PCM→8 kHz μ-law negotiation and
  correct recognition of "count from 60 to 67." All future one-minute guards
  start at Stasis/`RCA_CALL_START`.
- **ElevenLabs pre-change control:** call `1784682495.988`, archived at
  `logs/archived/rca-20260722-011003`; PASS on 16 kHz PCM→8 kHz μ-law, exact
  sibilant recognition/repetition, complete sixty-through-sixty-seven output,
  caller verdict "good," matched 39.41/39.44-second provider/caller egress,
  zero clipping/impairment flags, and clean zero-buffer cleanup. Post-change
  ElevenLabs must additionally pass barge-in.
- **Grok pre-change control:** call `1784683065.992`, archived at
  `logs/archived/rca-20260722-011950`; PASS on μ-law 8 kHz input and legacy
  linear PCM 24→8 kHz output conversion. Both sibilant requests were transcribed
  correctly, four barge-ins produced matching provider truncations, all four
  WAV taps were clean and clip-free, and Call History reported
  `codec_alignment_ok=1`. The caller went off the frozen script before digits,
  so the post-change Grok row must explicitly cover numeric accuracy.
- **Local Full pre-change control:** call `1784683487.997`, archived at
  `logs/archived/rca-20260722-012600`; FAIL on an existing response-delivery
  defect. Faster Whisper recognized the sibilant request, Qwen produced a reply,
  and Piper generated 42,075 bytes of native μ-law 8 kHz audio, but the response
  never reached the engine/caller taps. Only the greeting was captured, leaving
  roughly 36 seconds of caller-visible silence. Inbound taps were identical and
  clip-free, so this is not a resampling failure. This historical failure is
  retained, but Local Full is now explicitly untested/non-blocking for this
  release and is not represented as validated.
- **Local Hybrid attempts:** outbound channels `1784685108.1001` and
  `1784685190.1002` rang for 30 seconds and were unanswered. Neither entered
  Stasis or created an AAVA session, so both are excluded and no media verdict
  is assigned.
- **Local Hybrid targeted AudioSocket control:** call `1784685551.1003`,
  archived at `logs/archived/rca-20260722-020055`; conditional transport PASS
  on AudioSocket/stream with detected μ-law 8 kHz, local Faster Whisper STT,
  OpenAI LLM, and native μ-law 8 kHz Piper TTS. Five playback streams completed,
  three barge-ins worked, the farewell drained before hangup, RTP loss was zero,
  and recovered pre/post-compand snapshots were identical and clip-free. It is
  retained as targeted cross-transport evidence only: the route did not exercise
  the planned ExternalMedia/file golden lane, full four-leg taps were inactive,
  no caller subjective verdict was captured, and the transcript does not contain
  the frozen sibilant/numeric script. Later targeted calls plus the maintainer's
  clean-call verdict supersede this incomplete control as the accepted Local
  Hybrid golden dataset.
- **Diagnostics consistency finding and fix:** the Local Hybrid runtime skipped
  ARI recording even though both diagnostic environment switches were true and
  `StreamingPlaybackManager` reported taps active. `StreamingConfig` did not
  declare the diagnostic fields, so Pydantic discarded them before the ARI path
  read `self.config.streaming`; the playback manager separately honored the
  environment switch. The archived base YAML says true and local overlay says
  false, while `apply_diagnostic_defaults()` could also overwrite saved values
  whenever an environment variable was absent. The branch now preserves YAML
  unless an explicit environment override exists, models the five diagnostic
  fields canonically, retains captures from the effective setting, and makes ARI
  recording consume the playback manager's effective decision. The focused
  defaults/config integration gate passes 65/65. Internal streaming snapshots
  from the call remain partial evidence, not the required full four-leg capture.
- **Diagnostics canary deployed:** the narrow model/ARI-consistency patch was
  applied to voiprnd with timestamped source backups and only `ai_engine` was
  rebuilt/recreated while zero calls were active. Image
  `sha256:aa5506a8fe253b471255b6d7e973714b86950aab64008` is healthy; canonical
  `StreamingConfig`, retained capture manager, and playback manager all report
  taps enabled with `/tmp/ai-engine-taps`, and all provider/pipeline health rows
  are ready.
- **Local Hybrid diagnostics retest:** call `1784686483.1007`, archived at
  `logs/archived/rca-20260722-021610`; **FAIL** for caller-visible quality and
  frozen-script acceptance. The caller explicitly reported "the voice is
  breaking up." The count from 60 through 67 was recognized and produced, but
  local STT mangled the sibilant request as "he tells the show by the sea shore,"
  and none of the three required seven-digit sequences was exercised. Native
  μ-law 8 kHz Piper output required no resampling; all pre/post-compand pairs
  were byte-identical, the retained full engine outbound capture was clip-free,
  and Asterisk reported zero RTP loss with max RX jitter near 0.043 ms. The
  leading engine-side hypothesis is sentence-boundary starvation: playback
  warmed on only the 4,551-byte `Sure!` sentence while the 58,607-byte count
  sentence took another 1.49 seconds to synthesize, producing 33 low-buffer
  backoff ticks and one filler underflow. This requires a scoped overlap-vs-
  serial A/B and a post-Asterisk reference before attribution.
- **Asterisk recording prerequisite:** the consistency fix caused ARI recording
  to be attempted, but Asterisk returned HTTP 500 and logged `No such file or
  directory`. `format_wav` was loaded; the host was missing
  `/var/spool/asterisk/recording`. The directory was created after the call as
  `asterisk:asterisk` mode 0770. Full engine caller/agent captures and 26
  diagnostic snapshots were retained, but ARI recording must still be proven on
  the next call. Installer/readiness coverage for the spool prerequisite is now
  an implementation item.
- **Implementation state:** shared stateful 255-tap Blackman FIR primitive and
  explicit output policy are integrated for OpenAI, Google, Grok, and
  ElevenLabs full agents. `linear` remains the compatibility default for old
  profiles and `telephony_enhanced_8k` selects `bandlimited` per call. Provider
  settings inherit the profile by default; explicit provider and environment
  values remain narrower rollback/canary overrides. Google carries filter state
  across chunks and every provider clears output history at response/call
  lifecycle boundaries.
- **Modular implementation state:** the same explicit `output_resampler` policy
  is now propagated through OpenAI, Google, Deepgram, Groq, ElevenLabs, CAMB AI,
  and Azure TTS adapters. Native 8 kHz paths remain no-ops. Full-response TTS
  resets at each synthesized utterance; Azure streaming retains FIR history
  across network chunks and resets it for the next utterance. Pipeline and
  provider settings inherit the per-call profile by default and retain
  progressively narrower explicit rollback overrides.
- **Synthetic DSP/provider gate:** the focused suite passes 140/140 in the
  project AI-engine image, including irregular chunk-boundary equivalence,
  exact 24→8 kHz duration, >60 dB rejection of a 5 kHz alias source, <1 dB
  impact at 3 kHz, compatibility fallback for upsampling, and invalid-policy
  rejection. It also verifies explicit mode propagation and output sizing for
  OpenAI, Google, Grok, and ElevenLabs; policy inheritance and rollback for all
  seven modular TTS adapters; audio-profile contract validation; and load-time
  rejection of invalid provider or pipeline policy values.
- **Modular compatibility gate:** all affected existing adapter suites pass
  56 tests with one credential-dependent skip. A separate policy suite passes
  26/26 checks across all seven modular TTS adapters, including schema rejection
  of unknown modes, provider inheritance, per-pipeline rollback, and explicit
  mode propagation at representative conversion boundaries. The full focused
  DSP/full-agent/modular policy plus Grok lifecycle run passes 79/79.
- **Admin UI implementation and gate:** provider forms now expose a
  profile-inheritance/Compatibility/alias-safe output-downsampling selector with
  the active source and target rates. Audio Profiles identify GA Telephony,
  Enhanced Telephony, Experimental Wideband, and Custom contracts, show the
  effective downsampling mode and Agent usage, and block deletion while in use. The
  dedicated audio-safety component/page tests pass, the complete frontend
  suite passes 218/218, and lint plus the production frontend build pass. Modular UI
  coverage includes OpenAI, Google, Deepgram, Groq, ElevenLabs, CAMB AI, and
  Azure TTS. A provider-native 24 kHz profile with an 8 kHz Asterisk leg is
  explicitly labeled `Provider Native · 8 kHz Wire`, not wideband. In-use
  profiles require a clone/migrate workflow before mutation or deletion. If
  Agent usage cannot be loaded, editing and deletion fail closed rather than
  treating usage as empty.
  The backend canonical config model rejects unsupported fixed encoding/rate
  pairs, missing default-profile targets, and invalid provider or per-pipeline
  resampler policies before persistence/apply. The backend also rejects
  mutation or deletion of a profile referenced by an Agent, so Raw YAML and
  direct API writes cannot bypass the UI's clone/migrate safety rule.
- **Broad automated compatibility gate:** 445 relevant engine/config/provider/
  pipeline tests pass with one credential-dependent skip; all 11 shipped
  `config/ai-agent*.yaml` configurations load successfully. Admin UI backend
  persistence validation tests pass 4/4, including provider-level and
  per-pipeline invalid-policy rejection. Existing frontend warnings are limited
  to pre-existing React `act(...)`, React Router future-flag, browser-data age,
  and bundle-size notices; no test or build failures remain.
- **Full repository gate:** Python bytecode compilation succeeds and the full
  engine suite completes with 1,798 passes and 18 expected skips. Its sole
  container-only failure was the committed-secrets test because the minimal
  runtime image does not contain `git`; running the same guard in the worktree
  passes with no findings. After the fail-closed lifecycle change and live
  retest, the historical pre-ElevenLabs lifecycle run (2026-07-21) passed 1,849
  tests with 7 expected skips; only pytest temporary-directory cleanup warnings
  were emitted. After the scoped ElevenLabs fallback and provider-boundary
  lifecycle fixes, the historical post-ElevenLabs development run (2026-07-22)
  passed 1,826 tests with 18 expected skips and one intentionally deselected
  clean-tree assertion. The
  committed-secrets guard passes independently in the host worktree, and
  `git diff --check` also passes.
- **Historical initial PR freeze gate (2026-07-21):** after synchronizing with
  then-current
  `origin/main`, the exact CI-oriented engine suite passes 1,728 tests with
  17 expected skips and 140 documented deselections; coverage is 44.16% against
  the 26% threshold. The complete Admin backend suite passes 480/480 and the
  complete frontend suite passes 218/218, with lint and production build clean.
  Python compilation, shell syntax, diff whitespace, and the committed-secrets
  guard pass. Fresh AI engine, Admin UI, and Local AI Server production images
  all build successfully from the branch. Existing frontend dependency-age,
  bundle-size, and test-framework warnings are unchanged and non-blocking.
- **Authoritative PR freeze gate (2026-07-22, third review-fix batch):** the
  CI-selected root suite passes 1,777 tests with 6 skips and 139 documented
  deselections. The complete Admin backend suite passes 484/484 and the complete
  frontend suite passes 219/219. Frontend lint remains within the repository's
  existing warning budget with zero errors, the production build succeeds, and
  Python compilation, shell syntax, and diff whitespace checks pass. These are
  the release criteria for the next PR head; the older 1,849, 1,826, 1,831,
  1,728, 482, 481, and 480 results above are explicitly historical scoped runs.
- **Default-profile inheritance guard (2026-07-22):** the final review-fix batch
  treats a `profiles.default` mapping change as a cross-store Agent-impacting
  operation even when no profile body changes. Null or blank Agent assignments
  now block that change with the same fail-closed 409 workflow; explicitly
  assigned Agents do not block a default-only change. Focused and complete Admin
  backend regression gates pass, including both outcomes.
- **Generalized backend canary deployment (2026-07-21 21:10 PDT):** after a
  complete rollback archive at
  `/root/Asterisk-AI-Voice-Agent/backups/audio-transport-src-20260721-2112-complete.tar.gz`,
  all 18 changed engine source files were hash-matched to the tested worktree.
  Only `ai_engine` was rebuilt/recreated. Candidate image
  `sha256:0e696e4d4ed490aea185d536a36c9cd861d776f5236587a3aabbfbe13decea9d`
  is healthy with ARI connected, AudioSocket listening, every provider ready,
  all three pipelines healthy, and zero active calls/sessions/channels/
  playbacks/timers. Compatibility remains the default for providers not
  explicitly canaried.
- **OpenAI generalized-candidate attempt:** call `1784693459.1058`, archived at
  `logs/archived/rca-20260722-041127`; excluded from scoring. It proved the new
  image selected alias-safe bandlimited 24→8 kHz processing and connected
  normally with zero greeting underflows, but caller phrases `Thanks. Boop.`
  and `Thank you.` triggered the configured ending protocol after 10 seconds.
  No frozen sibilant/numeric/long-response rows were spoken. Repeat without
  ending language until the scoring rows are complete.
- **OpenAI generalized-candidate targeted call:** `1784693538.1064`, archived
  at `logs/archived/rca-20260722-041327`; TARGETED PASS for transport/playback,
  not the mandatory OpenAI row. Bandlimited 24→8 kHz was active on every
  response; greeting plus two response streams had exact provider-to-queue byte
  accounting, one terminal 160-byte pacing frame each, no accumulated drift,
  and successful provider-driven barge-in. All four engine legs and a 48.8-
  second bridge WAV were retained, were clip-free, and Asterisk reported zero
  packet loss. OpenAI transcribed `Can you count from 60 to 67?` exactly, but
  the caller omitted all three seven-digit sequences and the long-answer row;
  the sibilant attempt was split into `Can you say Selly?` and `by the
  seashore.` No subjective quality verdict was captured in this call. The
  maintainer later accepted the accumulated OpenAI call evidence as the release
  golden dataset.
- **Google generalized-candidate attempt:** outbound channel
  `1784693818.1070`, archived at `logs/archived/rca-20260722-041738`; excluded
  unanswered attempt. It rang for about 30 seconds and ended before Stasis, so
  no provider/media session or candidate verdict exists. Runtime remained
  healthy and empty afterward. Google bandlimited canary remains active for the
  next answered retry.
- **Google accidental-disconnect attempt:** call `1784695057.1071`, archived at
  `logs/archived/rca-20260722-043810`; excluded after the caller accidentally
  hung up at 15 seconds. It selected bandlimited 24→8 kHz, retained four clean
  engine legs plus a bridge WAV, and left runtime state healthy and empty, but
  only the partial request `about the project` was exercised.
- **Google targeted ordinary-conversation call:** `1784695142.1077`, archived
  at `logs/archived/rca-20260722-043948`; TARGETED PASS for candidate transport,
  exact provider-to-queue accounting, controlled interruption, complete
  farewell, bridge recording, and clean agent hangup. It omitted all frozen
  sibilant/numeric/long-answer rows and had no subjective verdict; later Google
  evidence and the maintainer acceptance supersede this incomplete call.
- **Google generalized-candidate scripted call:** `1784695300.1083`, archived
  at `logs/archived/rca-20260722-044342`; OBJECTIVE PASS for the candidate
  transport, exact sibilant repetition, all three seven-digit sequences with
  zero errors, long-answer barge-in, complete caller-facing farewell, retained
  four engine legs plus bridge WAV, and clean lifecycle. Bandlimited 24→8 kHz
  was active on all seven output segments. Google closed the first number turn
  before the third digits, then captured/repeated `6172049` exactly as a
  follow-up. It transcribed spoken `Stop` poorly but local/provider barge-in
  still stopped the long response. The wrong-length number row was omitted and
  the maintainer reported the call was clean and clear; the maintainer later
  accepted this accumulated evidence without a replacement call.
- **Deepgram generalized-candidate call:** `1784695755.1089`, archived at
  `logs/archived/rca-20260722-045047`; TARGETED PASS for the unchanged native
  8 kHz mu-law path and caller-visible quality. All seven completed provider
  segments had exact provider-to-queue accounting, all four engine legs plus a
  mixed bridge WAV were retained and clip-free, Asterisk reported zero packet
  loss, and six barge-ins were applied. Flux captured `4827316` twice and
  `9051842` exactly, and later transcribed “Sally sells sea shells by the
  seashore” exactly. The project-demo prompt refused to repeat those phrases,
  and a combined second/third-number turn was committed partway through the
  third sequence. The maintainer reported that the call was good overall and
  characterized the behavior as restrictive rather than an audio problem. This
  is sufficient to pass Deepgram transport/no-regression without changing the
  production prompt merely to satisfy the diagnostic script.
- **Deepgram prompt-authority experiment:** call `1784696013.1095`, archived at
  `logs/archived/rca-20260722-045430`; excluded from audio scoring. A temporary
  diagnostic clause added to legacy YAML did not appear in the active call
  because the Admin UI Agent record is authoritative for `demo_deepgram`.
  Original YAML was restored and reloaded; health returned clean and empty with
  the original config hash `257c09180ca5fc96`. No live Agent-store mutation is
  needed because the preceding call already established the audio result.
- **ElevenLabs Local-channel harness attempt:** outbound attempt beginning with
  `1784696460.1101`, archived at `logs/archived/rca-20260722-050520`; excluded.
  A Local channel rather than the real SIP trunk channel entered Stasis after
  answer, so AAVA failed closed with `No caller found for Local channel` before
  `RCA_CALL_START`. Subsequent calls use direct
  `SIP/callcentric_jugaar/13164619284` origination.
- **ElevenLabs generalized-candidate failure:** call `1784696708.1106`, archived
  at `logs/archived/rca-20260722-050815`; FAIL for barge-in. Bandlimited 16→8
  kHz ran on every response, all eight segments had exact byte accounting, and
  ElevenLabs recognized/repeated the sibilant phrase plus all three separately
  dictated seven-digit sequences exactly. During the long response, however,
  repeated “Stop” utterances were present in both the caller capture and the
  exact 16 kHz provider-input capture but ElevenLabs emitted neither a
  transcript nor interruption. AAVA applied no local fallback because
  `elevenlabs_agent` was absent from the fallback allowlist. The maintainer
  confirmed that barge-in did not work. Six of roughly 640,000 agent samples
  reached full scale; no audible distortion was reported, but the observation
  remains recorded rather than calling this capture clip-free.
- **ElevenLabs scoped barge-in fallback:** official ElevenLabs documentation
  confirms interruption handling is Agent-dashboard controlled under Advanced
  client events. The candidate now includes `elevenlabs_agent` in the existing
  caller-isolated provider fallback. It is active only while playback and
  inbound media are confirmed; it does not alter another provider's VAD policy.
  Focused provider/transport/DSP tests pass 84/84 after this change.
- **ElevenLabs fallback retest:** call `1784697431.1112`, archived at
  `logs/archived/rca-20260722-051800`; barge-in PASS but terminal-audio FAIL.
  Local fallback interrupted the greeting, and the later ElevenLabs provider
  event stopped the long response and discarded 85,734 buffered bytes. The
  generic 1.2-second output-suppression window then dropped the provider's
  immediate 10,892-byte “Goodbye” segment. Suppression is now resolved to zero
  after an authoritative `provider_event` interruption while remaining active
  for local fallback, where the provider may continue the old response. The
  focused gate passes 85/85.
- **ElevenLabs final lifecycle retest:** call `1784697651.1118`, archived at
  `logs/archived/rca-20260722-052145`; OBJECTIVE PASS. ElevenLabs interruption
  stopped the response and discarded 78,834 buffered bytes. No suppression was
  applied after the provider boundary; the complete 56,884-byte farewell was
  queued and drained before agent hangup. All three segments had exact byte
  accounting, the four engine legs and bridge WAV were retained, no capture
  reached full scale, and post-call state was healthy and empty. The maintainer
  confirmed the final call was clean and clear, completing the ElevenLabs row.
- **Grok generalized-candidate failure:** call `1784698218.1124`, archived at
  `logs/archived/rca-20260722-053300`; FAIL for caller-visible barge-in and the
  frozen numeric script. Bandlimited 24→8 kHz was active on all seven response
  segments with exact provider-to-queue accounting and unclipped captures, but
  the maintainer found interruption inefficient. Grok received and transcribed
  both repeated stop turns; its native speech-start event remained the effective
  trigger because the conservative 250 ms hosted-provider local fallback did
  not win on the short command. AAVA then spent about 418 ms completing two
  provider-tail grace waits that are unnecessary after an explicit barge-in.
  Grok also omitted the first 7-digit sequence until corrected. The next canary
  will skip tail grace only for interrupted streams and use a scoped Grok
  fallback reaction threshold; global provider/pipeline timing remains frozen.
- **Grok scoped-barge canary:** call `1784699398.1132`, archived at
  `logs/archived/rca-20260722-055202`; BARGE-IN PASS. Caller-isolated local
  fallback stopped the
  active response about 1.68 seconds before Grok's native speech-start event,
  and interrupted cleanup completed in roughly 10 ms instead of about 418 ms.
  Bandlimited 24→8 kHz remained active, naturally completed segments retained
  exact byte accounting, all WAVs were unclipped, the farewell completed, and
  post-call state was empty. The maintainer confirmed interruption worked well.
  Numeric recognition was 2/3 on first pass:
  `4827316` and `3178649` were correct, while `9061542` was correct only after
  one correction. The maintainer accepted Grok without a replacement numeric
  call based on the complete transport, barge-in, lifecycle, and subjective
  evidence.

## Scope
- In: archive-first live calls for each documented golden provider/pipeline combination; targeted cross-transport tests; caller-to-provider and provider-to-caller audio; codec, resampling, pacing, VAD/barge-in, playback, diagnostics, audio-profile resolution, Admin UI/backend configuration safety, documentation, rollback, and final architecture approval.
- Out: a Cartesian test of unsupported transport/playback combinations; promoting 16/24 kHz wire profiles to GA; silently changing global VAD or resampling behavior from a single-provider result; representing explicitly untested integrations as validated; and the Call History transcript race tracked separately in [GitHub issue #554](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues/554).

## Action items
- [x] **Freeze a reproducible test baseline before the next call.**
  - Record the deployed Git SHA/image IDs, complete effective configuration, Agent-to-provider/pipeline assignments, environment overrides, Asterisk version/modules, trunk codec, endpoint codec, and post-call health.
  - Back up `.env`, `config/ai-agent*.yaml`, and the Agent store before changing test routing.
  - Confirm the issue #553 OpenAI bandlimited patch state and flag value explicitly; do not allow an unrecorded mixed build.
  - Keep `telephony_ulaw_8k`, AudioSocket `slin@8000`, and streaming `8000` as the GA control unless a documented golden pipeline requires ExternalMedia/file playback.
  - Change only the selected Agent/provider/pipeline between baseline calls. Never apply or restart during an active call, and restore the frozen baseline after each experimental lane.

- [x] **Create the authoritative audio-contract inventory and reconcile current configuration drift.**
  - For every shipped integration, record: full agent versus modular stage, supported transport/playback combination, caller/trunk codec, Asterisk wire encoding/rate, engine working rate, provider input/output encoding/rate, target encoding/rate, conversion owner, and current golden-config source.
  - Start with the established release matrix: Google Live, OpenAI Realtime, Deepgram Voice Agent, ElevenLabs Agent, Grok, Local full agent, `local_hybrid`, `hybrid_elevenlabs`, and `telnyx_hybrid`.
  - Inventory newer or optional integrations such as CambAI, Azure STT/TTS, MiniMax, OpenAI modular STT/TTS, Groq stages, and other enabled pipeline permutations. Each must be classified as release-supported, experimental, unavailable for credentials/runtime reasons, or unsupported; no integration may be silently skipped.
  - Reconcile the misleading current golden selections `openai_realtime_24k` and `grok_24k`: provider-native 24 kHz is valid internally, but the GA telephony wire leg must resolve to the approved 8 kHz contract.
  - Identify duplicated conversion logic across `src/engine.py`, `src/audio/resampler.py`, full-agent adapters, pipeline adapters, and playback managers without changing it yet.

- [x] **Freeze the mandatory and targeted test matrix before execution.**
  - Mandatory golden lane:

    | Integration | Mode | Golden transport | Playback | GA wire profile |
    | --- | --- | --- | --- | --- |
    | Google Live | Full agent | AudioSocket | Stream | `telephony_ulaw_8k` |
    | OpenAI Realtime | Full agent | AudioSocket | Stream | `telephony_ulaw_8k` |
    | Deepgram Voice Agent | Full agent | AudioSocket | Stream | `telephony_ulaw_8k` |
    | ElevenLabs Agent | Full agent | AudioSocket | Stream | `telephony_ulaw_8k` |
    | Grok | Full agent | AudioSocket | Stream | `telephony_ulaw_8k` |
    | Local full agent | Full agent | AudioSocket | Stream | `telephony_ulaw_8k` |
    | `local_hybrid` | Pipeline | AudioSocket | Stream | `telephony_ulaw_8k` |
    | `hybrid_elevenlabs` | Pipeline | ExternalMedia | File | `telephony_ulaw_8k` |
    | `telnyx_hybrid` | Pipeline | ExternalMedia | File | `telephony_ulaw_8k` |

  - **Current release-validation scope:** Google Live, OpenAI Realtime,
    Deepgram Voice Agent, ElevenLabs Agent, Grok, and the tested Local Hybrid
    AudioSocket/stream path are accepted from the existing golden-call dataset.
    Local Full, `hybrid_elevenlabs`, and `telnyx_hybrid` remain available
    configurations but are explicitly labeled **untested** and do not block
    this release.
  - Correct any row during inventory if the current published golden baseline says otherwise, and record the source and rationale.
  - The original targeted cross-transport planning lane is historical. The
    accepted Local Hybrid evidence uses AudioSocket/stream; OpenAI native-24 kHz,
    Deepgram 8 kHz, and `hybrid_elevenlabs` alternate paths retain their recorded
    tested or untested classifications above.
  - Experimental profile lane runs only after all GA rows pass: one verified wideband-capable trunk, Asterisk channel, endpoint, and provider path. A provider-native 24 kHz API alone does not qualify the telephony path as wideband.

- [x] **Use one deterministic call script and freeze acceptance criteria before candidate calls.**
  - Exercise both directions with: sustained sibilants, ordinary conversation, three seven-digit sequences with varied cadence, one deliberately wrong-length sequence, silence, controlled barge-in, a long reply, and a complete farewell/hangup.
  - Run a control call on the frozen baseline first; derive and publish objective tolerances before comparing candidate behavior.
  - Require exact numeric capture/readback for the scripted sequences, no invented or reordered digits, intelligible caller input, no audible hiss/crackle/warble, clean barge-in, complete terminal speech, and maintainer subjective quality of at least “no regression.”
  - Require exact expected sample-duration conversion within one frame, no unexplained input/output frame loss, provider-to-enqueued ratio `1.000` for uncancelled output, no format-alignment errors, no clipping introduced by conversion, drift and underflow within the existing RCA baseline, and clean post-call lifecycle state.
  - Measure level, clipping, DC offset, discontinuities, spectral/alias energy, high-frequency energy near the 8 kHz Nyquist boundary, stream drift, underflows, latency, and transcript accuracy. Treat subjective listening and objective metrics as complementary gates.

- [x] **Verify diagnostics through the Admin UI before every test batch.**
  - Confirm four per-leg taps are active and writable: caller inbound, caller sent to provider, agent received from provider, and agent sent to caller; also retain the Asterisk MixMonitor recording.
  - From **Advanced Settings → Streaming**, enable taps, save, apply/restart as instructed, and verify effective runtime status rather than trusting the saved checkbox.
  - Require the UI/backend to report whether taps are active, the effective output directory, whether a restart is pending, the running configuration generation, and a visible failure reason when Apply Changes fails.
  - Capture Admin UI logs for save/apply failures and verify rollback leaves the previous runtime configuration active.
  - Archive raw engine/Admin/Asterisk logs, effective config, health, Call History JSON, deterministic RCA JSON, all WAV legs, and subjective notes under a unique `logs/archived/rca-YYYYMMDD-HHMMSS` directory for every call.
  - **Live verification correction:** call `1784685551.1003` had contradictory
    effective states: the playback manager was enabled by environment while the
    ARI path lost the undeclared Pydantic field and skipped recording. This item
    is complete as an investigation, but the next scored call batch is blocked
    until the consistency fix is deployed and the expected legs are confirmed
    after restart.
  - **Retest result:** the patch aligned runtime state and preserved both full
    engine capture legs, but ARI recording exposed a missing host spool
    directory. That directory now exists. Subsequent calls, including
    `1784695142.1077` and `1784695300.1083`, successfully created and archived
    mixed bridge WAVs alongside all four engine legs, completing the live
    artifact prerequisite for the current AudioSocket lane.

### Supporting implementation and live-validation evidence

- **Local Hybrid pacing control (completed):** optional
  `pipelines.<name>.options.tts.streaming_overlap`. When absent it inherits the
  existing global setting, preserving every current pipeline; explicit `false`
  selects complete-response/serial playback only for the named pipeline. The
  backend rejects non-boolean values and the Admin UI exposes Inherit/Enabled/
  Disabled with a latency-versus-continuity explanation. The control was
  validated in focused and broad automated gates and exercised in the serial
  A/B call below. It remains a scoped diagnostic/rollback control rather than a
  global audio-quality default.
- **Local Hybrid serial A/B:** call `1784687573.1011`, archived at
  `logs/archived/rca-20260722-023405`; FAIL for the frozen script. The explicit
  pipeline override resolved false on every turn and removed the overlap call's
  starvation signature (zero low-buffer bursts and zero underflows), but local
  Faster Whisper fragmented/misrecognized the caller and prevented all required
  phrases from being scored. This proves the control works and narrows the
  outbound pacing hypothesis; it does not justify changing the default. The
  next Local Hybrid test must first isolate inbound STT segmentation/accuracy.
  Offline OpenAI STT subsequently transcribed all three caller-only attempts
  exactly (`Count from 60 to 67`, with `No, you` on the third), proving the
  caller/trunk capture is intelligible. The blocker is Local Faster-Whisper's
  segmentation/decode path plus eager pipeline dispatch. Test the existing
  Local STT silence-window override before changing any shared VAD/default.
- **Scoped Local STT candidate:** the Local adapter protocol now accepts
  optional per-pipeline `segment_energy_threshold` and `segment_silence_ms`,
  inheriting server defaults when absent. The server validates bounded integers,
  stores policy per WebSocket/call, and reports it in `mode_ready`; no global VAD
  setting changes. A confirmed segmenter defect that duplicated the first 160 ms
  voice frame via preroll is fixed. Backend/config/protocol/adapter tests pass in
  split resource-safe runs (34 local server passes + 3 expected skips, 23 local
  adapter/policy passes), Admin config tests pass 6/6, UI policy tests pass 10/10,
  and the frontend build passes. Live canary remains required before any default
  or support claim.
- **Scoped Local STT canary deployment (2026-07-21 20:19 PDT):** rebuilt and
  recreated only `local_ai_server` and `ai_engine` on voiprnd. Running image
  IDs are `db5494e8fe6d` and `a022884ac37a`; both have zero restarts, Local AI
  is healthy, ARI/AudioSocket are healthy, and all three Local-STT pipelines
  revalidated without warnings. Effective `local_hybrid` policy reports
  `segment_silence_ms=1400` and `streaming_overlap=false`. Startup connection
  refusals were a transient ordering race while Local AI loaded its models;
  the engine's retry revalidated every pipeline once the WebSocket was ready.
- **Archived-audio replay gate:** replayed caller-only WAVs from failed call
  `1784687573.1011` through the live Local STT WebSocket at real-time 160 ms
  pacing. With the old energy threshold `1200`, the first utterance remained
  wrong (`Going from 60 to 60`) despite becoming one segment, proving silence
  duration alone is insufficient. Frame RMS analysis showed much of valid
  telephony speech below `1200` while the captured silence floor was `6`.
  With session-scoped threshold `300` plus silence `1400`, all three utterances
  became complete single finals: `count from 60 to 67`, `Come from 60 to 67`,
  and `No, you count from 60 to 67`; all numeric content was exact and the one
  residual `Come`/`count` substitution is a `tiny.en` model limitation, not a
  transport or segmentation loss. Direct decoder comparison found beam 5 and
  the larger `base.en` model did not consistently correct that consonant, while
  Faster-Whisper hotwords did. Do not globally add test-specific hotwords or
  raise the Local resource baseline as part of this audio-transport change.
  Candidate next gate: apply `segment_energy_threshold=300` only to
  `local_hybrid`, repeat the deterministic live call, and retain the current
  server default as instant rollback.
- **Local Hybrid scoped-policy live result:** call `1784690988.1016`, archived
  at `logs/archived/rca-20260722-033113`; TARGETED PASS. The runtime resolved
  energy/silence `300/1400` and serial playback from pipeline policy. All four
  caller turns produced one complete Local STT final, including exact capture
  and correct execution of `count from 60 to 67`; the former fragmented-final
  failure did not recur. `tiny.en` still substituted sibilant words (`sells` →
  `cells`, `seashells` → `C-shells`), while an independent OpenAI transcription
  of the same caller-only WAV was exact, so this residual belongs to Local model
  accuracy rather than transport. All four completed outbound utterances had
  exact padded byte accounting, zero underflows, zero low-buffer waits, and no
  sample-rate conversion; representative pre/post-compand taps were byte-
  identical. The maintainer reported the call was fine and noticed no issue,
  satisfying the targeted subjective continuity/no-hiss gate. Bridge recording
  also passed and produced a 69.16-second WAV alongside both full legs and all
  taps. At the time this did not close the original full-script row because the
  caller omitted several items and the harness guard cut off terminal playback.
  The maintainer later accepted the accumulated Local Hybrid calls as the
  golden dataset for the actually tested AudioSocket/stream path.
- **Local Hybrid focused numeric attempt:** call `1784691339.1022`, archived at
  `logs/archived/rca-20260722-033654`; excluded from numeric scoring because the
  requested seven-digit sequences were not spoken. It did produce a useful
  targeted barge-in PASS: one TalkDetect interruption stopped active playback,
  sent 46,720 PCM16 bytes, discarded the exact remaining 73,282 bytes from the
  120,002-byte decoded response, cleared gating, and resumed caller capture with
  zero underflow. All caller turns again remained single complete STT finals,
  but independent OpenAI transcription confirms residual `tiny.en` errors such
  as `60 to 67` → `60 salmon`; transport capture was intact. Bridge WAV/full
  legs/taps were retained. Repeat the unspoken numeric rows later. This log batch
  also exposed a prior-call farewell stream timing out several minutes after
  hangup; investigate that existing post-hangup playback lifecycle separately
  before final freeze.
- **Post-hangup playback lifecycle fix:** root cause confirmed in cleanup order.
  Cleanup stopped the currently active stream first, but did not cancel/await
  the pipeline dialog runner until after provider shutdown, bridge destruction,
  media-channel teardown, and AudioSocket disconnect. An in-flight cloud LLM
  request could therefore finish inside that window and create a brand-new
  stream after the one stop call, which then lived until keepalive timeout.
  Cleanup now cancels and awaits the pipeline producer before stopping playback
  or tearing down media, then stops any stream that producer had already made.
  TALK_DETECT is also removed while the caller channel still exists. A
  deterministic regression releases a blocked LLM specifically during bridge
  teardown and proves TTS/playback never starts; pipeline/cleanup/session policy
  suites passed 39/39. The first live forced-hangup attempt
  `1784691920.1028` did not contain caller speech, so it could not exercise an
  in-flight LLM, but it exposed a duplicate greeting created during cleanup.
  The valid reproduction `1784692192.1040`, archived at
  `logs/archived/rca-20260722-035044`, then proved cancellation/order alone is
  insufficient: OpenAI completed 1.816 seconds after cleanup began and a new
  Local-TTS response stream was created during cleanup. Cleanup stopped it with
  exactly zero provider/queued/transmitted bytes before bridge destruction, so
  no caller audio leaked, but late work was still instantiated. Root cause was
  that `_cleanup_call()` acquired the global guard without setting the existing
  `CallSession.cleanup_in_progress` marker, leaving cancellation-resistant
  adapter results without a durable output-boundary check. The candidate now
  sets that marker before the first cleanup await, suppresses runner startup,
  and fail-closes runner/greeting/turn/post-LLM/pre-output boundaries. A new
  cancellation-resistant LLM regression returns only after cleanup ownership
  and proves no history mutation, TTS, or stream creation; focused lifecycle,
  session, overlap-policy, and resampler suites pass 64/64. The live forced-
  hangup retest `1784692948.1052`, archived at
  `logs/archived/rca-20260722-040307`, **PASSed**: Local STT captured
  `Explain the project in detail.` exactly, OpenAI returned 1.879 seconds after
  cleanup began, and the new `post-llm` guard suppressed the result. No TTS
  request or playback stream started after cleanup, the mixed 8 kHz bridge WAV
  was retained, and post-call state was clean. This closes the lifecycle
  blocker; provider scoring may resume.
- **ARI recording retest:** the spool prerequisite is now healthy and the ARI
  request was attempted, but Asterisk rejected recording because the
  AudioSocket channel was already in the bridge (`Cannot record channel while
  in bridge`). Full engine captures and all compand taps were retained. The
  diagnostics fix must record the bridge or start channel recording before
  bridge membership, verify artifact creation, and report actual failure rather
  than optimistic "started" status.
- **Diagnostics experiment excluded from candidate:** the mixed-bridge
  recording and Asterisk module/spool readiness work explored during RCA was
  removed from PR #555. It remains historical evidence and requires an
  independently scoped design and review before any future release.
- **OpenAI diagnostics verification attempt:** SIP channel `1784688120.1015`,
  archived at `logs/archived/rca-20260722-024159`; excluded unanswered attempt.
  It was destroyed pre-Stasis after 30 seconds and produced no AAVA media or
  recording evidence. Repeat when the call can be answered.

- [x] **Execute and analyze test calls in controlled batches.**
  - Run full-agent golden calls first, then pipeline golden calls, then targeted cross-transport calls, and only then the experimental profile lane.
  - Stop a batch on the first unexplained regression; archive the call before changing any setting, compare it with the preceding control, and repeat only after documenting the hypothesis.
  - For each call, trace setup, effective profile resolution, provider negotiation, every conversion boundary, VAD/commit/cancel events, playback pacing, barge-in, hangup/drain, post-call hooks, and cleanup.
  - Publish one evidence row containing call ID, revision, effective contract, tap inventory, metrics, subjective verdict, transcript verdict, lifecycle verdict, and archive link. A row is `PASS` only when both the caller-visible result and objective RCA pass.
  - The maintainer accepted the accumulated OpenAI, Google, Deepgram,
    ElevenLabs, Grok, and Local Hybrid call archives as the golden dataset.
    Local Full, `hybrid_elevenlabs`, and `telnyx_hybrid` are explicitly
    untested/non-blocking, not assumed passes. The experimental-profile lane is
    accepted as already executed.
  - The Local Hybrid pacing, Local STT segmentation, diagnostics recording, and
    post-hangup lifecycle blockers have each been isolated, implemented behind
    scoped rollback controls, and live-tested. Candidate provider scoring has
    resumed. Google has an objective and subjective PASS; its omitted
    wrong-length row was waived by maintainer acceptance. Deepgram has a
    targeted objective/subjective transport PASS; its demo prompt prevented
    full script compliance, which is recorded as a behavior limitation rather
    than an audio failure and was accepted without a replacement call. ElevenLabs
    has an objective resampling/barge-in/farewell PASS after its scoped fallback
    and provider-boundary fixes; the maintainer confirmed the final call was
    clean and clear. Grok has objective and subjective resampling/barge-in
    passes. The maintainer accepted Grok, OpenAI, Google, and Deepgram from the
    existing call set and waived replacement calls for omitted script items.
    Local Hybrid's existing targeted transport, continuity, barge-in, and
    lifecycle calls are accepted as its golden dataset based on the maintainer's
    clean-call verdict. Local Full, `hybrid_elevenlabs`, and `telnyx_hybrid`
    are explicitly untested/non-blocking and will be investigated if users
    report issues. The live-call matrix is closed at this approved scope.

- [x] **Classify findings by layer before selecting a fix location.**
  - Separate source/trunk limitations, Asterisk transport format, profile resolution, inbound conversion, provider negotiation, provider-native synthesis, outbound anti-aliasing/resampling, encoding, pacing/playback, VAD/cancellation, and observability defects.
  - Compare identical source material at each tap to locate where noise, digit loss, clipping, drift, or spectral artifacts first appear.
  - Prefer a provider-local policy when the defect is unique to one provider contract; prefer a shared primitive only when multiple providers/pipelines reproduce the same failure at the same engine boundary.
  - Do not infer that a clean output path proves clean input recognition, or that an OpenAI-specific result applies to Deepgram, Google, ElevenLabs, Grok, local, or modular pipelines.
  - Record unrelated findings as separate issues so they cannot expand or obscure the audio-transport change; issue #554 remains independent.

- [x] **Finalize and implement the approved backend architecture with evidence-gated activation.**
  - Keep one explicit GA transport contract: Asterisk-facing telephony audio is 8 kHz unless an end-to-end wideband path is deliberately selected and validated.
  - Model provider-native input/output capabilities separately from the transport profile; a profile must not imply that Asterisk sends the provider’s native rate.
  - Prefer shared, stateful, chunk-safe codec/resampling primitives with provider adapters declaring native capabilities and selecting policy explicitly. Preserve provider-specific handling where protocols or cancellation semantics differ.
  - If bandlimited downsampling is broadly justified, introduce it behind per-provider/configurable policy with the current compatible behavior as rollback; do not silently switch every provider.
  - Define state ownership and reset rules for call start, response start/end, cancellation, barge-in, error, reconnect, fallback, hangup, and provider restart so resampler history cannot leak across segments or calls.
  - Add synthetic golden-vector, chunk-boundary, amplitude, alias-rejection, cancellation, and byte-accounting tests before any live candidate deployment.
  - Add a pipeline-scoped overlap/serial TTS policy before the Local Hybrid A/B;
    do not toggle the global overlap setting because it would alter every
    modular pipeline and confound provider comparisons.
  - **Implemented, focused validation passed:** runtime resolution reports whether the
    effective decision came from the global default or the pipeline override;
    provider swaps preserve this portable playback policy and the independent
    output-resampler policy.

- [x] **Design the Admin UI and backend configuration changes as part of the same architecture.**
  - Replace ambiguous profile presentation with **GA Telephony 8 kHz** and clearly badged **Experimental Wideband** presets; retain an expert custom mode but validate its encoding/rate pairs.
  - Show an effective-path preview before save: caller/trunk → Asterisk wire → engine → provider input → provider output → engine target → caller, including every encoding, sample rate, and resampling boundary.
  - Distinguish provider-native rates from transport rates and explain why OpenAI/Grok can use 24 kHz internally while the GA wire leg remains 8 kHz.
  - Validate impossible or unsupported combinations in both frontend and backend, identify the affected Agents/providers/pipelines, and fail closed without partially updating runtime state.
  - Update Audio Profiles from the retired “Used By Contexts” model to “Used By Agents,” correct transport-specific tooltips, display golden/experimental/support status, and prevent deleting or mutating an in-use GA baseline without explicit migration.
  - Make Save versus Apply/Restart state unambiguous; show active-call protection, pending generation, runtime-effective values, health confirmation, and a recoverable rollback action when reload/restart fails.
  - Diagnostic UI, recording, and readiness changes are outside this release
    candidate and must be designed, reviewed, and tracked independently.
  - Cover profile round trips, compatibility validation, active-Agent impact, restart classification, failed-apply rollback, accessibility, frontend tests, backend API tests, production build, and browser console/API checks.

- [x] **Close the implementation validation gate and prepare the review phase.**
  - Update this document with every call ID, archive, finding, threshold, exception, and final architecture decision; link any newly filed issues.
  - Require every maintainer-approved release-validation row to pass on one
    frozen revision. Explicitly untested rows remain labeled untested and are
    not assumed passes.
  - Maintainer authorized implementation on a dedicated branch while live validation continues; major architectural deviations still require explicit approval.
  - Implement as one coherent vertical slice with unit/integration/UI tests and
    deploy it to voiprnd as a reversible canary; the approved live-call matrix
    is complete from the retained golden dataset.
  - The automated regression/freeze gate passes and the implementation is ready
    for draft-PR review. Production shipping remains a separate decision after
    the repository's draft-review and final-freeze workflow completes.

## Open questions
- None. The maintainer fixed the release-validation scope: OpenAI, Google,
  Deepgram, ElevenLabs, Grok, and the tested Local Hybrid AudioSocket/stream
  path use the retained golden dataset; Local Full, `hybrid_elevenlabs`, and
  `telnyx_hybrid` are explicitly untested/non-blocking. `telephony_ulaw_8k`
  remains the GA wire baseline and alternate profiles remain experimental.

## Opt-in 16 kHz AudioSocket expansion (2026-07-24)

- [x] Prove Asterisk 20.17+ multirate AudioSocket framing on a direct G.722
  extension with `wideband_pcm_16k`; call `1784929286.8` was objectively
  `slin16@16000` and subjectively a clear improvement.
- [x] Preserve every existing 8 kHz profile and provider default. Wideband
  selection is per Agent/per call and remains immediately reversible.
- [x] Declare provider-native wideband boundaries independently of the
  Asterisk wire contract: ElevenLabs/Deepgram/Grok 16 kHz, Google 16 kHz input
  and 24 kHz output, OpenAI 24 kHz input/output, and Local as partial 16 kHz
  input with legacy 8 kHz output.
- [x] Extend supported modular TTS adapters to produce native PCM for a 16 kHz
  call. OpenAI, Google, Deepgram, ElevenLabs, Groq, Azure, and CAMB AI opt in;
  Local TTS stays explicitly partial because its current websocket protocol
  has no output-format negotiation.
- [x] Add focused compatibility and per-call isolation coverage; the initial
  transport/pipeline/provider-adapter suite passes 111/111. Google LINEAR16
  and MULAW WAV containers are stripped before AudioSocket playback, and
  Deepgram REST TTS explicitly requests `container=none` as recommended for
  raw VoIP audio.
- [ ] Deploy one frozen revision to voiprnd and execute direct-extension calls
  for every configured/credentialed full-agent provider and modular pipeline.
  Archive each call before analysis and retain objective and subjective results.
  - Revision `49f60b09` is deployed and healthy on voiprnd.
  - ElevenLabs full-agent call `1784932129.24`, archived at
    `logs/archived/rca-20260724-223012`, objectively passed the direct G.722 to
    `slin16@16000` to provider PCM16/16 kHz path in both directions with no
    resampling, zero RTP loss, functional barge-in, and clean teardown. The one
    spoken seven-digit sequence was transcribed exactly but the demo agent
    declined to repeat it; the sibilance phrase was transcribed and repeated
    exactly. Subjective caller verdict is pending.
  - Google Live call `1784932604.28`, archived at
    `logs/archived/rca-20260724-223726`, passed objectively and subjectively.
    G.722/16 kHz caller audio reached Google as linear PCM16/16 kHz without
    input resampling; Google native 24 kHz PCM was converted once by the
    profile-selected linear resampler to `slin16@16000`. The seven-digit and
    sibilance rows were transcribed and repeated exactly, barge-in worked, RTP
    loss was zero, teardown was clean, and the maintainer described the voice
    quality as awesome/great. The conversion reports `alias_safe=false`, which
    is retained as an explicit property of the compatibility linear policy.
  - OpenAI Realtime call `1784932927.32`, archived at
    `logs/archived/rca-20260724-224324`, passed the G.722 → 16 kHz AudioSocket →
    OpenAI 24 kHz input and OpenAI 24→16 kHz output paths. Digits and the
    sibilance phrase were exact, RTP loss was zero, normal-turn audio sounded
    great, and teardown was clean. The row remains **PARTIAL** because startup
    echo was admitted after provider generation ended but before the greeting
    transport drained: OpenAI transcribed loopback fragments `you` and `Bye.`
    and generated two unintended responses. The narrow correction will defer
    only OpenAI greeting gating release through the existing caller-facing
    drain observer; normal responses and other providers remain unchanged.
- [x] Run the full backend/frontend regression gates. Backend passed 1,981
  tests with 18 skips; frontend passed 221 tests, production build, and lint
  with existing warnings only. Focused wideband/provider/pipeline coverage
  passed 111 tests with one skip; compile, secret, release-doc, and diff checks
  passed.
- [ ] Complete the live validation matrix with pass/partial/untested
  classifications and prepare the branch for the repository PR workflow.

Release guidance: `wideband_pcm_16k` is opt-in. Recommend it only when the SIP
endpoint or trunk provider supports and actually negotiates G.722 (or another
true wideband codec). Keep PSTN/G.711 routes on an 8 kHz profile.
