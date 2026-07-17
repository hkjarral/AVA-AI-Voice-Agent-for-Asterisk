"""Idempotent v7.4 starter-agent provisioning for genuinely empty installs."""

from __future__ import annotations

import json
from typing import Mapping, Optional

from agents_store import AgentsStore


def seed_starter_agents(
    store: AgentsStore,
    *,
    provider: str,
    pipeline: Optional[str] = None,
    assistant_name: str = "AVA",
    assistant_role: str = "voice assistant",
    receptionist_greeting: Optional[str] = None,
    legacy_contexts: Optional[Mapping] = None,
) -> dict:
    """Create Receptionist, Sales, Support exactly once when the store is empty."""
    if store.list_all():
        return {"created": [], "already_configured": True}
    if legacy_contexts:
        return {
            "created": [],
            "already_configured": False,
            "legacy_contexts_pending": True,
        }

    name = (assistant_name or "AVA").strip()
    role = (assistant_role or "voice assistant").strip()
    # A pipeline remains the selected execution path, while a compatible full-
    # agent provider gives early transport/audio-profile resolution the same
    # capabilities that legacy pipeline Contexts supplied (for example, local +
    # local_hybrid). Callers leave this blank when no compatible provider exists.
    effective_provider = (provider or "").strip()
    extra_json = json.dumps({"pipeline": pipeline}) if pipeline else None
    tools_json = json.dumps(["hangup_call"])

    definitions = [
        {
            "slug": "receptionist",
            "display_name": "Receptionist",
            "role_label": "General reception",
            "greeting": receptionist_greeting or "Thank you for calling. How can I help you today?",
            "prompt": (
                f"You are {name}, the general receptionist and first point of contact. "
                "Understand why the caller is calling, answer only from information available to you, "
                "capture accurate contact details or a message when needed, and guide the caller to the "
                "right next step. Never invent company policies, hours, prices, or transfer targets. "
                "Use a transfer tool only when it exposes an explicitly configured destination. "
                f"Maintain the professional, concise manner expected of a {role}."
            ),
        },
        {
            "slug": "sales",
            "display_name": "Sales",
            "role_label": "Sales inquiries",
            "greeting": "Thanks for calling Sales. What can I help you find today?",
            "prompt": (
                f"You are {name}, a general sales assistant. Discover the caller's needs, answer from "
                "the product or service information available to you, clarify priorities and timing "
                "without being pushy, and capture details for an appropriate follow-up. Never invent "
                "pricing, availability, discounts, commitments, or company policy. Use transfer tools "
                "only for destinations explicitly available to this agent. Keep responses concise and conversational."
            ),
        },
        {
            "slug": "support",
            "display_name": "Support",
            "role_label": "Support triage",
            "greeting": "Thanks for calling Support. Please tell me what is happening.",
            "prompt": (
                f"You are {name}, a general support triage assistant. Identify the affected product or "
                "service, the symptoms, business impact, and troubleshooting already attempted. Offer "
                "only safe, reversible steps supported by the information available to you. Never invent "
                "technical procedures, account data, warranties, or resolution times. Summarize the issue "
                "clearly for escalation and use transfer tools only for explicitly configured destinations."
            ),
        },
    ]

    created = []
    try:
        for definition in definitions:
            row = store.create(
                provider=effective_provider,
                prompt=definition.pop("prompt"),
                greeting=definition.pop("greeting"),
                tools_json=tools_json,
                extra_json=extra_json,
                notes="Created by the v7.4 starter-agent setup",
                **definition,
            )
            created.append(row["slug"])
    except Exception:
        for slug in reversed(created):
            store.delete(slug)
        raise

    store.set_default("receptionist")
    return {"created": created, "default_slug": "receptionist", "already_configured": False}
