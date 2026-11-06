"""Helpers for transfer actions that should run after caller-facing audio."""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional

import structlog

from src.tools.context import ToolExecutionContext

logger = structlog.get_logger(__name__)

DEFERRED_TRANSFER_RESULT_KEY = "deferred_transfer"


def transfer_deferral_enabled(context: ToolExecutionContext) -> bool:
    transfer_cfg = context.get_config_value("tools.transfer") or {}
    if not isinstance(transfer_cfg, dict):
        return True
    return bool(transfer_cfg.get("defer_until_playback_complete", True))


def build_deferred_transfer_action(
    *,
    source_tool: str,
    commit_tool: str,
    transfer_type: str,
    target: str,
    description: str,
    dialplan_context: Optional[str] = None,
    destination_key: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    action = {
        "id": str(uuid.uuid4()),
        "kind": "transfer",
        "source_tool": source_tool,
        "commit_tool": commit_tool,
        "transfer_type": transfer_type,
        "target": str(target or ""),
        "description": str(description or target or ""),
        "dialplan_context": str(dialplan_context or "").strip(),
        "destination_key": str(destination_key or "").strip(),
        "created_at": time.time(),
    }
    if payload:
        action["payload"] = dict(payload)
    return action


async def store_pending_deferred_transfer(
    context: ToolExecutionContext,
    action: Dict[str, Any],
) -> None:
    session = await context.get_session()
    from src.tools.handoff_config import resolve_handoff, normalize_local_handoff_providers
    from src.config.provider_instances import provider_kind
    transfer = context.get_config_value("tools.transfer") or {}
    destination = (transfer.get("destinations") or {}).get(action.get("destination_key")) or {}
    if action.get("source_tool") == "live_agent_transfer":
        destination = (context.get_config_value("tools.extensions.internal") or {}).get(action.get("target")) or destination
    handoff = resolve_handoff(transfer, destination)
    if handoff:
        payload = action.get("payload") or {}
        if payload.get("screening_mode") == "caller_recording":
            prompt = str(payload.get("caller_screening_prompt") or "").strip()
            if prompt:
                handoff["message"] = f"{handoff['message']} {prompt}"
        key = getattr(context, "provider_name", None) or ""
        kind = provider_kind(key, (context.get_config_value("providers") or {}).get(key) or {}) or key
        local = normalize_local_handoff_providers(transfer.get("local_handoff_audio_providers", ["deepgram"]))
        handoff["owner"] = "engine" if local and kind in local and not session.pipeline_name else "provider"
        action["handoff"] = handoff
    session.pending_deferred_transfer = dict(action)
    await context.session_store.upsert_call(session)
    logger.info(
        "Deferred transfer armed",
        call_id=context.call_id,
        action_id=action.get("id"),
        source_tool=action.get("source_tool"),
        commit_tool=action.get("commit_tool"),
        transfer_type=action.get("transfer_type"),
        target=action.get("target"),
        dialplan_context=action.get("dialplan_context"),
    )


def build_deferred_transfer_result(
    *,
    action: Dict[str, Any],
    message: str,
    status: str = "success",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    handoff = action.get("handoff") or {}
    if handoff:
        message = handoff["message"]
    result = {
        "status": status,
        "message": message,
        "defer_until_playback_complete": True,
        # Runtime ownership metadata is private to the engine and must not be
        # echoed into provider-visible tool results.
        DEFERRED_TRANSFER_RESULT_KEY: {
            key: value
            for key, value in action.items()
            if not str(key).startswith("_")
        },
    }
    if handoff.get("owner") == "engine":
        result["message"] = "The transfer announcement is played by the engine. Do not speak again."
        result["ai_should_speak"] = False
    if extra:
        result.update(extra)
    return result


def get_deferred_transfer_action(result: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(result, dict):
        return None
    action = result.get(DEFERRED_TRANSFER_RESULT_KEY)
    if isinstance(action, dict) and action.get("kind") == "transfer":
        return action
    return None


async def commit_deferred_transfer_action(
    action: Dict[str, Any],
    context: ToolExecutionContext,
) -> Dict[str, Any]:
    from src.tools.registry import tool_registry as global_tool_registry

    commit_tool = str(action.get("commit_tool") or "").strip()
    tool_registry = context.tool_registry or global_tool_registry
    tool = tool_registry.get(commit_tool) if commit_tool else None
    if not tool or not hasattr(tool, "commit_deferred_action"):
        message = f"Deferred transfer commit tool unavailable: {commit_tool or 'unknown'}"
        logger.error(
            message,
            call_id=context.call_id,
            action={key: value for key, value in action.items() if not str(key).startswith("_")},
        )
        return {"status": "error", "message": message}

    return await tool.commit_deferred_action(action, context)


async def commit_pending_deferred_transfer(
    context: ToolExecutionContext,
) -> Optional[Dict[str, Any]]:
    session = await context.session_store.get_by_call_id(context.call_id)
    if not session:
        logger.warning("Cannot commit deferred transfer - session missing", call_id=context.call_id)
        return None

    action = getattr(session, "pending_deferred_transfer", None)
    if not isinstance(action, dict):
        return None

    try:
        logger.info(
            "Committing deferred transfer",
            call_id=context.call_id,
            action_id=action.get("id"),
            source_tool=action.get("source_tool"),
            commit_tool=action.get("commit_tool"),
            transfer_type=action.get("transfer_type"),
        )
        result = await commit_deferred_transfer_action(action, context)
        if isinstance(result, dict) and result.get("status") == "success":
            latest = await context.session_store.get_by_call_id(context.call_id)
            if latest:
                pending = getattr(latest, "pending_deferred_transfer", None)
                if isinstance(pending, dict) and pending.get("id") == action.get("id"):
                    latest.pending_deferred_transfer = None
                    await context.session_store.upsert_call(latest)
        else:
            logger.warning(
                "Deferred transfer commit did not succeed; keeping pending action",
                call_id=context.call_id,
                action_id=action.get("id"),
                result=result,
            )
        return result
    except Exception as exc:
        # The action remains pending unless the tool deliberately cleared or
        # replaced it. Never resurrect stale ownership after an awaited commit.
        logger.error(
            "Deferred transfer commit failed",
            call_id=context.call_id,
            action_id=action.get("id"),
            error=str(exc),
            exc_info=True,
        )
        return {"status": "error", "message": str(exc)}
