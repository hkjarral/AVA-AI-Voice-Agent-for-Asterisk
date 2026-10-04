"""Bounded source backlog for the opt-in Google Developer API experiment."""

import asyncio


class AudioBacklogQueue(asyncio.Queue):
    """Keep PCM by byte budget, with separate bounded space for control markers.

    Admission never waits in the provider receive callback. The existing playback
    consumer still uses get(). Item limits also bound tiny-chunk object overhead.
    """

    def __init__(self, max_bytes: int, *, max_audio_items: int = 8192):
        super().__init__()
        self.max_bytes = max_bytes
        self.max_audio_items = max_audio_items
        self.pending_bytes = 0
        self.audio_items = 0
        self.control_items = 0
        self.closed = False

    @staticmethod
    def _is_audio(item):
        return isinstance(item, (bytes, bytearray))

    def _fits(self, item):
        if self.closed:
            return False
        if self._is_audio(item):
            return (
                self.pending_bytes + len(item) <= self.max_bytes
                and self.audio_items < self.max_audio_items
            )
        return self.control_items < 8

    def full(self):
        return self.closed or self.pending_bytes >= self.max_bytes or self.audio_items >= self.max_audio_items

    def put_nowait(self, item):
        if not self._fits(item):
            raise asyncio.QueueFull
        # Equivalent to asyncio.Queue.put_nowait, with per-item admission above.
        self._put(item)
        self._unfinished_tasks += 1
        self._finished.clear()
        self._wakeup_next(self._getters)

    async def put(self, item):
        while not self._fits(item):
            if self.closed:
                raise asyncio.QueueFull
            waiter = self._get_loop().create_future()
            self._putters.append(waiter)
            try:
                await waiter
            except BaseException:
                waiter.cancel()
                try:
                    self._putters.remove(waiter)
                except ValueError:
                    pass
                if not waiter.cancelled():
                    self._wakeup_next(self._putters)
                raise
        self.put_nowait(item)

    def _put(self, item):
        super()._put(item)
        if self._is_audio(item):
            self.pending_bytes += len(item)
            self.audio_items += 1
        else:
            self.control_items += 1

    def _get(self):
        item = super()._get()
        if self._is_audio(item):
            self.pending_bytes -= len(item)
            self.audio_items -= 1
        else:
            self.control_items -= 1
        return item

    def close(self):
        """Discard cancelled audio, reject late writes, and wake blocked users."""
        if self.closed:
            return
        while not self.empty():
            self.get_nowait()
            self.task_done()
        self.put_nowait(None)
        self.closed = True
        while self._putters:
            self._wakeup_next(self._putters)
