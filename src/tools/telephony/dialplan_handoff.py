"""Shared guarded ARI handoff. Audio sequencing belongs to the engine."""
from __future__ import annotations
from typing import Any, Dict, Optional, Callable, Awaitable
import structlog
from src.tools.context import ToolExecutionContext
logger = structlog.get_logger(__name__)

async def continue_to_dialplan(*, context: ToolExecutionContext, target: str,
    description: str, transfer_type: str, dialplan_context: str, transfer_state: str,
    priority: int = 1, require_valid_target: bool = False,
    claim_ownership: Optional[Callable[[dict], Awaitable[bool]]] = None,
    release_ownership: Optional[Callable[[dict], Awaitable[bool]]] = None,
    before_dispatch: Optional[Callable[[], Awaitable[None]]] = None) -> Dict[str, Any]:
    target_exists: Optional[bool] = None
    validator = getattr(context.ari_client, "dialplan_target_exists", None)
    if callable(validator):
        try:
            target_exists = await validator(
                context.caller_channel_id,
                context=dialplan_context,
                extension=target,
                priority=priority,
            )
        except Exception:
            logger.warning(
                "Dialplan target validation unavailable",
                call_id=context.call_id,
                transfer_type=transfer_type,
                target=target,
                context=dialplan_context,
                exc_info=True,
            )

    if target_exists is False or (require_valid_target and target_exists is not True):
        logger.error(
            "Transfer dialplan target does not exist",
            call_id=context.call_id,
            transfer_type=transfer_type,
            target=target,
            context=dialplan_context,
            priority=priority,
        )
        return {
            "status": "failed",
            "message": f"Transfer destination {description} is not available.",
            "destination": target,
            "type": transfer_type,
        }

    if target_exists is not True:
        logger.warning(
            "Dialplan target could not be pre-validated; attempting guarded transfer",
            call_id=context.call_id,
            transfer_type=transfer_type,
            target=target,
            context=dialplan_context,
            priority=priority,
        )

    session = await context.get_session()
    from src.core.session_store import SessionStore
    import uuid
    action_id = str(uuid.uuid4())
    if claim_ownership is None and isinstance(context.session_store, SessionStore):
        claim_ownership = lambda changes: context.session_store.claim_dialplan_handoff(session, changes, action_id)
        release_ownership = lambda previous: context.session_store.restore_dialplan_handoff(session, previous, action_id)
    previous_transfer_state = {
        "transfer_active": bool(getattr(session, "transfer_active", False)),
        "transfer_state": getattr(session, "transfer_state", None),
        "transfer_target": getattr(session, "transfer_target", None),
    }

    # StasisEnd can race the HTTP response. Claim transfer ownership before
    # continue so normal cleanup cannot hang up a successfully handed-off leg.
    changes = dict(transfer_active=True, transfer_state=transfer_state, transfer_target=description)
    if claim_ownership is not None:
        if not await claim_ownership(changes):
            return {"status": "failed", "ownership_lost": True}
    else:
        await context.update_session(**changes)

    if before_dispatch is not None:
        try:
            await before_dispatch()
        except Exception:
            if release_ownership is not None and not await release_ownership(previous_transfer_state):
                return {"status": "failed", "ownership_lost": True}
            return {"status": "failed", "message": "Handoff preparation failed before dispatch."}
    if isinstance(context.session_store, SessionStore):
        current = await context.session_store.get_by_call_id(context.call_id)
        if (current is not session or session.cleanup_in_progress or session.cleanup_completed
                or not getattr(session, "transfer_active", False)
                or getattr(session, "transfer_state", None) != transfer_state):
            return {"status": "failed", "ownership_lost": True}
    try:
        handed_off = await context.ari_client.continue_in_dialplan(
            context.caller_channel_id,
            context=dialplan_context,
            extension=target,
            priority=priority,
        )
    except Exception:
        handed_off = None
        logger.warning(
            "Transfer dialplan handoff raised",
            call_id=context.call_id,
            transfer_type=transfer_type,
            target=target,
            context=dialplan_context,
            exc_info=True,
        )

    handoff_indeterminate = handed_off is not True and handed_off is not False

    if handed_off is True:
        logger.info(
            "Dialplan transfer initiated",
            call_id=context.call_id,
            transfer_type=transfer_type,
            target=target,
            context=dialplan_context,
        )
        return {
            "status": "success",
            "message": f"Transferring you to {description} now.",
            "destination": target,
            "type": transfer_type,
        }

    # Only an explicit rejection proves the channel remained under AAVA
    # ownership. An indeterminate response may have followed an accepted
    # handoff, so retain the transfer guard and avoid destructive cleanup.
    restore_ownership = handed_off is False
    if restore_ownership:
        try:
            if release_ownership is not None:
                if not await release_ownership(previous_transfer_state):
                    return {"status": "failed", "ownership_lost": True}
            else:
                await context.update_session(**previous_transfer_state)
        except Exception:
            logger.error(
                "Failed to restore transfer ownership after unconfirmed handoff",
                call_id=context.call_id,
                transfer_type=transfer_type,
                target=target,
                context=dialplan_context,
                exc_info=True,
            )
    else:
        logger.warning(
            "Transfer handoff outcome indeterminate; retaining transfer ownership",
            call_id=context.call_id,
            transfer_type=transfer_type,
            target=target,
            context=dialplan_context,
        )

    logger.error(
        "Transfer dialplan handoff was not confirmed",
        call_id=context.call_id,
        transfer_type=transfer_type,
        target=target,
        context=dialplan_context,
        handoff_indeterminate=handoff_indeterminate,
        ownership_restored=restore_ownership,
    )
    result = {
        "status": "failed",
        "message": f"Unable to transfer to {description}.",
        "destination": target,
        "type": transfer_type,
    }
    if handoff_indeterminate:
        result["handoff_indeterminate"] = True
    return result
