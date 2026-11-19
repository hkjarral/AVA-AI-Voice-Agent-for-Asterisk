"""Phase adapters for native Pushover; no telephony ownership or CRM writes."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from dataclasses import dataclass, field

from src.integrations.pushover import (
    PushoverToolConfig,
    delivery_outcome,
    read_key,
    get_sender,
    render_message,
)
from src.tools.base import Tool, ToolCategory, ToolDefinition, ToolPhase
from src.tools.summary import generate_summary, snapshot_history


@dataclass
class NotificationState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    events: dict = field(default_factory=dict)
    briefings: dict = field(default_factory=dict)


def verified_meeting(tool_calls):
    """Use successful current-call calendar writes, never conversational claims."""
    active = {}
    for call in tool_calls:
        if (
            call.get("name") not in {"google_calendar", "microsoft_calendar"}
            or call.get("status") != "success"
        ):
            continue
        action = call.get("action")
        event = call.get("target_id")
        if action == "delete_event":
            if event:
                active.pop(event, None)
            else:
                active.clear()
        elif action in {"create_event", "reschedule_event"}:
            params = call.get("params") or {}
            start, end = params.get("start_datetime"), params.get("end_datetime")
            if (
                event
                and start
                and end
                and (action == "create_event" or event in active)
            ):
                active[event] = f"{start} to {end}"
    return next(reversed(active.values()), "unknown") if active else "unknown"


def notification_values(
    context, *, phase, enrichment=None, metadata=None, destination="", outcome=""
):
    enrichment = enrichment or {}
    metadata = metadata or {}
    # Corrections affect presentation only; never routing, consent or CRM authority.
    merged = {**enrichment, **metadata}
    corrected = (
        metadata.get("customer_name")
        if metadata.get("customer_name_source") == "caller_confirmed"
        else None
    ) or metadata.get("caller_confirmed_name")
    crm = (
        (enrichment.get("customer_name") or enrichment.get("contact_name"))
        if enrichment.get("match_status") in {None, "matched"}
        else None
    )
    caller_id = str(context.caller_name or "").strip()
    if (
        caller_id.lower()
        in {
            "unknown",
            "anonymous",
            "unavailable",
            "restricted",
            "private",
            "wireless caller",
            "out of area",
        }
        or caller_id == context.caller_number
    ):
        caller_id = ""
    name = corrected or crm or caller_id or "unknown"
    source = (
        "caller-confirmed"
        if corrected
        else "CRM" if crm else "caller ID" if caller_id else "unknown"
    )
    return {
        "agent": str(context.context_name or "AVA")[:100],
        "context_name": str(context.context_name or "AVA")[:100],
        "caller_name": str(name)[:100],
        "called_number": str(getattr(context, "called_number", None) or "unknown")[:64],
        "call_outcome": outcome or "pending",
        "call_duration": str(getattr(context, "call_duration_seconds", 0)),
        "stage": {
            "pre_call": "arrival",
            "in_call": "in-call update",
            "post_call": "completion",
        }[phase],
        "call_id": str(context.call_id)[:128],
        "caller_number": str(context.caller_number or "unknown")[:64],
        "callback_number": str(merged.get("callback_number") or "unknown")[:64],
        "name": str(name)[:100],
        "identity_source": source,
        "company": str(
            merged.get("company")
            or merged.get("company_name")
            or merged.get("customer_company")
            or "unknown"
        )[:100],
        "crm_status": str(
            enrichment.get("crm_status") or enrichment.get("match_status") or "unknown"
        )[:100],
        "destination": str(destination or "unknown")[:100],
        "outcome": outcome or "pending",
        "language": str(merged.get("language") or "unknown")[:64],
        # No inference of booked meetings from conversational intent.
        "meeting": "unknown",
        "summary": "",
        "summary_status": "disabled",
    }


class PushoverTool(Tool):
    """A named managed tool selected through AVA's existing phase/Agent policy."""

    def __init__(self, name, config, *, sender=None):
        self.config = PushoverToolConfig.model_validate(config)
        self.sender = sender or get_sender()
        self.phase = self.config.phase
        self._definition = ToolDefinition(
            name=name,
            description=self.config.description,
            category=ToolCategory.BUSINESS,
            phase=ToolPhase(self.phase),
            is_global=self.config.is_global,
            parameters=[],
            timeout_ms=self.config.timeout_ms
            + (self.config.summary_timeout_ms if self.config.generate_summary else 0)
            + 250,
        )

    @property
    def definition(self):
        return self._definition

    async def execute(self, *args):
        params, context = args if len(args) == 2 else ({}, args[0])
        result = {
            "status": "skipped",
            "internal_notification": True,
            "notification_service": "pushover",
            "delivery_status": "skipped",
            "message": "",
        }
        try:
            result = await asyncio.wait_for(
                self._execute(params, context, result),
                self.definition.timeout_ms / 1000,
            )
        except asyncio.CancelledError:
            result.update(status="cancelled", error_code="cancelled")
            raise
        except asyncio.TimeoutError:
            result.update(status="timeout")
            result.setdefault("error_code", "timeout")
        except Exception:
            result.update(status="error", error_code="notification_unavailable")
        finally:
            if hasattr(context, "execution_results"):
                context.execution_results[self.definition.name] = result
        return {} if self.phase == "pre_call" else result

    async def _execute(self, params, context, progress):
        base = {
            "status": "skipped",
            "internal_notification": True,
            "notification_service": "pushover",
            "message": "",
        }
        if not self.config.enabled:
            return {**base, "delivery_status": "skipped"}
        if params:
            return {
                **base,
                "status": "error",
                "delivery_status": "rejected",
                "error_code": "unexpected_parameters",
            }
        owner = await context.get_session() if self.phase == "in_call" else context
        if self.phase == "in_call" and (
            getattr(owner, "cleanup_in_progress", False)
            or getattr(owner, "cleanup_completed", False)
        ):
            return {**base, "delivery_status": "skipped"}
        state = getattr(owner, "notification_state", None)
        if state is None:
            state = NotificationState()
            owner.notification_state = state
        history = (
            await snapshot_history(context)
            if self.phase == "in_call"
            else copy.deepcopy(getattr(context, "conversation_history", []) or [])
        )
        enrichment = copy.deepcopy(getattr(owner, "pre_call_results", {}) or {})
        metadata = copy.deepcopy(getattr(owner, "call_metadata", {}) or {})
        tool_calls = list(getattr(owner, "tool_calls", []) or [])
        attempts = sum(
            1
            for call in tool_calls
            if call.get("name")
            in {"blind_transfer", "attended_transfer", "live_agent_transfer"}
        )
        last_user = next(
            (
                m.get("content", "")
                for m in reversed(history)
                if m.get("role") == "user"
            ),
            "",
        )
        event = hashlib.sha256(
            json.dumps(
                [
                    self.definition.name,
                    self.phase,
                    attempts,
                    sum(1 for m in history if m.get("role") == "user"),
                    last_user,
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        destination = (
            (getattr(context, "transfer_destination", None) or "unknown")
            if self.phase == "post_call"
            else self.config.destination_key
        )
        outcome = (
            getattr(context, "call_outcome", "")
            if self.phase == "post_call"
            else "pending"
        )
        values = notification_values(
            context,
            phase=self.phase,
            enrichment=enrichment,
            metadata=metadata,
            destination=destination,
            outcome=outcome,
        )
        values["meeting"] = verified_meeting(tool_calls)[:160]
        recipients = self.config.recipients
        deliveries = {
            key: {"label": entry.label, "delivery_status": "disabled"}
            for key, entry in recipients.items()
            if not entry.enabled
        }
        enabled = {key: entry for key, entry in recipients.items() if entry.enabled}
        async with state.lock:
            if event in state.events:
                return {**base, **state.events[event], "deduplicated": True}
            if len(state.events) >= 64:
                return {
                    **base,
                    "delivery_status": "capacity",
                    "error_code": "call_event_limit",
                }
            state.events[event] = {"status": "skipped", "delivery_status": "pending"}
        progress.update(
            base, delivery_status="skipped", notification_deliveries=deliveries
        )
        duplicates = {}
        try:
            # Resolve credential identities privately to avoid duplicate sends under
            # different row names and suppress completion only for matching recipients.
            token = await read_key(self.config.token_file)
            keys = await asyncio.gather(
                *(read_key(entry.key_file) for entry in enabled.values())
            )
            identities = {
                key: (
                    hashlib.sha256((token + ":" + secret).encode()).hexdigest()
                    if token and secret
                    else None
                )
                for key, secret in zip(enabled, keys)
            }
            prior = state.briefings.get(self.config.suppress_after_tool) or {}
            targets = {}
            duplicates = {}
            owners = {}
            for key, entry in enabled.items():
                identity = identities[key]
                briefing = prior.get(identity) if identity else None
                if (
                    self.phase == "post_call"
                    and outcome == "transferred"
                    and briefing
                    and destination in briefing["aliases"]
                    and attempts == briefing["attempts"] + 1
                ):
                    deliveries[key] = {
                        "label": entry.label,
                        "delivery_status": "suppressed",
                    }
                elif identity and identity in owners:
                    duplicates[key] = owners[identity]
                else:
                    targets[key] = entry
                    if identity:
                        owners[identity] = key
            if targets and self.config.generate_summary:
                generator = getattr(context, "summary_generator", None) or getattr(
                    getattr(context, "tool_registry", None), "summary_generator", None
                )
                summary_values, diagnostics = await generate_summary(
                    self.config,
                    generator,
                    call_id=context.call_id,
                    history=history,
                    values=values,
                )
                values.update(
                    {key: summary_values[key] for key in ("summary", "summary_status")}
                )
                progress.update(diagnostics)
                if (
                    values["summary_status"] != "ok"
                    and self.config.summary_failure_policy == "abort"
                ):
                    progress["error_code"] = "summary_unavailable"
                    for key, entry in targets.items():
                        deliveries[key] = {
                            "label": entry.label,
                            "delivery_status": "skipped",
                        }
                    return progress
            if self.phase == "in_call":
                live = await context.get_session()
                if getattr(live, "cleanup_in_progress", False) or getattr(
                    live, "cleanup_completed", False
                ):
                    return progress
            message = render_message(self.config, self.phase, values)

            async def send_one(key, entry):
                # Conservative reservation before entering a cancellable sender.
                deliveries[key] = {"label": entry.label, "delivery_status": "uncertain"}
                try:
                    sent = await self.sender.send(
                        token_file=self.config.token_file,
                        key_file=entry.key_file,
                        message=message,
                        timeout_ms=self.config.timeout_ms,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    sent = {
                        "delivery_status": "uncertain",
                        "error_code": "transport_unavailable",
                    }
                deliveries[key] = {"label": entry.label, **sent}
                if (
                    self.phase == "in_call"
                    and sent["delivery_status"] == "accepted"
                    and identities[key]
                ):
                    destinations = (
                        context.get_config_value("tools.transfer.destinations", {})
                        or {}
                    )
                    target = destinations.get(self.config.destination_key) or {}
                    aliases = [self.config.destination_key] + [
                        str(target.get(k) or "")
                        for k in ("description", "target", "name")
                    ]
                    async with state.lock:
                        state.briefings.setdefault(self.definition.name, {})[
                            identities[key]
                        ] = {"aliases": [a for a in aliases if a], "attempts": attempts}

            await asyncio.gather(
                *(send_one(key, entry) for key, entry in targets.items())
            )
            for key, original in duplicates.items():
                deliveries[key] = {
                    **deliveries[original],
                    "label": enabled[key].label,
                    "deduplicated": True,
                }
            progress.update(delivery_outcome(deliveries))
            return progress
        except asyncio.CancelledError:
            # Keep accepted rows and uncertain in-flight sends; never blindly replay.
            if deliveries:
                progress.update(delivery_outcome(deliveries))
            raise
        finally:
            for key, entry in enabled.items():
                if key not in deliveries:
                    original = duplicates.get(key)
                    deliveries[key] = (
                        {
                            **deliveries[original],
                            "label": entry.label,
                            "deduplicated": True,
                        }
                        if original in deliveries
                        else {"label": entry.label, "delivery_status": "skipped"}
                    )
            progress.update(delivery_outcome(deliveries))
            async with state.lock:
                state.events[event] = copy.deepcopy(progress)
