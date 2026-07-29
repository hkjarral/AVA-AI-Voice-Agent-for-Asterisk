"""Conversation coordination and observability utilities.

This module centralizes the logic that toggles audio capture, tracks
conversation state transitions, and exposes Prometheus metrics so the
engine can make gating decisions with a single source of truth.
"""

from __future__ import annotations

import asyncio
from typing import Dict, Optional, Protocol, TYPE_CHECKING, Set

import structlog
from prometheus_client import Counter, Gauge

from .models import CallSession
from .session_store import SessionStore

if TYPE_CHECKING:  # pragma: no cover
    from .playback_manager import PlaybackManager

logger = structlog.get_logger(__name__)

# Prometheus metrics are defined at module scope so they are registered
# exactly once even if the coordinator is instantiated multiple times.
_TTS_GATING_GAUGE = Gauge(
    "ai_agent_tts_gating_active",
    "Number of calls with TTS gating active",
)
_AUDIO_CAPTURE_GAUGE = Gauge(
    "ai_agent_audio_capture_enabled",
    "Number of calls with upstream audio capture enabled",
)
_CONVERSATION_STATE_GAUGE = Gauge(
    "ai_agent_conversation_state",
    "Number of calls in each conversation state",
    labelnames=("state",),
)
_BARGE_IN_COUNTER = Counter(
    "ai_agent_barge_in_events_total",
    "Count of barge-in attempts detected while TTS playback is active",
)

# Accepted conversation states for the simple state gauge.
_CONVERSATION_STATES = ("greeting", "listening", "processing")


class NoInputWatchdogObserver(Protocol):
    """Structural interface used by TTS gating without importing the watchdog."""

    async def note_agent_output_start(self, call_id: str) -> None:
        """Observe the start of caller-facing output."""
        ...

    async def note_agent_output_end(
        self,
        call_id: str,
        *,
        reset_timer: bool = True,
        preserve_policy_state: bool = False,
    ) -> None:
        """Observe the end of caller-facing output."""
        ...


class ConversationCoordinator:
    """Central coordinator for conversation state and observability."""

    def __init__(self, session_store: SessionStore, playback_manager: Optional["PlaybackManager"] = None):
        self._session_store = session_store
        self._playback_manager = playback_manager
        self._capture_fallback_tasks: Dict[str, asyncio.Task] = {}
        self._barge_in_seen: Dict[str, bool] = {}
        self._barge_in_totals: Dict[str, int] = {}
        self._gated_calls: Set[str] = set()
        self._capture_enabled_calls: Set[str] = set()
        self._state_by_call: Dict[str, str] = {}
        self._no_input_watchdog: Optional[NoInputWatchdogObserver] = None

    def set_playback_manager(self, playback_manager: "PlaybackManager") -> None:
        """Attach the playback manager after initialisation."""
        self._playback_manager = playback_manager

    def set_no_input_watchdog(self, watchdog: NoInputWatchdogObserver) -> None:
        """Attach the optional caller-inactivity observer.

        Kept duck-typed to avoid a core-module import cycle.
        """
        self._no_input_watchdog = watchdog

    async def register_call(self, session: CallSession) -> None:
        """Initialise metrics for a newly tracked call session."""
        logger.debug("ConversationCoordinator registering call", call_id=session.call_id)
        self._set_capture_enabled(session.call_id, bool(session.audio_capture_enabled))
        self._set_tts_gated(session.call_id, bool(session.tts_playing))
        self._set_state_metric(session.call_id, session.conversation_state)
        self._barge_in_seen[session.call_id] = False
        self._barge_in_totals.setdefault(session.call_id, 0)
        # Ensure we do not leak old fallback tasks
        await self._cancel_capture_fallback(session.call_id)

    async def unregister_call(self, call_id: str) -> None:
        """Remove metrics and timers associated with a call session."""
        logger.debug("ConversationCoordinator unregistering call", call_id=call_id)
        await self._cancel_capture_fallback(call_id)
        self._barge_in_seen.pop(call_id, None)
        self._barge_in_totals.pop(call_id, None)
        self._gated_calls.discard(call_id)
        self._capture_enabled_calls.discard(call_id)
        self._state_by_call.pop(call_id, None)
        self._refresh_metrics()

    async def sync_from_session(self, session: CallSession) -> None:
        """Synchronise gauges to reflect the latest session values."""
        self._set_capture_enabled(session.call_id, bool(session.audio_capture_enabled))
        self._set_tts_gated(session.call_id, bool(session.tts_playing))
        self._set_state_metric(session.call_id, session.conversation_state)

    async def on_tts_start(self, call_id: str, playback_id: str) -> bool:
        """Disable audio capture for the duration of a TTS playback."""
        logger.info("🔇 ConversationCoordinator gating audio", call_id=call_id, playback_id=playback_id)
        success = await self._session_store.set_gating_token(call_id, playback_id)
        if success:
            self._set_tts_gated(call_id, True)
            self._set_capture_enabled(call_id, False)
            self._barge_in_seen[call_id] = False
            if self._no_input_watchdog:
                try:
                    await self._no_input_watchdog.note_agent_output_start(call_id)
                except Exception:
                    logger.exception(
                        "Caller inactivity output-start observer failed",
                        call_id=call_id,
                    )
        return success

    async def on_tts_end(
        self,
        call_id: str,
        playback_id: str,
        reason: str = "playback-finished",
        *,
        notify_no_input: bool = True,
    ) -> bool:
        """Re-enable capture and optionally report caller-facing output completion."""
        logger.info(
            "🔊 ConversationCoordinator clearing gating",
            call_id=call_id,
            playback_id=playback_id,
            reason=reason,
        )
        success = await self._session_store.clear_gating_token(call_id, playback_id)
        if success:
            session = await self._session_store.get_by_call_id(call_id)
            tts_gated = False
            capture_enabled = True
            if session:
                tts_gated = bool(getattr(session, "tts_playing", False))
                capture_enabled = bool(getattr(session, "audio_capture_enabled", True))
            self._set_tts_gated(call_id, tts_gated)
            self._set_capture_enabled(call_id, bool(capture_enabled))
            self._barge_in_seen[call_id] = False
            if notify_no_input and self._no_input_watchdog and not tts_gated:
                try:
                    await self._no_input_watchdog.note_agent_output_end(call_id)
                except Exception:
                    logger.exception(
                        "Caller inactivity output-end observer failed",
                        call_id=call_id,
                    )
        return success

    async def cancel_tts(self, call_id: str, playback_id: str) -> None:
        """Clear gating when playback fails to start."""
        await self.on_tts_end(call_id, playback_id, reason="playback-cancelled")

    def note_audio_during_tts(self, call_id: str) -> None:
        """Record a barge-in attempt if audio arrives while TTS plays."""
        if call_id not in self._barge_in_seen:
            self._barge_in_seen[call_id] = False
            self._barge_in_totals.setdefault(call_id, 0)
        if not self._barge_in_seen[call_id]:
            logger.debug("🎧 Barge-in attempt detected", call_id=call_id)
            _BARGE_IN_COUNTER.inc()
            self._barge_in_totals[call_id] = self._barge_in_totals.get(call_id, 0) + 1
            self._barge_in_seen[call_id] = True

    async def update_conversation_state(self, call_id: str, state: str) -> None:
        """Update session conversation state and reflect it in gauges."""
        if state not in _CONVERSATION_STATES:
            logger.debug("Unknown conversation state requested", call_id=call_id, state=state)
            return
        session = await self._session_store.get_by_call_id(call_id)
        if not session:
            return
        if session.conversation_state == state:
            return
        session.conversation_state = state
        await self._session_store.upsert_call(session)
        self._set_state_metric(call_id, state)

    def get_pending_timer_count(self) -> int:
        """Return the number of pending fallback timers."""
        return len([t for t in self._capture_fallback_tasks.values() if not t.done()])

    async def schedule_capture_fallback(self, call_id: str, delay: float) -> None:
        """Ensure audio capture is eventually re-enabled after a delay."""
        logger.info(
            "[TIMER] Scheduled: action=capture_fallback",
            call_id=call_id,
            delay_seconds=delay,
            pending_timers=self.get_pending_timer_count() + 1,
        )
        await self._cancel_capture_fallback(call_id)

        async def _task():
            try:
                await asyncio.sleep(delay)
                session = await self._session_store.get_by_call_id(call_id)
                if not session:
                    return
                if session.audio_capture_enabled:
                    return
                session.audio_capture_enabled = True
                await self._session_store.upsert_call(session)
                self._set_capture_enabled(call_id, True)
                logger.info(
                    "[TIMER] Executed: action=capture_fallback",
                    call_id=call_id,
                    result="capture_re_enabled",
                )
            except asyncio.CancelledError:
                logger.info(
                    "[TIMER] Cancelled: action=capture_fallback",
                    call_id=call_id,
                    reason="task_cancelled",
                )
            except Exception:  # pragma: no cover - defensive logging
                logger.exception("ConversationCoordinator capture fallback failed", call_id=call_id)

        task = asyncio.create_task(_task())
        self._capture_fallback_tasks[call_id] = task

    async def get_summary(self) -> Dict[str, Optional[int]]:
        """Summarise conversation metrics for health reporting."""
        sessions = await self._session_store.get_all_sessions()
        gating_active = sum(1 for session in sessions if session.tts_playing)
        capture_disabled = sum(1 for session in sessions if not session.audio_capture_enabled)
        barge_in_total = sum(self._barge_in_totals.values())
        return {
            "gating_active": gating_active,
            "capture_disabled": capture_disabled,
            "barge_in_total": barge_in_total,
        }

    async def _cancel_capture_fallback(self, call_id: str) -> None:
        task = self._capture_fallback_tasks.pop(call_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _set_state_metric(self, call_id: str, state: str) -> None:
        if state not in _CONVERSATION_STATES:
            state = "listening"
        self._state_by_call[call_id] = state
        self._refresh_metrics()

    def _set_tts_gated(self, call_id: str, gated: bool) -> None:
        if gated:
            self._gated_calls.add(call_id)
        else:
            self._gated_calls.discard(call_id)
        self._refresh_metrics()

    def _set_capture_enabled(self, call_id: str, enabled: bool) -> None:
        if enabled:
            self._capture_enabled_calls.add(call_id)
        else:
            self._capture_enabled_calls.discard(call_id)
        self._refresh_metrics()

    def _refresh_metrics(self) -> None:
        _TTS_GATING_GAUGE.set(len(self._gated_calls))
        _AUDIO_CAPTURE_GAUGE.set(len(self._capture_enabled_calls))
        for state in _CONVERSATION_STATES:
            count = sum(1 for v in self._state_by_call.values() if v == state)
            _CONVERSATION_STATE_GAUGE.labels(state).set(count)

__all__ = ["ConversationCoordinator"]
