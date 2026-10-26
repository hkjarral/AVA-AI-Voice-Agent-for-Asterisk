# Integration backup and rollback safety

The updated CLI and pinned recovery script preserve `secrets/` (including immutable
provider/Pushover credentials and the Calendly token) with owner-only files inside
a private backup directory. They snapshot `data/operator/calendly.db` through
SQLite's online backup API, including committed WAL data, and verify integrity.
They also preserve the non-secret `calendly.db.configured` privacy marker, which
must survive disconnect and temporary journal absence. They do not copy a live
journal's main file alone or restart either service.
Missing integrations are skipped; an unreadable/corrupt existing journal, unsafe
symlink, or failed copy stops the update before checkout changes.

Each preservation pass creates a separate private `integration-state.*` directory
inside the normal backup tree. An overwrite-mode refresh does not overwrite the
earlier snapshot. Failed/partial snapshots are not a completed recovery set.

Python 3.7+ with SQLite's backup API is required wherever the helper runs when
integration state exists (journal, managed secrets or configured overrides).
The new updater image includes this runtime. Rebuild an older updater from the
target source or select the target release image before UI updates; old images
cannot gain Python through CLI self-update. The CLI skips the helper when no
integration state exists, even without Python. If the host cannot read the journal, make a private, verified snapshot
using the application's Python/SQLite runtime and resolve host compatibility before
retrying; do not bypass the failure or replace it with a raw live-file copy.
An old CLI does not gain this behavior merely by checking out new source. Use the
target release CLI or pinned recovery script, as described in the installation guide.

Standard Compose mounts map `/app/data/` to checkout `data/` and `/app/project/`
to checkout paths. `CALENDLY_DB_PATH` and `CALENDLY_TOKEN_PATH` from the process or
`.env` are supported within these paths or within the checkout. Expanded paths,
external mounts and symlinked state require an explicitly verified manual backup;
the helper refuses to guess a mapping. Compose-only path overrides are not discovered
by the host helper: verify actual service settings and snapshot relocated state
manually before invoking either updater. The same requirement applies to relocated
Agents/Call History stores. Preserve actual configured paths and ownership.

Backups contain private credentials and booking information. Keep them out of Git,
exports, support bundles and public PRs. Credential files and the journal are separate
snapshots, not one distributed transaction; do not rotate credentials or change
connections during the maintenance window. Validate connection identity/revision
after recovery; a mismatch must remain disabled until staff resolves it.

Call History's queued erasures and internal legacy-erasure flag live in its own
configured SQLite database. Preserve that database with a consistent WAL-aware
snapshot as well; an adapter journal/credential snapshot alone is not the full
privacy recovery set. The additive flag is ignored safely by older application
code, whose inserts retain the conservative default until upgraded writers resume.

## Application rollback is not state rollback

Keep the current persistent volumes, journal, operation barriers and credentials
when reverting application images. Disable Calendly admissions first; let already
admitted requests settle or remain visible for staff recovery. Remove unsupported
Calendly tool/policy references before starting older code. Do not automatically
restore a journal or credentials snapshot during source rollback: a remote booking
may have succeeded after that snapshot, and restoring it can erase replay protection.

For confirmed data damage only, stop all writers/readers before restoring a verified
SQLite backup, preserve the damaged database and sidecars privately, and reconcile
all remote operations after the backup timestamp. Keep booking disabled until staff
has reviewed that interval and validated credential identity. Local restore does
not cancel appointments or establish that a submission never happened.

Agent avatars remain part of `agents.db`; YAML exports are not an appearance backup.
See [Agents rollback](AGENTS_EXPERIENCE_ROLLOUT.md) and
[Calendly storage/recovery](Calendly-calendar-tool.md#storage-privacy-and-recovery).
