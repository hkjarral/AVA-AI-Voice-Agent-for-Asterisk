"""Shared per-call serialization for VICIdial lifecycle mutations."""

from __future__ import annotations

import asyncio
import threading
import weakref


_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_locks_guard = threading.Lock()


def vicidial_lifecycle_lock(call_id: str) -> asyncio.Lock:
    """Return the shared lock for one active VICIdial-owned call."""
    normalized = str(call_id or "").strip()
    if not normalized:
        raise ValueError("VICIdial lifecycle call ID is required")
    with _locks_guard:
        lock = _locks.get(normalized)
        if lock is None:
            lock = asyncio.Lock()
            _locks[normalized] = lock
        return lock
