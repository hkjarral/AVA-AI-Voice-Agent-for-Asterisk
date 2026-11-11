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

## Preparation evidence

On base `8e64c3a997cc098c66f0f07c9db7ba09a53ef859`, Node 22.23.4:

- Strict clean install and complete `npm ls --all` passed without peer conflicts.
- 81 test files / 628 tests passed with two workers. The affected HTTP editor's
  14 tests passed again after the nullable-ref type correction.
- Lint passed: zero errors and the unchanged 868-warning allowance.
- Production build and actual Admin image build passed.
- `tsc --noEmit` is not clean on baseline: both baseline and migration report
  104 errors at the same file/line/code locations. Existing diagnostics remain;
  React's expanded type names change some diagnostic text. No new diagnostic
  locations remain after the two scoped type adjustments.

These results precede the final-base refresh; rerun checks affected by integration
before publication. Browser and final-head evidence belong in the PR report.

Local production-asset Chromium checks passed for rejected-login recovery,
authenticated History rendering, individual detail close/delete cancellation,
refresh, Streaming settings save preserving saved greeting values, and Monaco
mount/remount using locally intercepted 0.54.0 assets (the existing loader's
requested version). All API responses were synthetic; external requests were
blocked or fulfilled locally.

Repeated History close attempts intermittently leave/reopen the detail modal.
The same fixture reproduces this on unchanged React 18 base `8e64c3a9`; this is
not attributed to the migration. Recheck after the pending #767 History work is
integrated; do not claim repeated History lifecycle acceptance until resolved.
