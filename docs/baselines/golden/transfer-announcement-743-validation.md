# Transfer announcement validation — #743 / #702

## Revision and scope

Live-call source: `a4c003e9f0cd8a41639bc0c72f26b4252802f0d2`, PR
[#744](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/pull/744).
Tests ran on 2026-10-18 UTC using Google Live, AudioSocket, the
`demo_google_live` Agent, optional transfer announcements enabled, and predial
routing. `live_agent_transfer` deferred its commit through `blind_transfer`.
Raw logs, operator configuration, identities and prompt backups remain private.
Only call IDs and revision-pinned lifecycle evidence are published here.

## Live acceptance

| Call ID | Scenario | Observed result |
| --- | --- | --- |
| `1792288459.643` | Filler/tool-call turn, then announcement in a later turn (#743) | PASS: filler completed first, full configured announcement completed later, readiness `reply_completed`, full audio enqueue, drain before one successful transfer commit. |
| `1792288515.654` | Announcement in the originating tool turn (#702 regression) | PASS: readiness `origin_sentence_matched`, one full announcement, full audio enqueue, drain before one successful transfer commit. No repeated announcement or additional assistant turn. |

Times below are UTC on 2026-10-18:

| Event | Delayed call | Originating-turn call |
| --- | --- | --- |
| Tool response sent | 01:54:47.145755 | 01:55:40.584347 |
| Filler turn complete | 01:54:47.164678 | Not present |
| Announcement turn complete | 01:54:48.456983 | 01:55:41.999397 |
| Announcement readiness | 01:54:48.472613 | 01:55:42.014322 |
| Transfer audio drain complete | 01:54:52.009017 | 01:55:44.794314 |
| Single transfer commit | 01:54:52.009761 | 01:55:44.795179 |
| Answered destination bridged | 01:55:02.639324 | 01:55:52.355857 |
| Transfer commit success | 01:55:02.644313 | 01:55:52.375933 |
| Call outcome transferred | 01:55:12.604897 | 01:56:05.210427 |

The announcement segments enqueued all provider bytes (ratio 1.0 in both calls).
Transfer drains took 3.535 and 2.778 seconds, respectively. Neither call relied
on an announcement timeout. Predial ringing may begin while speech is playing;
bridging waits for announcement drain and destination answer. Engine logs had no
error/critical events during this window. Post-call health was healthy, with ARI
connected, AudioSocket listening, and zero remaining calls, sessions, channels,
playbacks, connections or pending timers.

The temporary test instructions were removed and the Agent prompt restored
exactly from its pre-test backup after acceptance. The operator's independently
saved announcement was retained.

## Automated coverage and remaining qualification

The implementation validation covered the CI-selected engine suite (3,113 tests
passed), Admin backend (857 passed), and frontend (440 passed), plus lint,
production build, Python compilation and committed-secret checks before the live
calls. After review fixes, the CI-selected engine suite passed 3,172 tests (14 skipped,
176 deselected), the frontend passed 441 tests, and lint/build passed. The 121
focused handoff regressions include tool-before-audio replies,
completed-call evidence cleanup across all six provider implementations, and
clear/retype timing controls, native Grok execution with private action metadata,
missing-preparation recovery, bounded playback startup/cancellation, exact-playback
retry ownership with bounded terminal recovery, shared legacy provider-list
normalization across all six providers, normalized word-boundary prefix matching
for originating speech, and delivered-result notification integrity. Nine PlaybackManager
tests also pass. CI on
the final PR head is the authority for the
post-review result; these live calls remain pinned to the source revision above.

All six full-agent adapters and modular pipeline/local-TTS paths have automated
handoff coverage. These two calls are live acceptance only for Google Live's two
announcement paths. They do not establish live acceptance for other providers,
announcement-disabled behavior, cancellation, timeout recovery, attended
screening or pipeline/local-TTS delivery. Google did not produce an extra
repeated turn, so forced duplicate suppression is covered by automated tests
rather than independently stressed by these calls. Logs establish enqueue,
drain and transfer ordering, not intelligibility at the caller's handset.
