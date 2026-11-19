"""Action-scoped announcement readiness. No provider I/O or telephony policy here.

The engine drives this synchronously before its first event-handler await. Sealing
closes admission immediately; the async owner then drains and commits normally.
"""
from __future__ import annotations

import asyncio
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Callable


def normalized_words(text: str) -> str:
    return " ".join("".join(c if c.isalnum() else " " for c in unicodedata.normalize("NFKC", text).casefold()).split())


@dataclass
class Announcement:
    action_id: str
    config: dict
    origin_id: str | None = None
    origin: dict = field(default_factory=dict)
    clock: Callable[[], float] = time.monotonic
    prepared_at: float = field(default_factory=time.monotonic)
    sent_at: float | None = None
    selected_id: str | None = None
    selected_started_at: float | None = None
    sealed: bool = False
    cancelled: bool = False
    reason: str | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def seal(self, reason: str) -> None:
        if not self.sealed:
            self.sealed = True
            self.reason = reason
            self.changed.set()

    def cancel(self) -> None:
        self.cancelled = True
        self.changed.set()

    def _origin_satisfied(self) -> bool:
        text = normalized_words(self.origin.get("text", ""))
        sentence = normalized_words(self.config["message"])
        return bool(sentence and f"{text} ".startswith(f"{sentence} ") and self.origin.get("completed")
                    and self.origin.get("started_at") is not None and not self.origin.get("interrupted"))

    def observe(self, event: dict) -> None:
        if self.cancelled or self.sealed:
            return
        kind = event.get("type")
        evidence = event.get("handoff_evidence") or {}
        if kind == "HandoffToolResultSent":
            if event.get("success") is False:
                self.seal("tool_result_send_failed")
                return
            if self.sent_at is None:
                self.sent_at = event["sent_at"]
                self.changed.set()
            self.origin.update(event.get("origin") or {})
        turn_id = evidence.get("id")
        if turn_id and turn_id == self.origin_id:
            self.origin.update(evidence)
        if self.sent_at is not None and self._origin_satisfied():
            self.seal("origin_sentence_matched")
            return
        started = evidence.get("started_at")
        if (self.sent_at is not None and turn_id and turn_id != self.origin_id
                and started is not None and started >= self.sent_at):
            if self.selected_id is None and started <= self.sent_at + self.config["start_timeout_sec"]:
                self.selected_id = turn_id
                self.selected_started_at = started
                self.changed.set()
            if turn_id == self.selected_id and evidence.get("completed") and not evidence.get("interrupted"):
                self.seal("reply_completed")

    def deadline(self) -> float:
        if self.sent_at is None:
            return self.prepared_at + self.config["finish_timeout_sec"]
        if self.selected_started_at is None:
            return self.sent_at + self.config["start_timeout_sec"]
        return self.selected_started_at + self.config["finish_timeout_sec"]

    def expire(self) -> None:
        if not self.sealed and self.clock() >= self.deadline():
            self.seal("finish_timeout" if self.selected_id else "start_timeout")

    async def wait(self, still_owned) -> bool:
        while not self.cancelled:
            if not await still_owned():
                self.cancel()
                return False
            self.expire()
            if self.sealed:
                return True
            self.changed.clear()
            try:
                # Recheck ownership even when cancel_transfer emits no provider event.
                await asyncio.wait_for(self.changed.wait(), timeout=min(0.1, max(0.001, self.deadline() - self.clock())))
            except asyncio.TimeoutError:
                pass
        return False
