"""Bounded login counters and recognition of previously successful login origins."""

import hashlib
import hmac
import ipaddress
import math
import os
import sqlite3
import time
from collections import OrderedDict, deque
from collections.abc import Hashable
from contextlib import closing
from pathlib import Path


_LABEL_KEY = os.urandom(32)


def login_label(value: str) -> bytes:
    """Keep untrusted login names and addresses out of the bounded counter table."""
    return hmac.digest(_LABEL_KEY, value.encode(), "sha256")


class LoginThrottle:
    def __init__(self, max_failures=5, window_s=900, lockout_s=900, clock=time.monotonic,
                 capacity=10_000):
        self._max_failures = max_failures
        self._window_s = window_s
        self._lockout_s = lockout_s
        self._clock = clock
        self._capacity = capacity
        self._partial = OrderedDict()
        self._locks = OrderedDict()

    def _expire_locks(self, now):
        # Fixed lock duration means insertion order is also expiry order.
        while self._locks and next(iter(self._locks.values())) <= now:
            self._locks.popitem(last=False)

    def retry_after(self, key: Hashable, *, include_saturation=True) -> int:
        now = self._clock()
        self._expire_locks(now)
        until = self._locks.get(key)
        if until is None and include_saturation and len(self._locks) == self._capacity:
            until = next(iter(self._locks.values()))
        return math.ceil(until - now) if until is not None else 0

    def failure_count(self, key: Hashable) -> int:
        now = self._clock()
        self._expire_locks(now)
        if key in self._locks:
            return self._max_failures
        recent = self._partial.get(key)
        if recent is None:
            return 0
        while recent and recent[0] <= now - self._window_s:
            recent.popleft()
        if not recent:
            del self._partial[key]
        return len(recent)

    def record_failure(self, key: Hashable) -> None:
        if self.retry_after(key):
            return
        self.failure_count(key)
        recent = self._partial.get(key)
        if recent is None:
            # Reserved budgets accept only registered accounts or previously
            # authenticated pairs. Expire idle entries in last-failure order.
            if self._capacity is None:
                cutoff = self._clock() - self._window_s
                while self._partial and next(iter(self._partial.values()))[-1] <= cutoff:
                    self._partial.popitem(last=False)
            elif len(self._partial) + len(self._locks) >= self._capacity:
                self._partial.popitem(last=False)
            recent = self._partial[key] = deque()
        self._partial.move_to_end(key)
        now = self._clock()
        recent.append(now)
        if len(recent) >= self._max_failures:
            del self._partial[key]
            self._locks[key] = now + self._lockout_s

    def record_success(self, key: Hashable) -> None:
        self._partial.pop(key, None)
        self._locks.pop(key, None)


def client_address(peer: str, forwarded: str, trusted: str) -> str:
    """Walk from the socket peer towards the first untrusted forwarding hop."""
    if not trusted.strip() or not forwarded:
        return peer
    try:
        networks = [ipaddress.ip_network(entry.strip(), strict=False) for entry in trusted.split(",")]
        address = ipaddress.ip_address(peer)
        for hop in reversed(forwarded.split(",")):
            if not any(address in network for network in networks):
                break
            address = ipaddress.ip_address(hop.strip())
        return str(address)
    except ValueError:
        return peer


_ORIGIN_TTL = 30 * 86400


def _origin_key(username, address):
    return hashlib.sha256(f"{username}\0{address}".encode()).digest()


def known_login_origin(path: Path, username: str, address: str, *, now=None) -> bool:
    if not path.is_file():
        return False
    cutoff = (time.time() if now is None else now) - _ORIGIN_TTL
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        return db.execute("SELECT 1 FROM login_origins WHERE origin = ? AND last_login > ?",
                          (_origin_key(username, address), cutoff)).fetchone() is not None


def remember_login_origin(path: Path, username: str, address: str, *, now=None) -> None:
    now = time.time() if now is None else now
    fd = os.open(path, os.O_RDONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(fd)
    with closing(sqlite3.connect(path, timeout=1)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS login_origins(origin BLOB PRIMARY KEY, last_login REAL NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS origins_expiry ON login_origins(last_login)")
        db.execute("DELETE FROM login_origins WHERE last_login <= ?", (now - _ORIGIN_TTL,))
        db.execute("INSERT OR REPLACE INTO login_origins VALUES (?, ?)", (_origin_key(username, address), now))
