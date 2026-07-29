"""Provider-independent caller inactivity watchdog.

The watchdog owns timing only.  The engine supplies provider/pipeline-aware
callbacks for speaking an announcement and hanging up the caller channel.
Keeping those concerns separate makes the timer deterministic and easy to test.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

import structlog
from prometheus_client import Counter, Gauge

logger = structlog.get_logger(__name__)

_NO_INPUT_EVENTS = Counter(
    "ai_agent_no_input_events_total",
    "Caller inactivity watchdog events",
    labelnames=("action",),
)
_NO_INPUT_ACTIVE = Gauge(
    "ai_agent_no_input_watchdogs_active",
    "Number of active caller inactivity watchdogs",
)

AnnouncementCallback = Callable[[str, str, str], Awaitable[bool]]
HangupCallback = Callable[[str], Awaitable[None]]
PauseCallback = Callable[[str], Awaitable[bool]]

_DEFAULT_CHECK_IN_MESSAGE = "Are you still there?"
_DEFAULT_FINAL_MESSAGE = "I still can't hear you, so I'll end the call now. Goodbye."


def _coerce_bool(value: Any, default: bool) -> bool:
    """Coerce common YAML/JSON boolean forms without truthy-string surprises."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default


def _coerce_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    """Return a finite in-range float or the policy default."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed) or parsed < minimum or parsed > maximum:
        return default
    return parsed


def _coerce_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Return an integral in-range value or the policy default."""
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed) or not parsed.is_integer():
        return default
    result = int(parsed)
    return result if minimum <= result <= maximum else default


def _coerce_message(value: Any, default: str) -> str:
    """Return a non-empty bounded announcement or its safe default."""
    if value is None:
        return default
    message = str(value).strip()
    return message if 0 < len(message) <= 500 else default


@dataclass(frozen=True)
class NoInputPolicy:
    enabled: bool = True
    inbound_enabled: bool = True
    outbound_enabled: bool = False
    initial_timeout_sec: float = 30.0
    grace_timeout_sec: float = 15.0
    max_check_ins: int = 1
    check_in_message: str = _DEFAULT_CHECK_IN_MESSAGE
    final_message: str = _DEFAULT_FINAL_MESSAGE

    @classmethod
    def from_mapping(cls, value: Optional[Mapping[str, Any]]) -> "NoInputPolicy":
        """Build a safe policy from merged global and per-agent values."""
        raw = dict(value or {})
        return cls(
            enabled=_coerce_bool(raw.get("enabled", True), True),
            inbound_enabled=_coerce_bool(raw.get("inbound_enabled", True), True),
            outbound_enabled=_coerce_bool(raw.get("outbound_enabled", False), False),
            initial_timeout_sec=_coerce_float(raw.get("initial_timeout_sec", 30.0), 30.0, 1.0, 3600.0),
            grace_timeout_sec=_coerce_float(raw.get("grace_timeout_sec", 15.0), 15.0, 1.0, 3600.0),
            max_check_ins=_coerce_int(raw.get("max_check_ins", 1), 1, 0, 10),
            check_in_message=_coerce_message(raw.get("check_in_message"), _DEFAULT_CHECK_IN_MESSAGE),
            final_message=_coerce_message(raw.get("final_message"), _DEFAULT_FINAL_MESSAGE),
        )

    def applies_to(self, *, is_outbound: bool) -> bool:
        """Return whether this policy applies to the call direction."""
        if not self.enabled:
            return False
        return self.outbound_enabled if is_outbound else self.inbound_enabled


@dataclass
class _CallState:
    call_id: str
    policy: NoInputPolicy
    is_outbound: bool
    event: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None
    ready: bool = False
    input_active: bool = False
    output_active: bool = False
    processing: bool = False
    suspended: bool = False
    self_announcement: bool = False
    terminal: bool = False
    phase: str = "waiting"
    check_ins: int = 0
    deadline: Optional[float] = None
    output_pause_remaining: Optional[float] = None
    last_activity_at: float = field(default_factory=time.monotonic)
    last_activity_source: str = "call_start"


class NoInputWatchdog:
    """Per-call inactivity state machine using monotonic time."""

    def __init__(
        self,
        announce: AnnouncementCallback,
        hangup: HangupCallback,
        *,
        should_pause: Optional[PauseCallback] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize the watchdog with engine-owned speech and hangup callbacks."""
        self._announce = announce
        self._hangup = hangup
        self._should_pause = should_pause
        self._clock = clock
        self._states: Dict[str, _CallState] = {}

    def has_call(self, call_id: str) -> bool:
        """Return whether a call currently has a registered watchdog."""
        return call_id in self._states

    def snapshot(self, call_id: str) -> Optional[Dict[str, Any]]:
        """Return an observable copy of a call's watchdog state."""
        state = self._states.get(call_id)
        if not state:
            return None
        return {
            "ready": state.ready,
            "input_active": state.input_active,
            "output_active": state.output_active,
            "processing": state.processing,
            "suspended": state.suspended,
            "phase": state.phase,
            "check_ins": state.check_ins,
            "deadline": state.deadline,
            "last_activity_at": state.last_activity_at,
            "last_activity_source": state.last_activity_source,
        }

    async def register(
        self,
        call_id: str,
        policy: NoInputPolicy,
        *,
        is_outbound: bool,
    ) -> bool:
        """Replace any prior state and start a watchdog when policy applies."""
        await self.stop(call_id)
        if not policy.applies_to(is_outbound=is_outbound):
            logger.info(
                "Caller inactivity watchdog disabled for call",
                call_id=call_id,
                is_outbound=is_outbound,
            )
            return False
        state = _CallState(call_id=call_id, policy=policy, is_outbound=is_outbound)
        self._states[call_id] = state
        state.task = asyncio.create_task(self._run(state), name=f"no-input-{call_id}")
        _NO_INPUT_ACTIVE.inc()
        logger.info(
            "Caller inactivity watchdog registered",
            call_id=call_id,
            is_outbound=is_outbound,
            initial_timeout_sec=policy.initial_timeout_sec,
            grace_timeout_sec=policy.grace_timeout_sec,
            max_check_ins=policy.max_check_ins,
        )
        return True

    async def stop(self, call_id: str) -> None:
        """Cancel and remove a call watchdog without leaking its gauge."""
        state = self._states.pop(call_id, None)
        if not state:
            return
        task = state.task
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        _NO_INPUT_ACTIVE.dec()

    async def mark_ready(self, call_id: str) -> None:
        """Start inactivity timing after greeting/session setup completes."""
        state = self._states.get(call_id)
        if not state:
            return
        state.ready = True
        if not state.output_active and not state.processing and not state.suspended:
            self._reset_initial_deadline(state)
        self._wake(state)

    async def note_activity(self, call_id: str, source: str) -> None:
        """Reset inactivity state for authoritative caller activity."""
        state = self._states.get(call_id)
        if not state or state.terminal:
            return
        state.last_activity_at = self._clock()
        state.last_activity_source = source
        state.check_ins = 0
        state.phase = "waiting"
        if state.ready and not state.output_active and not state.processing and not state.suspended:
            self._reset_initial_deadline(state)
        else:
            state.deadline = None
        _NO_INPUT_EVENTS.labels("caller_activity").inc()
        self._wake(state)

    async def note_processing(self, call_id: str, active: bool) -> None:
        """Pause timing while the agent processes a caller turn."""
        state = self._states.get(call_id)
        if not state or state.terminal:
            return
        state.processing = bool(active)
        if active:
            state.deadline = None
        elif state.ready and not state.output_active and not state.suspended:
            self._reset_initial_deadline(state)
        self._wake(state)

    async def note_input_state(self, call_id: str, active: bool, source: str) -> None:
        """Pause timing for sustained caller speech and restart after it ends."""
        state = self._states.get(call_id)
        if not state or state.terminal:
            return
        was_active = state.input_active
        # TALK_DETECT and provider VAD end events are not guaranteed to be paired
        # with a start event.  An unmatched/duplicate end must be a no-op: resetting
        # the deadline here can indefinitely postpone the post-check-in grace timeout.
        if not active and not was_active:
            return
        state.input_active = bool(active)
        if active:
            state.last_activity_at = self._clock()
            state.last_activity_source = source
            state.check_ins = 0
            state.phase = "waiting"
            state.deadline = None
            _NO_INPUT_EVENTS.labels("caller_activity").inc()
        elif state.ready and not state.output_active and not state.processing and not state.suspended:
            self._reset_initial_deadline(state)
        self._wake(state)

    async def note_agent_output_start(self, call_id: str) -> None:
        """Pause and preserve the current deadline during agent output."""
        state = self._states.get(call_id)
        if not state or state.terminal:
            return
        if not state.output_active:
            state.output_pause_remaining = (
                max(0.0, float(state.deadline) - self._clock())
                if state.deadline is not None
                else None
            )
        state.output_active = True
        state.processing = False
        state.deadline = None
        self._wake(state)

    async def note_agent_output_end(
        self,
        call_id: str,
        *,
        reset_timer: bool = True,
        preserve_policy_state: bool = False,
    ) -> None:
        """Resume timing after agent output, optionally preserving remaining time."""
        state = self._states.get(call_id)
        if not state or state.terminal:
            return
        state.output_active = False
        state.processing = False
        if preserve_policy_state:
            state.output_pause_remaining = None
            self._wake(state)
            return
        if not state.self_announcement and reset_timer:
            state.check_ins = 0
            state.phase = "waiting"
            if state.ready and not state.suspended:
                self._reset_initial_deadline(state)
        elif not state.self_announcement and state.ready and not state.suspended:
            # Hosted full-agent platforms may synthesize a response to their own
            # silence pseudo-turn (for example ElevenLabs' "...").  That output
            # should pause AVA's caller-idle deadline while it is audible, but it
            # must not buy the hosted agent a fresh inactivity window.
            remaining = state.output_pause_remaining
            if remaining is None:
                self._reset_initial_deadline(state)
            else:
                state.deadline = self._clock() + max(0.0, remaining)
        state.output_pause_remaining = None
        self._wake(state)

    async def set_suspended(self, call_id: str, suspended: bool) -> None:
        """Suspend timing for transfer, hold, or other protected lifecycle states."""
        state = self._states.get(call_id)
        if not state or state.terminal:
            return
        state.suspended = bool(suspended)
        if state.suspended:
            state.deadline = None
        elif state.ready and not state.output_active and not state.processing:
            self._reset_initial_deadline(state)
        self._wake(state)

    def _wake(self, state: _CallState) -> None:
        """Wake the state machine after a state transition."""
        state.event.set()

    def _reset_initial_deadline(self, state: _CallState) -> None:
        """Set a fresh initial idle deadline from the monotonic clock."""
        state.deadline = self._clock() + state.policy.initial_timeout_sec

    def _can_count(self, state: _CallState) -> bool:
        """Return whether idle time may currently advance."""
        return bool(
            state.ready
            and not state.input_active
            and not state.output_active
            and not state.processing
            and not state.suspended
            and not state.terminal
        )

    async def _wait_for_change(self, state: _CallState, timeout: Optional[float] = None) -> bool:
        """Wait for a state change and report whether one beat the timeout."""
        state.event.clear()
        try:
            if timeout is None:
                await state.event.wait()
            else:
                await asyncio.wait_for(state.event.wait(), timeout=max(0.0, timeout))
            return True
        except asyncio.TimeoutError:
            return False

    async def _run(self, state: _CallState) -> None:
        """Drive check-in and terminal transitions for one call."""
        try:
            while self._states.get(state.call_id) is state and not state.terminal:
                if not self._can_count(state):
                    await self._wait_for_change(state)
                    continue

                if state.deadline is None:
                    self._reset_initial_deadline(state)
                changed = await self._wait_for_change(
                    state,
                    max(0.0, float(state.deadline or self._clock()) - self._clock()),
                )
                if changed or not self._can_count(state):
                    continue

                # Transfer/MOH state can be changed by a tool through SessionStore
                # without emitting a watchdog event. Re-check engine eligibility at
                # the deadline so a caller on hold never hears an inactivity prompt.
                if self._should_pause and await self._should_pause(state.call_id):
                    state.check_ins = 0
                    state.phase = "waiting"
                    self._reset_initial_deadline(state)
                    _NO_INPUT_EVENTS.labels("policy_paused").inc()
                    continue

                if state.phase == "grace" and state.check_ins >= state.policy.max_check_ins:
                    await self._finish_for_no_input(state)
                    continue

                if state.check_ins < state.policy.max_check_ins:
                    await self._perform_check_in(state)
                    continue

                await self._finish_for_no_input(state)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Caller inactivity watchdog failed", call_id=state.call_id, exc_info=True)
            _NO_INPUT_EVENTS.labels("watchdog_error").inc()
            if self._states.get(state.call_id) is state:
                self._states.pop(state.call_id, None)
                _NO_INPUT_ACTIVE.dec()

    async def _perform_check_in(self, state: _CallState) -> None:
        """Speak one check-in and enter the configured reply grace window."""
        activity_before = state.last_activity_at
        state.check_ins += 1
        state.phase = "announcing"
        state.deadline = None
        state.self_announcement = True
        _NO_INPUT_EVENTS.labels("check_in").inc()
        logger.info(
            "Caller inactivity check-in",
            call_id=state.call_id,
            attempt=state.check_ins,
            max_check_ins=state.policy.max_check_ins,
        )
        try:
            spoken = await self._announce(
                state.call_id,
                state.policy.check_in_message,
                "check_in",
            )
            if not spoken:
                _NO_INPUT_EVENTS.labels("announcement_failed").inc()
        except Exception:
            logger.error(
                "Caller inactivity check-in announcement failed",
                call_id=state.call_id,
                exc_info=True,
            )
            _NO_INPUT_EVENTS.labels("announcement_failed").inc()
        finally:
            state.self_announcement = False

        if state.last_activity_at > activity_before:
            state.check_ins = 0
            state.phase = "waiting"
            self._reset_initial_deadline(state)
            _NO_INPUT_EVENTS.labels("caller_resumed").inc()
            return

        state.output_active = False
        state.phase = "grace"
        state.deadline = self._clock() + state.policy.grace_timeout_sec

    async def _finish_for_no_input(self, state: _CallState) -> None:
        """Speak the terminal warning and attempt the engine-owned hangup."""
        activity_before = state.last_activity_at
        if state.policy.final_message:
            state.phase = "final_announcement"
            state.deadline = None
            state.self_announcement = True
            try:
                spoken = await self._announce(
                    state.call_id,
                    state.policy.final_message,
                    "final",
                )
                if not spoken:
                    _NO_INPUT_EVENTS.labels("announcement_failed").inc()
            except Exception:
                logger.error(
                    "Caller inactivity final announcement failed",
                    call_id=state.call_id,
                    exc_info=True,
                )
                _NO_INPUT_EVENTS.labels("announcement_failed").inc()
            finally:
                state.self_announcement = False

        # A caller who speaks during the final warning keeps the call alive.
        if state.last_activity_at > activity_before:
            state.check_ins = 0
            state.phase = "waiting"
            state.output_active = False
            self._reset_initial_deadline(state)
            _NO_INPUT_EVENTS.labels("caller_resumed").inc()
            return

        state.terminal = True
        state.phase = "hangup"
        _NO_INPUT_EVENTS.labels("hangup").inc()
        logger.info("Caller inactivity timeout reached", call_id=state.call_id)
        try:
            await self._hangup(state.call_id)
        except Exception:
            logger.error(
                "Caller inactivity hangup callback failed",
                call_id=state.call_id,
                exc_info=True,
            )
            _NO_INPUT_EVENTS.labels("watchdog_error").inc()
