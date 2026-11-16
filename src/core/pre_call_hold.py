"""A pre-call invocation's delayed, caller-only ARI playback owner."""

import asyncio
from uuid import uuid4

import structlog

logger = structlog.get_logger(__name__)


class PreCallHold:
    """Retain the ID across cancellation and an in-flight playback start.

    A caller-supplied ARI playback ID lets teardown stop even a start whose
    response was lost. Never cancel an in-flight POST merely because the tool
    finished: stop again after that request settles, covering a late acceptance.
    All network waits are bounded; the late finalizer stays on the session.
    """

    def __init__(self, ari, session, sound, threshold_ms, stop_connection):
        self.ari = ari
        self.session = session
        self.sound = sound
        self.threshold_ms = threshold_ms
        self.stop_connection = stop_connection
        self.playback_id = f"pre-call-hold-{uuid4().hex}"
        self.closed = False
        self.request_started = False
        self.stopped = False
        self._stop_lock = asyncio.Lock()
        session.pre_call_hold_audio.add(self)
        self.task = asyncio.create_task(self._run(), name=self.playback_id)

    async def _stop(self):
        if not self.request_started:
            return
        async with self._stop_lock:
            for attempt in range(3):
                try:
                    if await asyncio.wait_for(self.ari.stop_playback(self.playback_id), 1.0) is True:
                        self.stopped = True
                        return
                except Exception:
                    pass
                if attempt < 2:
                    await asyncio.sleep(0.05)
            logger.warning("Pre-call hold playback stop unconfirmed", call_id=self.session.call_id)

    async def _run(self):
        try:
            await asyncio.sleep(self.threshold_ms / 1000.0)
            if self.closed or self.session.cleanup_in_progress or self.session.cleanup_completed:
                return
            await self.stop_connection(self.session, reason="pre-call-hold-audio")
            if self.closed or self.session.cleanup_in_progress or self.session.cleanup_completed:
                return
            self.request_started = True
            await asyncio.wait_for(self.ari.play_sound(
                self.session.caller_channel_id, self.sound, playback_id=self.playback_id,
            ), 5.0)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Pre-call hold playback start unconfirmed", call_id=self.session.call_id)
        finally:
            if self.closed or self.session.cleanup_in_progress or self.session.cleanup_completed:
                # A previous stop may have seen 404 before this POST completed.
                self.stopped = False
                await self._stop()
                if self.stopped or not self.request_started:
                    self.session.pre_call_hold_audio.discard(self)

    async def close(self):
        self.closed = True
        if not self.request_started and not self.task.done():
            self.task.cancel()
        # Let the start settle when possible. On timeout _run retains ownership
        # and repeats the stop after the late response, independently of tools.
        try:
            await asyncio.wait_for(asyncio.shield(self.task), 2.0)
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            if not self.task.cancelled():
                raise
        if not self.stopped:
            await self._stop()
        if self.task.done() and (self.stopped or not self.request_started):
            self.session.pre_call_hold_audio.discard(self)


async def stop_pre_call_holds(session):
    """Close only hold resources owned by this session, including late starts."""
    await asyncio.gather(*(hold.close() for hold in list(session.pre_call_hold_audio)))
