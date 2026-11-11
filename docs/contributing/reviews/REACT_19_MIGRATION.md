# Admin UI React 19 migration

Scope: PR #750 and dependency tracker #220. React and React DOM must move
as a pair; React DOM types also require the matching React type major.
No configuration schema, backend, telephony, or provider behavior changes.

## Compatibility decisions

Checked against the [official React 19 upgrade guide](https://react.dev/blog/2024/04/25/react-19-upgrade-guide)
and npm package peer declarations on 2026-11-11:

| Package | Selected version | Reason |
| --- | --- | --- |
| react / react-dom | 19.3.1 | React DOM requires React `^19.3.1` |
| @types/react / @types/react-dom | 19.3.0 | DOM types require React types `^19.3.0` |
| lucide-react | 0.475.0 | Previous 0.330.0 excludes React 19; this release explicitly includes it |
| @monaco-editor/react | Existing 4.7.0 | Already accepts React and React DOM 19 |
| @testing-library/react | Existing 16.3.3 | Already accepts React 19 and both type packages |
| react-router-dom | Existing 7.18.5 | React/DOM peers are >=18 |
| Radix, Sonner, react-markdown | Existing locked versions | Peer declarations accept React 19 |

Only the React family, scheduler, and Lucide versions change. Retain Node 22,
Jest DOM 7, ESLint 8/React Refresh 0.4, security overrides, and unrelated locked
versions. Do not bypass peer resolution with force or legacy-peer flags.

The app already uses `createRoot`, StrictMode, and the modern `react-jsx`
transform. Authentication's child type now uses `React.JSX.Element`, since React
19 removes the global JSX namespace. The tool editor focus fallback explicitly
accepts a nullable ref, matching React 19 useRef types and its existing optional
focus call. Existing effect cleanup, portal dialogs,
Router navigation, and async action tests must continue to pass.

## Acceptance

Use a clean Node 22 install with `npm ci --strict-peer-deps`, `npm ls --all`,
full frontend lint/tests/build, and the actual `admin_ui/Dockerfile` image build.
Browser checks use production assets and intercepted synthetic APIs for login,
History detail/confirmation, configuration persistence and Monaco lifecycle.
No provider requests, calls, live configuration writes, deployment or restart
are needed for these local checks. A local image build does not establish live
service or call acceptance. Record exact test results in the PR.

Rollback restores the preceding frontend image/dependencies; this migration
introduces no persistent-data migration.

## Validation on final base

Base `6fd8df3c10149e1868231497887cc6a27075ec4a`, after #840 / #767,
Node 22.23.4:

- Strict clean install and complete `npm ls --all` passed without peer conflicts.
- 82 test files / 634 tests passed with two workers.
- Lint passed: zero errors and the unchanged 868-warning allowance.
- Production build and actual Admin image build passed.
- `tsc --noEmit` is not clean: exact baseline reports 105 errors and migration reports 104. The sole removed
  diagnostic is the recording-player test’s ES2022-only `Array.at` call; use
  ES2020-compatible indexing instead. The remaining file/line/code locations
  match baseline. Existing failures remain;
  React's expanded type names change some diagnostic text. No new diagnostic
  locations remain after the two scoped type adjustments.
- Chromium production-asset checks passed for rejected-login recovery,
  authenticated History, detail/confirmation cancellation, refresh, Streaming
  settings save preserving greeting values, and Monaco mount/remount with locally
  intercepted 0.54.0 assets (the existing loader's requested version).
- Merged recording-player smoke passed: synthetic PCM playback/pause/seek
  availability, download byte fidelity, blob errors, ambiguous/missing/late
  refresh, close and no page errors. All API/audio fixtures were synthetic/local.
- Five repeated History open/close cycles passed on exact baseline and migration.
  Earlier preparation on `8e64c3a9` intermittently left/reopened the detail modal
  on both React 18 and 19; that observation was not reproduced in this final-base
  run and is not attributed to the migration.

No calls, provider requests, deployment, live configuration writes or service
restarts were performed. Browser checks do not establish live call acceptance.
