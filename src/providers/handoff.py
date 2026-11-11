"""Normalize emitted speech into bounded announcement evidence for all adapters.

Ordinary provider events are unchanged until an optional handoff is prepared.
Provider-native playback/response IDs remain untouched.
"""
from __future__ import annotations

import time
import uuid
from collections import OrderedDict


class HandoffEvidence:
    def __init__(self):
        self.calls = {}
        # Per-call flags for unannounced deferred transfers: whether a result
        # was delivered, and whether provider VAD reports the caller talking.
        self.deferred = {}

    def reset(self, call_id):
        self.calls.pop(call_id, None)
        self.deferred.pop(call_id, None)

    def deferred_flags(self, call_id):
        return self.deferred.setdefault(call_id, {"result_sent": False, "caller_speaking": False})

    def state(self, call_id):
        return self.calls.setdefault(call_id, {"turns": OrderedDict(), "current": None, "active": None})

    def turn(self, call_id, native_id=None):
        state = self.state(call_id)
        key = str(native_id) if native_id is not None else state["current"]
        if key is None:
            key = uuid.uuid4().hex
        if key not in state["turns"]:
            state["turns"][key] = {"id": key, "started_at": None, "completed": False, "interrupted": False, "text": ""}
            while len(state["turns"]) > 8:
                state["turns"].popitem(last=False)
        if native_id is None and state["current"] is None:
            state["current"] = key
        return state["turns"][key]

    def transcript(self, call_id, text, native_id=None):
        turn = self.turn(call_id, native_id)
        turn["text"] = text
        if not self.state(call_id)["current"] and not turn["completed"]:
            self.state(call_id)["current"] = turn["id"]
        return dict(turn)

    def prepare(self, call_id, action, native_id=None):
        state = self.state(call_id)
        # Repeated tool results for one action must not reset identity/deadlines.
        if state["active"] == action["id"]:
            origin_id = state.get("origin_id")
            return dict(self.turn(call_id, origin_id))
        turn = self.turn(call_id, native_id)
        if turn.get("owner_action_id") not in {None, action["id"]}:
            # An old response must never become evidence for a replacement.
            turn = self.turn(call_id, uuid.uuid4().hex)
        else:
            state["current"] = turn["id"] if turn["started_at"] is not None and not turn["completed"] else None
        turn["owner_action_id"] = action["id"]
        state["active"] = action["id"]
        state["origin_id"] = turn["id"]
        return dict(turn)

    def event(self, event, native_id=None):
        call_id = event.get("call_id")
        if not call_id:
            return event
        state = self.state(call_id)
        kind = event.get("type")
        if event.get("handoff_evidence") is not None:
            return {**event, "handoff_action_id": event["handoff_evidence"].get("owner_action_id") or state["active"]} if state["active"] else event
        if kind in {"ToolCall", "function_call", "tool_call"}:
            state["tool_origin"] = self.turn(call_id, native_id)["id"]
        evidence = None
        native_id = event.get("audio_response_id") or native_id
        if kind == "AgentAudio" and event.get("data") and event.get("_handoff_real_audio", True):
            evidence = self.turn(call_id, native_id)
            state["current"] = evidence["id"]
            if evidence["started_at"] is None:
                evidence["started_at"] = time.monotonic()
                if state["active"]:
                    evidence.setdefault("owner_action_id", state["active"])
        elif kind in {"agent_transcript", "HandoffTranscript"} or (kind == "ConversationText" and event.get("role") == "assistant"):
            evidence = self.transcript(call_id, str(event.get("text") or event.get("content") or ""), native_id)
        elif kind in {"ProviderBargeIn", "interruption"}:
            evidence = self.turn(call_id, native_id)
            evidence["interrupted"] = True
        elif kind in {"AgentAudioDone", "HandoffAudioComplete", "GoogleAudioGenerationComplete"}:
            if native_id is None and state["current"] is None:
                return event
            evidence = self.turn(call_id, native_id)
            reason = event.get("reason", "")
            fallback = reason in {"audio_idle_fallback", "fallback_audio_idle", "fallback_no_audio", "duration_estimate", "stop_session", "interruption"}
            if reason in {"interruption", "stop_session"}:
                evidence["interrupted"] = True
            if kind != "GoogleAudioGenerationComplete" and not fallback and event.get("_handoff_complete", True):
                evidence["completed"] = True
            if state["current"] == evidence["id"] and (evidence["completed"] or kind == "GoogleAudioGenerationComplete"):
                state["current"] = None
        if state["active"] and evidence is not None:
            return {**event, "handoff_action_id": evidence.get("owner_action_id") or state["active"], "handoff_evidence": dict(evidence)}
        return event


class EventCallback:
    """Preserve callback instrumentation while adding internal evidence."""
    def __init__(self, wrapped, target):
        self.wrapped = wrapped
        self.target = target

    async def __call__(self, event):
        return await self.wrapped(event)

    def __getattr__(self, name):
        return getattr(self.target, name)
