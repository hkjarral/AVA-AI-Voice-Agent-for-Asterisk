# Assisted transfer destination setup

In **Tools → In-Call → Transfer & live agents**, use **Discover endpoints**
beside **Add Destination** to find candidates through your existing Asterisk
ARI connection. Discovery is also available while the Transfer Tool is disabled;
it does not enable that tool.

## Discover, review, and apply

1. Configure the connection on the **Asterisk** page. No additional credentials
   or FreePBX API application are needed. The Admin UI uses its configured ARI
   host, scheme, port, credentials, and TLS verification setting. Saved settings
   may differ from those currently loaded by the AI Engine.
2. Click **Discover endpoints**. The authenticated Admin API makes only
   `GET /ari/endpoints`. Search the results and explicitly select candidates.
   Offline and unknown endpoints can be selected; the reported state is a
   snapshot, not a promise that someone is available.
3. Click **Review selected**. Confirm a unique destination key, dialable
   extension number, description/name, and dialplan context for each candidate.
   Numeric resource identifiers are suggested as numbers, preserving leading
   zeros; other identifiers require a number to be entered. ARI does not supply
   directory names. The context defaults to the configured extension-transfer
   context, or `from-internal`.
4. Click **Add to configuration**. Server-side validation checks the complete
   batch against your current draft, including unsaved destinations. Conflicting
   keys or equal extension number/context pairs block the batch. Nothing is
   overwritten. Use **Back** to deselect an existing destination or correct the
   fields.
5. Use the page's existing **Save & Apply** action to save and publish the
   destinations for new calls. Active calls retain their captured generation.
   A failed save retains your edits. If saving succeeds but applying fails, the
   page says so and the previous tool generation remains active.

Agents using **Inherit** can use added destinations when their transfer tools
are enabled. Agents using **Selected** keep their existing selections; choose
the new destination explicitly on the Agent if needed. **None**, stale, and
empty selections retain their existing fail-closed behavior. Discovery does not
change Agent policies.

Selected candidates become ordinary `tools.transfer.destinations` entries of
type `extension`. They do not create Live Agents, enable attended transfer,
change handoff settings, or modify PBX routing. Existing transfer execution uses
the confirmed number through the configured dialplan context. All existing
manual setup remains available for extensions, queues, ring groups, feature
codes, and custom targets.

## What discovery can tell you

Asterisk endpoints identify devices by technology and resource, such as
`PJSIP/102`. They may include trunks. A resource name is not authoritative proof
of an internal extension or its dialable number, and an online device is not
necessarily an available agent. Virtual extensions and dialplan destinations
may have no endpoint at all.

Results are temporary and are never an authorization inventory. Closing or
cancelling the dialog discards its selections. Refresh clears selections; if a
refresh fails, any older results are marked as a previous snapshot and cannot
be selected until discovery succeeds again. Disappearing or offline endpoints
do not remove saved destinations. There is no background synchronization.

FreePBX GraphQL directory discovery, OAuth, and richer directory metadata are
separate future work. This feature neither creates PBX extensions nor changes
the transfer execution path.

## Limits and troubleshooting

- Discovery has a 10-second network deadline, a 1 MiB uncompressed response limit,
  and a limit of 5,000 endpoints. Responses must be complete and structurally
  valid; duplicate technology/resource pairs or malformed rows reject the
  inventory atomically. Discovery requests identity encoding and rejects compressed
  responses before reading or decompressing their bodies.
- Up to 100 destinations can be reviewed per batch. Assisted setup accepts keys
  of 1–64 letters/numbers/underscores/hyphens, starting with a letter or number;
  numbers of 1–32 digits; descriptions of 1–200 characters without control
  characters; and contexts of 1–80 letters/numbers/underscores/dots/hyphens,
  starting with a letter, number, or underscore. Manual configuration keeps its
  existing behavior.
- Authentication/permission errors: check the existing ARI credentials and
  user permissions on the Asterisk page.
- Endpoint API unavailable: check that the Asterisk endpoint ARI module is
  installed/enabled. Manual setup remains available.
- Connection/TLS/timeout errors: check the configured connection and certificate
  trust. Discovery does not change TLS settings or retry automatically.
- Oversized/invalid inventory: no partial inventory is accepted. Retry after
  fixing the upstream response or use manual setup.

Redirects are not followed. Credential values, channel IDs, upstream response
bodies, and exception details are not returned by discovery. Listing and review
never originate, transfer, hang up, reload, or otherwise alter calls, and never
persist configuration. Cancelling an in-flight request prevents late results
from adding destinations.

## Verified API contract

The Asterisk endpoint API has existed since 12.0.0 and is available in the
project's supported Asterisk versions when its ARI endpoint module is enabled.
The endpoint model supplies technology/resource identifiers, an optional
`online`/`offline`/`unknown` state, and channel IDs. Only identifiers and the
reported state are exposed here; missing state is shown as unknown.

- [Asterisk endpoint API](https://docs.asterisk.org/Asterisk_20_Documentation/API_Documentation/Asterisk_REST_Interface/Endpoints_REST_API/)
- [Asterisk endpoint model](https://docs.asterisk.org/Asterisk_20_Documentation/API_Documentation/Asterisk_REST_Interface/Asterisk_REST_Data_Models/#endpoint)

No database migration or new configuration source is introduced. Saved
destinations remain usable if the UI feature is rolled back.
