"""In-call disposition workflow for VICIdial-owned Remote Agent calls."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import structlog

from src.integrations.vicidial import (
    VicidialApiClient,
    VicidialIntegrationError,
    VicidialSessionInfo,
    allowed_dispositions,
    normalize_callback_datetime,
    status_for,
)
from src.tools.base import Tool, ToolCategory, ToolDefinition, ToolParameter
from src.tools.context import ToolExecutionContext

logger = structlog.get_logger(__name__)
DNC_WRITE_ATTEMPTS = 3
DNC_RETRY_DELAY_SECONDS = 0.25


def _session_info(session: Any) -> VicidialSessionInfo:
    raw = dict(getattr(session, "external_session", {}) or {})
    return VicidialSessionInfo(**{
        key: value
        for key, value in raw.items()
        if key in VicidialSessionInfo.__dataclass_fields__
    })


def _dialing_campaign_id(
    info: VicidialSessionInfo,
    mapping: Dict[str, Any],
) -> str:
    """Return the dialing campaign VICIdial requires for campaign actions.

    ``callid_info.campaign_id`` is an inbound group for inbound/blended calls,
    not a valid dialing campaign for campaign-scoped DNC or ``update_lead``.
    The Remote Agent mapping owns the explicit action campaign in that direction.
    Never fall back to ``agent_status.campaign_id`` because an inbound-only
    Remote Agent reports the reserved ``CLOSER`` login mode there.
    Outbound calls already report the dialing campaign directly.
    """
    if str(info.direction or "").strip().lower() == "inbound":
        return str(mapping.get("campaign_id") or "").strip()
    return str(info.campaign_id or mapping.get("campaign_id") or "").strip()


def _queue_pending_workflow(
    session: Any,
    *,
    semantic: str,
    connection: Dict[str, Any],
    payload: Dict[str, Any],
    info: VicidialSessionInfo,
    retry_terminal: Optional[Dict[str, str]],
) -> bool:
    """Persist a safely replayable workflow; return true if already completed."""
    try:
        from src.core.vicidial_store import get_vicidial_store

        queued_payload = dict(payload)
        queued_payload["requested_status"] = str(
            getattr(session, "external_requested_disposition", None) or ""
        )
        if retry_terminal:
            queued_payload["retry_terminal"] = {
                "semantic": str(retry_terminal.get("semantic") or "ai_hangup"),
                "operation_reason": str(
                    retry_terminal.get("operation_reason") or "durable-workflow-retry"
                ),
                "session": {
                    key: value
                    for key, value in info.to_dict().items()
                    if key != "metadata" and value is not None
                },
                "mapping_revision": str(
                    getattr(session, "external_mapping_revision", None) or ""
                ),
            }
        queued = get_vicidial_store().enqueue_pending_action(
            operation=semantic,
            connection=connection,
            payload=queued_payload,
            call_id=str(getattr(session, "call_id", None) or "") or None,
            external_call_id=info.external_call_id or None,
        )
        action_id = str(queued.get("id") or "").strip()
        if not action_id:
            raise RuntimeError("VICIdial retry queue did not return an action ID")
        if str(queued.get("status") or "") == "completed":
            payload["workflow_committed"] = True
            session.external_disposition_payload = payload
            session.external_events.append({
                "operation": f"{semantic}_queue",
                "success": True,
                "message": f"An identical queued {semantic} action was already completed",
                "action_id": action_id,
            })
            return True
        payload["workflow_queued"] = True
        payload["workflow_queue_id"] = action_id
        session.external_disposition_payload = payload
        session.external_events.append({
            "operation": f"{semantic}_queue",
            "success": True,
            "message": f"{semantic.title()} action queued durably for retry",
            "action_id": action_id,
        })
    except Exception as exc:
        session.external_events.append({
            "operation": f"{semantic}_queue",
            "success": False,
            "message": f"{semantic.title()} retry queue failed: {type(exc).__name__}",
        })
        logger.error(
            "VICIdial workflow retries exhausted and durable queueing failed",
            call_id=getattr(session, "call_id", None),
            workflow=semantic,
            exc_info=True,
        )
    return False


def queue_vicidial_disposition_workflow(
    session: Any,
    *,
    retry_terminal: Optional[Dict[str, str]] = None,
) -> bool:
    """Durably retain a selected DNC/callback before acknowledging it."""
    semantic = str(
        getattr(session, "external_disposition_label", None) or ""
    ).strip().lower()
    if semantic not in {"dnc", "callback"}:
        return False
    payload = dict(getattr(session, "external_disposition_payload", {}) or {})
    already_completed = _queue_pending_workflow(
        session,
        semantic=semantic,
        connection=dict(getattr(session, "external_connection", {}) or {}),
        payload=payload,
        info=_session_info(session),
        retry_terminal=retry_terminal,
    )
    retained_payload = dict(
        getattr(session, "external_disposition_payload", {}) or {}
    )
    return bool(
        already_completed
        or retained_payload.get("workflow_committed")
        or retained_payload.get("workflow_queued")
    )


async def commit_vicidial_disposition_workflow(
    session: Any,
    *,
    retry_terminal: Optional[Dict[str, str]] = None,
    queue_on_failure: bool = True,
) -> bool:
    """Commit and verify a pending DNC/callback side effect before hangup."""
    semantic = str(getattr(session, "external_disposition_label", None) or "").strip()
    if semantic not in {"dnc", "callback"}:
        return True

    mapping = dict(getattr(session, "external_mapping", {}) or {})
    connection = dict(getattr(session, "external_connection", {}) or {})
    payload = dict(getattr(session, "external_disposition_payload", {}) or {})
    if bool(payload.get("workflow_committed")):
        return True
    info = _session_info(session)
    client = VicidialApiClient(connection)

    if semantic == "dnc":
        for attempt in range(1, DNC_WRITE_ATTEMPTS + 1):
            result = await client.add_dnc_phone(
                phone_number=str(payload.get("phone_number") or info.phone_number or ""),
                campaign_id=str(payload.get("campaign_id") or ""),
            )
            session.external_events.append({
                "operation": "dnc",
                "attempt": attempt,
                **result.to_dict(),
            })
            if result.success:
                queued_action_id = str(payload.get("workflow_queue_id") or "").strip()
                if queued_action_id:
                    try:
                        from src.core.vicidial_store import get_vicidial_store

                        get_vicidial_store().mark_pending_action_workflow_completed(
                            queued_action_id
                        )
                    except Exception:
                        logger.warning(
                            "VICIdial DNC succeeded but its retry record was not advanced",
                            call_id=getattr(session, "call_id", None),
                            action_id=queued_action_id,
                            exc_info=True,
                        )
                payload["workflow_committed"] = True
                session.external_disposition_payload = payload
                return True
            if attempt < DNC_WRITE_ATTEMPTS:
                await asyncio.sleep(DNC_RETRY_DELAY_SECONDS)
        if queue_on_failure:
            return _queue_pending_workflow(
                session,
                semantic=semantic,
                connection=connection,
                payload=payload,
                info=info,
                retry_terminal=retry_terminal,
            )
        return False

    expected = {
        "lead_id": str(payload.get("lead_id") or info.lead_id or ""),
        "callback_type": "CURRENT",
        "recipient": str(payload.get("callback_type") or "ANYONE"),
        "callback_status": "ACTIVE",
        "lead_status": str(
            getattr(session, "external_requested_disposition", None) or "CALLBK"
        ),
        "campaign_id": str(payload.get("campaign_id") or info.campaign_id or ""),
        "callback_date": str(payload.get("callback_datetime") or ""),
    }
    if str(payload.get("callback_type") or "ANYONE").upper() == "USERONLY":
        expected["user"] = str(payload.get("callback_user") or info.agent_user or "")

    # A previous update may have succeeded even when its response or the first
    # verification timed out. Read before mutating on every retry so we do not
    # create duplicate scheduled callbacks.
    verification = await client.lead_callback_info(
        lead_id=str(payload.get("lead_id") or info.lead_id or "")
    )
    if not verification.success:
        # An earlier update may already have succeeded even if its response was
        # lost. Without a successful read we cannot prove absence, so retrying
        # the mutation could create a duplicate scheduled callback.
        session.external_events.append({
            "operation": "callback_verify",
            **verification.to_dict(),
        })
        if queue_on_failure:
            return _queue_pending_workflow(
                session,
                semantic=semantic,
                connection=connection,
                payload=payload,
                info=info,
                retry_terminal=retry_terminal,
            )
        return False
    matching_rows = [
        row
        for row in verification.rows
        if all(str(row.get(key) or "").strip() == value for key, value in expected.items())
    ]
    if not matching_rows:
        result = await client.update_lead_callback(
            lead_id=str(payload.get("lead_id") or info.lead_id or ""),
            campaign_id=str(payload.get("campaign_id") or info.campaign_id or ""),
            callback_datetime=str(payload.get("callback_datetime") or ""),
            callback_status=str(
                getattr(session, "external_requested_disposition", None) or "CALLBK"
            ),
            callback_type=str(payload.get("callback_type") or "ANYONE"),
            callback_user=str(payload.get("callback_user") or info.agent_user or ""),
            callback_comments=str(payload.get("comments") or ""),
        )
        session.external_events.append({"operation": "callback", **result.to_dict()})
        if not result.success:
            if queue_on_failure:
                return _queue_pending_workflow(
                    session,
                    semantic=semantic,
                    connection=connection,
                    payload=payload,
                    info=info,
                    retry_terminal=retry_terminal,
                )
            return False
        verification = await client.lead_callback_info(
            lead_id=str(payload.get("lead_id") or info.lead_id or "")
        )
        matching_rows = [
            row
            for row in verification.rows
            if all(str(row.get(key) or "").strip() == value for key, value in expected.items())
        ]

    if not verification.success or not matching_rows:
        verification.success = False
        verification.error_code = "callback_verification_failed"
        verification.message = (
            "VICIdial accepted the callback update but the expected active callback "
            "record could not be verified"
        )
    session.external_events.append({
        "operation": "callback_verify",
        **verification.to_dict(),
    })
    if verification.success:
        payload["workflow_committed"] = True
        session.external_disposition_payload = payload
        queued_action_id = str(payload.get("workflow_queue_id") or "").strip()
        if queued_action_id:
            try:
                from src.core.vicidial_store import get_vicidial_store

                get_vicidial_store().mark_pending_action_workflow_completed(
                    queued_action_id
                )
            except Exception:
                logger.warning(
                    "VICIdial callback succeeded but its retry record was not advanced",
                    call_id=getattr(session, "call_id", None),
                    action_id=queued_action_id,
                    exc_info=True,
                )
    elif queue_on_failure:
        return _queue_pending_workflow(
            session,
            semantic=semantic,
            connection=connection,
            payload=payload,
            info=info,
            retry_terminal=retry_terminal,
        )
    return bool(verification.success)


async def execute_vicidial_transfer(
    *,
    context: ToolExecutionContext,
    destination: Dict[str, Any],
) -> Dict[str, Any]:
    """Serialize a configured cold transfer with other terminal operations."""
    from src.core.vicidial_lifecycle import vicidial_lifecycle_lock

    async with vicidial_lifecycle_lock(context.call_id):
        session = await context.get_session()
        return await _execute_vicidial_transfer_locked(
            context=context,
            destination=destination,
            session=session,
        )


async def _execute_vicidial_transfer_locked(
    *,
    context: ToolExecutionContext,
    destination: Dict[str, Any],
    session: Any,
) -> Dict[str, Any]:
    """Commit a cold transfer while the call lifecycle lock is held."""
    if getattr(session, "external_platform", None) != "vicidial":
        return {"status": "failed", "message": "This is not a VICIdial-owned call"}
    if bool(getattr(session, "external_finalized", False)):
        return {"status": "failed", "message": "The VICIdial call is already finalized"}
    if bool(getattr(session, "external_finalizing", False)):
        return {
            "status": "failed",
            "message": "The VICIdial terminal workflow has already started",
        }

    # DNC and callback are API workflows, not merely terminal statuses. Commit
    # and verify either one before transfer finalizes the Remote Agent session;
    # cleanup intentionally skips calls finalized by a successful transfer.
    pending_workflow = str(
        getattr(session, "external_disposition_label", None) or ""
    ).strip().lower()
    if pending_workflow in {"dnc", "callback"}:
        if not await commit_vicidial_disposition_workflow(session):
            await context.session_store.upsert_call(session)
            logger.warning(
                "VICIdial transfer blocked because pending workflow could not be committed",
                call_id=context.call_id,
                workflow=pending_workflow,
            )
            return {
                "status": "failed",
                "message": (
                    f"The {pending_workflow} request could not be confirmed, so the "
                    "transfer was not started"
                ),
            }
        # Persist the workflow result before attempting the terminal action.
        # If transfer control then times out or raises, call history still
        # records that VICIdial accepted the DNC request.
        await context.session_store.upsert_call(session)

    mapping = dict(getattr(session, "external_mapping", {}) or {})
    connection = dict(getattr(session, "external_connection", {}) or {})
    transfer_type = str(destination.get("type") or "").strip().lower()
    target = str(destination.get("target") or "").strip()
    status = str(destination.get("status") or status_for(
        mapping,
        "ai_ingroup_transfer" if transfer_type == "vicidial_ingroup" else "ai_extension_transfer",
        "AIXFR" if transfer_type == "vicidial_ingroup" else "AIEXT",
    ))
    client = VicidialApiClient(connection)
    info = _session_info(session)
    if transfer_type == "vicidial_ingroup":
        result = await client.call_control(
            info, stage="INGROUPTRANSFER", status=status, ingroup_choices=target
        )
    elif transfer_type == "vicidial_extension":
        result = await client.call_control(
            info, stage="EXTENSIONTRANSFER", status=status, phone_number=target
        )
    else:
        return {"status": "failed", "message": "Unsupported VICIdial transfer type"}

    session.external_events.append({"operation": "transfer", **result.to_dict()})
    if not result.success:
        await context.session_store.upsert_call(session)
        logger.warning(
            "VICIdial transfer failed",
            call_id=context.call_id,
            destination=target,
            transfer_type=transfer_type,
            api_message=result.message,
        )
        return {
            "status": "failed",
            "message": "VICIdial did not accept the transfer; the caller remains connected",
        }

    session.external_finalized = True
    session.external_disposition = status
    session.external_disposition_label = "transfer"
    session.transfer_active = True
    session.transfer_state = "transferred"
    session.transfer_target = str(destination.get("description") or target)
    session.transfer_destination = target
    session.call_outcome = "transferred"
    await context.session_store.upsert_call(session)
    queued_action_id = str(
        dict(getattr(session, "external_disposition_payload", {}) or {}).get(
            "workflow_queue_id"
        )
        or ""
    ).strip()
    if queued_action_id:
        try:
            from src.core.vicidial_store import get_vicidial_store

            get_vicidial_store().complete_pending_action(queued_action_id)
        except Exception:
            logger.warning(
                "VICIdial transfer succeeded but its retry record was not completed",
                call_id=context.call_id,
                action_id=queued_action_id,
                exc_info=True,
            )
    try:
        from src.core.vicidial_store import get_vicidial_store

        recorded = get_vicidial_store().record_real_call_verification(
            mapping_id=info.mapping_id,
            mapping_revision=str(
                getattr(session, "external_mapping_revision", None) or ""
            ),
            direction=info.direction,
            external_call_id=info.external_call_id,
            status=status,
            operation="transfer",
        )
        if not recorded:
            logger.info(
                "Discarded VICIdial transfer evidence from stale mapping revision",
                call_id=context.call_id,
                mapping_id=info.mapping_id,
            )
    except Exception:
        logger.warning(
            "VICIdial transfer completed but readiness evidence could not be recorded",
            call_id=context.call_id,
            mapping_id=info.mapping_id,
            exc_info=True,
        )
    return {
        "status": "success",
        "message": f"Transferring you to {destination.get('description') or target} now.",
        "destination": target,
        "type": transfer_type,
        "vicidial_status": status,
    }


class SetCallDispositionTool(Tool):
    """Select the status ViciDial should record when the call ends."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="set_call_disposition",
            description=(
                "Set an allowlisted VICIdial disposition for this call. This tool is the "
                "authorized way to manage VICIdial call results and compliance requests. "
                "When a caller asks to be put on a do-not-call list, says DNC, asks to stop "
                "calling, or asks to remove their number, you MUST call this tool immediately "
                "with disposition='dnc'; never refuse or claim you cannot manage the request. "
                "When a caller on a VICIdial call asks to be called back, you MUST use this "
                "tool with disposition='callback' and callback_datetime as an ISO date/time. "
                "Do not use a calendar, appointment, or scheduling tool as a substitute for "
                "the VICIdial callback, even when those tools are available. Only create a "
                "separate calendar appointment when the caller explicitly requests one in "
                "addition to the callback. This tool does not end the call, so also use "
                "hangup_call when the caller asks to end."
            ),
            category=ToolCategory.BUSINESS,
            requires_channel=True,
            parameters=[
                ToolParameter(
                    name="disposition",
                    type="string",
                    description=(
                        "Exact configured disposition name. For any do-not-call, stop-calling, "
                        "or remove-my-number request use dnc. Other common configured values "
                        "include sale, not_interested, and callback."
                    ),
                    required=True,
                ),
                ToolParameter(
                    name="callback_datetime",
                    type="string",
                    description=(
                        "Required ISO date/time for a callback disposition. Confirm the "
                        "caller's requested date, time, and timezone before calling the tool."
                    ),
                ),
                ToolParameter(
                    name="comments",
                    type="string",
                    description="Optional callback comments.",
                ),
            ],
        )

    async def execute(
        self, parameters: Dict[str, Any], context: ToolExecutionContext
    ) -> Dict[str, Any]:
        from src.core.vicidial_lifecycle import vicidial_lifecycle_lock

        async with vicidial_lifecycle_lock(context.call_id):
            session = await context.get_session()
            return await self._execute_locked(parameters, context, session)

    async def _execute_locked(
        self,
        parameters: Dict[str, Any],
        context: ToolExecutionContext,
        session: Any,
    ) -> Dict[str, Any]:
        if getattr(session, "external_platform", None) != "vicidial":
            return {"status": "failed", "message": "Disposition is only available on VICIdial calls"}
        if bool(getattr(session, "external_finalized", False)):
            return {"status": "failed", "message": "The VICIdial call is already finalized"}
        if bool(getattr(session, "external_finalizing", False)):
            return {
                "status": "failed",
                "message": "The VICIdial terminal workflow has already started",
            }

        mapping = dict(getattr(session, "external_mapping", {}) or {})
        connection = dict(getattr(session, "external_connection", {}) or {})
        semantic = str(parameters.get("disposition") or "").strip().lower()
        dispositions = allowed_dispositions(mapping)
        status = dispositions.get(semantic)
        if not status:
            return {
                "status": "failed",
                "message": "Unknown disposition. Available values: " + ", ".join(
                    sorted(dispositions)
                ),
            }
        if semantic in {"dnc", "callback"}:
            status = status_for(mapping, semantic, status)

        existing_semantic = str(
            getattr(session, "external_disposition_label", None) or ""
        ).strip().lower()
        if existing_semantic == "dnc":
            if semantic != "dnc":
                return {
                    "status": "failed",
                    "message": (
                        "A do-not-call request is already selected and cannot be replaced"
                    ),
                }
            return {
                "status": "success",
                "message": "Do-not-call disposition is already selected.",
                "vicidial_status": str(
                    getattr(session, "external_requested_disposition", None) or status
                ),
            }
        if existing_semantic == "callback" and semantic not in {"callback", "dnc"}:
            return {
                "status": "failed",
                "message": (
                    "A callback is already selected and cannot be replaced by "
                    "another disposition"
                ),
            }
        existing_payload = dict(
            getattr(session, "external_disposition_payload", {}) or {}
        )
        if existing_semantic == "callback" and bool(
            existing_payload.get("workflow_committed")
        ):
            if semantic == "callback":
                return {
                    "status": "success",
                    "message": (
                        "The scheduled callback is already committed and cannot be "
                        "changed on this call."
                    ),
                    "vicidial_status": str(
                        getattr(session, "external_requested_disposition", None)
                        or status
                    ),
                }
            return {
                "status": "failed",
                "message": (
                    "A scheduled callback is already committed and cannot be "
                    "replaced with DNC on this call."
                ),
            }

        info = _session_info(session)
        payload: Dict[str, Any] = {}
        if semantic == "dnc":
            dnc_campaign_id = _dialing_campaign_id(info, mapping)
            if not info.phone_number or (
                mapping.get("dnc_scope") == "campaign" and not dnc_campaign_id
            ):
                return {"status": "failed", "message": "VICIdial phone/campaign data is unavailable for DNC"}
            payload = {
                "phone_number": info.phone_number,
                "campaign_id": (
                    dnc_campaign_id
                    if mapping.get("dnc_scope") == "campaign"
                    else "SYSTEM_INTERNAL"
                ),
            }
        elif semantic == "callback":
            try:
                callback_datetime = normalize_callback_datetime(
                    parameters.get("callback_datetime"),
                    str(connection.get("timezone") or "UTC"),
                )
            except VicidialIntegrationError as exc:
                return {"status": "failed", "message": str(exc)}
            callback_campaign_id = _dialing_campaign_id(info, mapping)
            if not info.lead_id or not callback_campaign_id:
                return {"status": "failed", "message": "VICIdial lead/campaign data is unavailable for callback"}
            callback_type = str(mapping.get("callback_type") or "ANYONE").upper()
            payload = {
                "lead_id": info.lead_id,
                "campaign_id": callback_campaign_id,
                "callback_datetime": callback_datetime,
                "callback_type": callback_type,
                "callback_user": info.agent_user,
                "comments": str(parameters.get("comments") or "")[:200],
            }

        if existing_semantic == "callback" and semantic in {"callback", "dnc"}:
            queued_action_id = str(
                existing_payload.get("workflow_queue_id") or ""
            ).strip()
            if queued_action_id:
                try:
                    from src.core.vicidial_store import get_vicidial_store

                    retry_store = get_vicidial_store()
                    queued_action = retry_store.get_pending_action(queued_action_id)
                    if queued_action and bool(
                        queued_action.get("workflow_completed")
                    ):
                        if semantic == "callback":
                            return {
                                "status": "success",
                                "message": (
                                    "The scheduled callback is already committed and "
                                    "cannot be changed on this call."
                                ),
                                "vicidial_status": str(
                                    getattr(
                                        session,
                                        "external_requested_disposition",
                                        None,
                                    )
                                    or status
                                ),
                            }
                        return {
                            "status": "failed",
                            "message": (
                                "A scheduled callback is already committed and cannot "
                                "be replaced with DNC on this call."
                            ),
                        }
                    elif queued_action and queued_action.get("status") == "pending":
                        if not retry_store.cancel_pending_action(queued_action_id):
                            raise RuntimeError(
                                "pending callback retry could not be canceled"
                            )
                except Exception:
                    logger.warning(
                        "VICIdial callback retry could not be closed before replacement",
                        call_id=context.call_id,
                        action_id=queued_action_id,
                        exc_info=True,
                    )
                    return {
                        "status": "failed",
                        "message": (
                            "The pending VICIdial callback could not be safely replaced. "
                            "Please retry."
                        ),
                    }

        session.external_requested_disposition = status
        session.external_disposition_label = semantic
        session.external_disposition_payload = payload
        session.external_events.append({
            "operation": "disposition_selected",
            "success": True,
            "semantic": semantic,
            "status": status,
        })
        if semantic in {"dnc", "callback"} and not queue_vicidial_disposition_workflow(
            session
        ):
            session.external_requested_disposition = None
            session.external_disposition_label = None
            session.external_disposition_payload = {}
            session.external_events.append({
                "operation": "disposition_durability",
                "success": False,
                "semantic": semantic,
                "message": (
                    "VICIdial compliance request was not acknowledged because "
                    "durable storage failed"
                ),
            })
            await context.session_store.upsert_call(session)
            return {
                "status": "failed",
                "message": (
                    f"The {semantic} request could not be recorded safely. "
                    "Please retry."
                ),
            }
        await context.session_store.upsert_call(session)
        retained_payload = dict(
            getattr(session, "external_disposition_payload", {}) or {}
        )
        if semantic in {"dnc", "callback"}:
            durability = (
                "confirmed in VICIdial"
                if retained_payload.get("workflow_committed")
                else "recorded durably for VICIdial"
            )
            message = (
                f"{semantic.title()} request {durability}; the final call status "
                "will be applied when the call ends."
            )
        else:
            message = f"Disposition set to {semantic}; it will be applied when the call ends."
        return {
            "status": "success",
            "message": message,
            "vicidial_status": status,
        }
