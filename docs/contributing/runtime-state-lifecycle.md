# Runtime diagnostic and endpoint-cache ownership

The engine and streaming manager retain a small amount of process-local state
outside `SessionStore`. Its lifetime follows the owner below; segment completion
and call completion are separate events.

| State | Owner and release boundary |
| --- | --- |
| `StreamingPlaybackManager._first_send_logged` | Call ID. Preserve the first-frame log suppression across responses, playback restarts and AudioSocket reconnects. `Engine._cleanup_call` calls the manager's `cleanup_call` as part of its owned final teardown. |
| `StreamingPlaybackManager._rtp_remote_wait_logged` | Stream ID. `_cleanup_stream` releases only that stream's entry, including failure, cancellation and stale-producer cleanup. A late RTP write result must not re-add retired stream state. |
| `Engine._last_transcript_ts` | Call ID. Pipeline STT writes this timestamp and the response path reads it for latency histograms. Final call cleanup releases it, including an interrupted teardown. Late buffered/streaming STT results honor the captured session's terminal marker so they cannot recreate the timestamp. |
| `Engine._audiosocket_frame_count` | Caller channel ID. Count live-session ingress frames; release on the last connection's disconnect and again at final call cleanup. Disconnecting one of several connections preserves the shared counter. |
| `Engine.channel_to_conns`, `conn_to_channel`, `audiosocket_primary_conn` | Active AudioSocket bindings. Retire the disconnected connection synchronously before awaiting session lookup; clear session media fields only if they still refer to that connection. Final call cleanup also releases residual bindings if transport disconnect failed. |
| `Engine._outbound_pjsip_endpoint_cache` | Engine instance. Positive and negative probe results expire using the existing `AAVA_OUTBOUND_PJSIP_ENDPOINT_CACHE_TTL_SECONDS` (default 300 seconds). Access and insertion prune expired numbers; insertion caps the cache at 1,024 results, evicting the oldest inserted result. Probe completion sets the timestamp. A nonpositive TTL disables retention. |

Before any transport send and after awaited diagnostics, output setup or write results,
`_send_audio_chunk` checks the session terminal flags and the exact stream object
and ID. A late producer cannot start new sends or repopulate diagnostic markers
after teardown begins. A transport write already submitted before teardown may
complete, but its late result does not update retired state or start another send.

Ownership loss is cancellation, not a recoverable transport failure. The drain,
frame emitter and pacer retain their captured stream across awaits; they must
not retain rejected speech, consume a replacement's remainder/queue, record its
failure or change its end reason. File fallback rechecks that owner before
buffer consumption and playback. Its admission predicate follows file creation,
gating and playback registration through `PlaybackManager` to ARI submission.
If ownership changes while an ARI request is already in flight, only that
playback ID is stopped. An unconfirmed stop retains the existing cancellation
retry owner and token; it must not release a replacement's output ownership.
Healthy transport failures still retain speech and use the existing file fallback.

The unused `_last_segment_end_ts` and `channel_to_conn` containers were removed.
Use the active stream or the connection set rather than reintroducing parallel
state. Cleanup is idempotent and must leave other calls and replacement streams
or connections intact.

The cache remains an optimization: a miss or eviction uses the same PJSIP/SIP
probe order and Local-channel fallback. It does not retain consent, routing
ownership, DNC, callback, disposition or booking data. These fixes add no operator
settings, API/schema changes, migrations, persistence or frontend controls.
Deployment uses the normal engine replacement/restart procedure; existing
configuration and defaults remain valid. Restart begins with empty process-local
state; rollback requires no data conversion.

## Offline verification

Run `PYTHONPATH=. python -m pytest tests/test_runtime_state_cleanup.py tests/test_streaming_fallback_ownership.py -q` with
the repository's Python dependencies. The tests use synthetic frames, mocked ARI
and transport writers, isolated call sessions and private temporary data paths.
They cover repeated calls, multiple segments, reconnects, stale callbacks,
interrupted cleanup, cache expiry, concurrent insertion and eviction. They do
not establish live PBX/provider acceptance or a measured memory/latency gain.
