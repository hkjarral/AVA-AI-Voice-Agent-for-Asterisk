# Agents experience: upgrade, rollback and voiprnd acceptance

## Scope

The Agents page adds searchable cards/list views, configuration-based tool summaries,
96px card avatars and a routed editor with General, Conversation, Tools and Advanced
sections. The editor preserves the existing controls and sends only changed fields.
Internal navigation, browser Back and page unload protect unsaved changes.

Male/female presets are UI metadata, not TTS voice settings. Existing agents display
male AVA until an operator selects another preset. The UI does not infer the voice's
gender. A custom image overrides the picture; the preset remains its removal fallback.

## What existing operators will experience

- Their agents, default selection, enabled state, prompts, voices and tool policies
  remain in place. The first start adds two nullable appearance columns; there is
  no setup wizard or required per-agent configuration.
- Agents initially show the male AVA mascot, even if their configured voice is
  female. Operators can select Female in General settings. This is a visual label,
  not automatic voice detection or a change to the TTS provider.
- Edit now opens a full page with General, Conversation, Tools and Advanced
  sections. Existing tool permissions, including Calendly appointment access,
  remain editable in Tools. The card's configured-tool summary links there.
- Card/list preference is saved in that browser. Search and filters are temporary.
  Configured-tool counts describe configuration, not verified tool availability.
- Uploads are saved only with **Save changes**. A custom image stays visible when
  switching the male/female fallback; **Use default image** removes the override.
  Images must be PNG, JPEG or WebP, at most 2 MiB and 16 million pixels. They are
  resized to at most 256×256; metadata and animation are not retained.
- Save open edits before deployment. Admin UI briefly disconnects during its
  container replacement; running calls continue in the unchanged engine. An old
  browser tab can retain the previous UI until refreshed. Hard-refresh after the
  deployment (or a rollback); a cached lazy-loaded page may otherwise fail to load.
  Existing credentials and the JWT secret are retained, so this change does not
  deliberately log users out; an already expired session still requires sign-in.
- On rollback, new editor deep links are unavailable in the old UI; return to
  `/agents`. Appearance is hidden by the old UI but retained in SQLite for a later
  re-upgrade. Do not restore the database just to roll back the application.

## Storage and compatibility

- `AgentsStore` adds nullable `avatar_preset` and `avatar_image` TEXT columns only.
  No rows, identifiers, routing flags, defaults or existing settings are rewritten.
  Missing columns are checked on every initialization. A partial migration resumes;
  inability to add required columns fails initialization instead of serving broken writes.
- PNG/JPEG/WebP inputs are limited to 2 MiB and 16 million pixels, decoded and
  re-encoded as metadata-free PNG thumbnails no larger than 256×256 and 256 KiB.
  Image bytes are stored as a data URL in the agent row. Save, replace and remove
  occur through the existing authenticated Agent API. Cancel writes nothing.
- This deliberately replaces the initially proposed external image directory.
  Images and settings are in the same SQLite transaction and backup. There are no
  separate files to orphan, serve publicly or recover out of sync. Deleting the row
  also deletes its image; existing backups may retain earlier versions.
- The existing persistent data volume and `AGENTS_DB_PATH` are reused. No new
  volume/service or image-directory migration is needed. Default mascots ship in
  the Admin UI image. No engine, provider or telephony code changes are required.
- YAML reconciliation retains the appearance columns and imported-agent ownership.
  Appearance is intentionally excluded from runtime YAML exports. A YAML export
  alone is therefore not a backup of appearance; preserve the SQLite database.
- Older clients omit the new fields and leave them untouched. The immediate
  predecessor Admin UI ignores the columns and continues to read/create/update
  agents. Do not remove columns during rollback. Re-upgrade retains appearance.
  Compatibility is verified against the implementation branch's base commit;
  arbitrary older major-version downgrades are not covered.

## Before deploying to voiprnd

1. Record the running Admin UI image ID, deployed revision and resolved Compose
   configuration. Preserve local operator configuration; do not reset the server checkout.
2. Check the actual `AGENTS_DB_PATH`, data mount, available disk space and permissions.
   The normal updater snapshots `agents.db` with SQLite's backup API. Its default
   host-path backup must not be assumed to cover a relocated database: explicitly
   snapshot the configured path when using one.
3. Make a consistent database backup before the new Admin UI first starts. With
   the current Admin UI running, the following creates a snapshot beside the real
   database, under the same persistent directory. Verify that directory is mounted
   on the host and copy the snapshot off-host according to the normal backup policy:

   ```sh
   docker compose exec -T admin_ui python - <<'PY'
   import os, sqlite3
   from datetime import datetime, timezone
   from pathlib import Path
   db = Path(os.getenv('AGENTS_DB_PATH', '/app/data/operator/agents.db'))
   if not db.is_file():
       raise SystemExit(f'No existing database at {db}; investigate before deployment')
   stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
   backup = db.parent / f'agents-before-experience-{stamp}.db'
   with sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True) as source:
       with sqlite3.connect(backup) as destination:
           source.backup(destination)
           assert destination.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
   backup.chmod(0o600)
   print(backup)
   PY
   ```

4. Build/tag the candidate Admin UI image from the reviewed commit and retain the
   old image. Recreate **only `admin_ui`** using the deployment's normal Compose
   configuration. Do not restart the engine to apply this UI change. Allow any
   existing legacy Context migration to complete through its established path.
5. Check Admin UI startup logs and authenticated API access, then hard-refresh the
   browser to load the matching frontend. Record candidate revision/image and results.

## Voiprnd acceptance checklist

- Compare existing agent count, IDs, slugs, voices, providers/pipelines, active/default
  flags, prompts and tool policies with the pre-deployment snapshot.
- Confirm cards/list, search, status/engine filters and empty results; verify 96px
  right-side card avatars and compact left-side list avatars in both themes.
- Open Tools from a card and inspect the existing access policies. Counts are
  configured capabilities including inherited selections, never connection-health claims.
- Change only a preset and verify the request changes only appearance; confirm the
  provider, actual voice, routing and tool permissions are unchanged.
- Upload PNG/JPEG/WebP, save, refresh, replace, remove and cancel. Check invalid
  format/oversize errors and confirm a rejected save leaves previous data intact.
- Navigate Back or to another section of the application with unsaved changes;
  test both Keep editing and Discard. Test reload's browser warning.
- Recreate Admin UI and verify appearance persists. Reconcile an imported YAML
  agent and verify its custom avatar survives without changing ownership.
- Place an agreed test call to an existing agent and verify its voice and routing.
  This live call check remains a deployment acceptance step; local tests do not
  substitute for a voiprnd call.

## Rollback

For a UI/application regression, redeploy the recorded predecessor Admin UI image
and hard-refresh browsers. **Keep the upgraded database and data mount.** Nullable
extra columns are ignored by the predecessor; old-UI edits preserve their contents.
This preserves operator edits made since deployment and avoids a destructive DB restore.

Restore the snapshot only for confirmed data damage and after deciding which newer
operator changes would be lost. Stop all database writers/readers (`admin_ui` and
`ai_engine`, plus any external tooling), retain a copy of the failed database and
its WAL/SHM files, restore the verified snapshot at the configured database path,
and remove only stale WAL/SHM sidecars associated with the replaced database.
Preserve file owner/permissions, run `PRAGMA integrity_check`, then start the
recorded compatible images. Never copy a live SQLite main file over another live
database or treat an application rollback as automatic database restoration.

## Local validation

Automated tests cover legacy and partially upgraded schemas, repeated migration,
image validation/normalization, omitted-vs-null PATCH behavior, YAML reconciliation,
SQLite online backup/restore, changed-field saves and blocked browser navigation.
The predecessor store is also exercised against an upgraded DB for edit/create and
re-upgrade compatibility. Browser checks use an isolated sample database, not voiprnd.

## voiprnd deployment record — 2026-10-24

- Waited for CI, Regression Hardening, CodeQL and Admin UI URL Validation to pass
  on main `198a4aab9c117c5667f9546a82bfd00693ad4ae9` before merging it into PR #772.
  The merge retains Calendly appointment access in the routed Tools section.
- Deployed application revision `7ef4670487a39829e544b53db8cc019cf7d1c73b`.
  Its draft CI/PR gate and CodeQL passed. The merged frontend passed all 529 tests
  and a production build; 104 focused backend/store/API/engine-resolution tests passed.
- Image: `ava-admin-ui:agents-experience-7ef46704`, digest
  `sha256:5b32e09188ff5ebc8bc22b246809f9bd3d35f1becd52b1bb6e87b2f084a0109d`.
  Runtime verified as Python 3.11.17 and Pillow 13.0.0. Image decoding is restricted
  to the declared supported format before parsing.
- Source is an isolated checkout at
  `/root/aava-candidates/agents-experience-7ef46704`. The original dirty checkout
  and the active Calendly engine source were retained. Admin UI keeps the existing
  config, secrets, environment, data and media mounts. Resolved Compose differences
  were checked to contain only the Admin image, build context and project source mount.
- Backup: `/root/aava-backups/agents-experience-20261024T210203Z/` (private directory).
  Contains a SQLite online backup with a successful integrity check, private row and
  config baselines, configuration archive, container/Compose snapshots and build logs.
  The host Python/SQLite is too old for this DB; the backup uses the running Admin
  container's SQLite API. Do not use the host SQLite client to manipulate this store.
- Rollback is prepared at the backup directory's `rollback-admin.sh`; it recreates
  only Admin UI using retained image `ava-admin-ui:agents-rollback-20261024T210203Z`.
  It keeps the live DB. A separate copy of the backup was upgraded, opened/edited by
  that exact old image, and re-upgraded successfully with custom appearance intact.
- Post-deployment checks at 21:15 UTC: all 15 original agent rows match every
  pre-upgrade field, both new columns are null, DB integrity is `ok`, all backed-up
  configuration files have identical content hashes, and legacy resource-policy
  promotion changed zero rows. Neither engine container ID/start time changed;
  engine config hash/load time are unchanged and ARI remains connected. There were
  zero active calls at the observed checkpoints. No calls were originated for testing.
- Admin health, nested-route serving, default mascot assets and unauthenticated API
  rejection passed. Startup completed without error/traceback/critical log entries.
  After operator sign-in, live browser checks passed for all 15 cards, search by
  name/tool, no-results state, status/engine filters, card/list views, the Tools
  deep link, Calendly appointment permissions, all four settings sections, the
  female avatar preview and Keep editing/Discard navigation protection. Discard
  retained the original male fallback. At 21:29 UTC, all 15 saved agent rows and
  configuration hashes still matched the backup; engine IDs/start times/config
  remained unchanged. No live agent was created, saved or deleted for testing.
  Image save/reload/remove persistence was exercised against the isolated local
  database and image compatibility copy, not by modifying live agents.
- Observed upgrade caveats: an already-open tab displayed the old workspace until
  a normal refresh; authentication survived that refresh. The tool catalog took
  several seconds to populate in the initial staging candidate. The final PR
  replaces the temporary zero counts/empty message with an accessible "Loading
  tools and selections…" status until the catalog succeeds or fails. Deferred
  response tests cover populated, empty and failed catalogs, including preserving
  saved tool settings when an unrelated appearance edit is saved after failure.
  The narrow in-app browser is easier to use with the existing sidebar collapsed.
- This is a detached staging candidate, not a merged release. Use the recorded
  Compose deployment/rollback commands for this candidate; do not use the general
  Updates page to switch revisions during acceptance. The operator accepted the
  staged experience and requested the loading fix before freezing the PR and
  running final CI/reviews. The deployment above records the initial candidate;
  check the PR's final validation record for subsequent candidate revisions.
