"""Shared guarded ARI handoff. Audio sequencing belongs to the engine."""
from __future__ import annotations
import asyncio
from typing import Any, Dict, Optional, Callable, Awaitable
import structlog
from src.tools.context import ToolExecutionContext
logger = structlog.get_logger(__name__)

async def continue_to_dialplan(*, context: ToolExecutionContext, target: str,
    description: str, transfer_type: str, dialplan_context: str, transfer_state: str,
    priority: int = 1, require_valid_target: bool = False,
    claim_ownership: Optional[Callable[[dict], Awaitable[bool]]] = None,
    release_ownership: Optional[Callable[[dict], Awaitable[bool]]] = None,
    before_dispatch: Optional[Callable[[], Awaitable[None]]] = None,
    expected_session=None, expected_deferred_id: Optional[str] = None,
    dispatch_state: Optional[dict] = None, expected_completion_action: Optional[dict] = None,
    on_dispatch_started: Optional[Callable[[], None]] = None) -> Dict[str, Any]:
    target = str(target).strip()
    # Bind identity before any target-validation await. Channel IDs may re-enter.
    session = expected_session if expected_session is not None else await context.get_session()
    dispatch_state = dispatch_state if dispatch_state is not None else {}
    dispatch_state["started"] = False
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

    from src.core.session_store import SessionStore
    import uuid
    action_id = str(uuid.uuid4())
    default_claim = claim_ownership is None and isinstance(context.session_store, SessionStore)
    if default_claim:
        claim_ownership = lambda changes: context.session_store.claim_dialplan_handoff(
            session, changes, action_id, expected_deferred_id=expected_deferred_id, expected_completion_action=expected_completion_action)
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

    async def release_preparation():
        if release_ownership is not None:
            return await release_ownership(previous_transfer_state)
        await context.update_session(**previous_transfer_state)
        return True

    try:
        if before_dispatch is not None:
            await before_dispatch()
        if isinstance(context.session_store, SessionStore):
            current = await context.session_store.get_by_call_id(context.call_id)
            owned = (await context.session_store.owns_dialplan_handoff(
                session, action_id, expected_deferred_id=expected_deferred_id, expected_completion_action=expected_completion_action)
                if default_claim else current is session and not session.cleanup_in_progress
                and not session.cleanup_completed and bool(getattr(session, "transfer_active", False))
                and not session.pending_deferred_transfer)
            if not owned:
                await release_preparation()
                return {"status": "failed", "ownership_lost": True}
        if on_dispatch_started is not None:
            on_dispatch_started()  # Synchronous; no await between metadata and the POST.
    except asyncio.CancelledError:
        await release_preparation()
        raise
    except Exception:
        if not await release_preparation():
            return {"status": "failed", "ownership_lost": True}
        return {"status": "failed", "message": "Handoff preparation failed before dispatch."}
    # No await between this flag and starting the POST. Cancellation from this
    # point may mean PBX acceptance, so ownership must be retained.
    dispatch_state["started"] = True
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
