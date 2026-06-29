"""
Unified Transfer Tool - Transfer calls to extensions, queues, or ring groups.

This tool implements the canonical `blind_transfer` tool and replaces the legacy
`transfer_call` and `transfer_to_queue` tools with a single unified interface.
"""

from typing import Dict, Any, Optional, Tuple, List
import structlog

from ..base import Tool, ToolDefinition, ToolParameter, ToolCategory
from ..context import ToolExecutionContext
from .deferred_transfer import (
    build_deferred_transfer_action,
    build_deferred_transfer_result,
    store_pending_deferred_transfer,
    transfer_deferral_enabled,
)

logger = structlog.get_logger(__name__)


class UnifiedTransferTool(Tool):
    """
    Unified tool for transferring calls to various destinations:
    - Extensions: Direct SIP/PJSIP endpoints
    - Queues: ACD queues via FreePBX ext-queues context
    - Ring Groups: Ring groups via FreePBX ext-group context
    
    Note: Available destinations are configured in tools.transfer.destinations
    and validated at execution time.
    """
    
    @property
    def definition(self) -> ToolDefinition:
        """Return tool definition."""
        return ToolDefinition(
            name="blind_transfer",
            description=(
                "Blind transfer the caller to another configured destination. "
                "Supports Transfer Destinations of type extension, queue, and ring group. "
                "Use a configured destination key from Tools -> Transfer Destinations. "
                "The system validates that the destination exists before transferring. "
                "Prefer exact destination keys exposed in the runtime prompt/context instead of inventing names."
            ),
            category=ToolCategory.TELEPHONY,
            requires_channel=True,
            max_execution_time=30,
            parameters=[
                ToolParameter(
                    name="destination",
                    type="string",
                    description=(
                        "Configured Transfer Destinations key or close match "
                        "(matched against destination key/description)."
                    ),
                    required=True
                )
            ]
        )

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())

    @staticmethod
    def _resolve_dialplan_context(
        transfer_type: str,
        dest_config: Dict[str, Any],
        transfer_config: Dict[str, Any],
    ) -> str:
        configured = ""
        if isinstance(dest_config, dict):
            configured = str(dest_config.get("dialplan_context") or dest_config.get("context") or "").strip()
        if configured:
            return configured

        if transfer_type == "extension":
            return str((transfer_config or {}).get("extension_context") or "from-internal").strip() or "from-internal"
        if transfer_type == "queue":
            return str((transfer_config or {}).get("queue_context") or "ext-queues").strip() or "ext-queues"
        if transfer_type == "ringgroup":
            return str((transfer_config or {}).get("ringgroup_context") or "ext-group").strip() or "ext-group"
        return "from-internal"

    async def _defer_or_commit_transfer(
        self,
        *,
        context: ToolExecutionContext,
        source_tool: str,
        transfer_type: str,
        target: str,
        description: str,
        dialplan_context: str,
        destination_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        if transfer_deferral_enabled(context):
            try:
                session = await context.get_session()
                pending = getattr(session, "pending_deferred_transfer", None)
                if isinstance(pending, dict) and pending.get("kind") == "transfer":
                    same_pending_transfer = (
                        str(pending.get("transfer_type") or "").strip() == str(transfer_type or "").strip()
                        and str(pending.get("target") or "").strip() == str(target or "").strip()
                        and str(pending.get("dialplan_context") or "").strip() == str(dialplan_context or "").strip()
                    )
                    if not same_pending_transfer:
                        logger.warning(
                            "Conflicting deferred transfer request while another transfer is pending",
                            call_id=context.call_id,
                            existing_action_id=pending.get("id"),
                            existing_target=pending.get("target"),
                            existing_transfer_type=pending.get("transfer_type"),
                            requested_target=target,
                            requested_transfer_type=transfer_type,
                        )
                        return {
                            "status": "failed",
                            "message": "A transfer is already pending. Please wait for it to complete.",
                        }
                    logger.info(
                        "Suppressing duplicate deferred transfer request",
                        call_id=context.call_id,
                        existing_action_id=pending.get("id"),
                        existing_target=pending.get("target"),
                        requested_target=target,
                    )
                    return build_deferred_transfer_result(
                        action=pending,
                        message=f"Transferring you to {pending.get('description') or description} now.",
                        extra={
                            "destination": pending.get("target") or target,
                            "type": pending.get("transfer_type") or transfer_type,
                            "duplicate_suppressed": True,
                        },
                    )
            except Exception:
                logger.debug("Failed to check duplicate deferred transfer", call_id=context.call_id, exc_info=True)

        action = build_deferred_transfer_action(
            source_tool=source_tool,
            commit_tool="blind_transfer",
            transfer_type=transfer_type,
            target=target,
            description=description,
            dialplan_context=dialplan_context,
            destination_key=destination_key,
        )

        if transfer_deferral_enabled(context):
            await self._maybe_start_predial_transfer(context, action)
            await store_pending_deferred_transfer(context, action)
            return build_deferred_transfer_result(
                action=action,
                message=f"Transferring you to {description} now.",
                extra={
                    "destination": target,
                    "type": transfer_type,
                },
            )

        return await self.commit_deferred_action(action, context)

    def _deferred_strategy(self, context: ToolExecutionContext) -> str:
        transfer_config = context.get_config_value("tools.transfer") or {}
        if not isinstance(transfer_config, dict):
            return "drain_then_dial"
        strategy = str(transfer_config.get("deferred_strategy") or "drain_then_dial").strip().lower()
        if strategy in {"predial", "pre_dial", "pre-dial", "predial_then_bridge"}:
            return "predial_then_bridge"
        return "drain_then_dial"

    def _predial_endpoint_for_action(self, action: Dict[str, Any]) -> str:
        target = str(action.get("target") or "").strip()
        dialplan_context = str(action.get("dialplan_context") or "").strip()
        if not target or not dialplan_context:
            return ""
        return f"Local/{target}@{dialplan_context}"

    def _caller_id_for_predial(self, context: ToolExecutionContext) -> str:
        number = str(context.caller_number or "").strip()
        name = str(context.caller_name or "").strip()
        if name and number:
            safe_name = name.replace('"', "").replace("<", "").replace(">", "").strip()
            return f'"{safe_name}" <{number}>'
        return number or name or ""

    async def _maybe_start_predial_transfer(
        self,
        context: ToolExecutionContext,
        action: Dict[str, Any],
    ) -> None:
        if self._deferred_strategy(context) != "predial_then_bridge":
            return

        endpoint = self._predial_endpoint_for_action(action)
        if not endpoint:
            logger.warning("Predial transfer skipped - endpoint unavailable", call_id=context.call_id, action=action)
            return

        app = str(context.get_config_value("asterisk.app_name", "asterisk-ai-voice-agent") or "asterisk-ai-voice-agent")
        transfer_config = context.get_config_value("tools.transfer") or {}
        try:
            dial_timeout_sec = int((transfer_config if isinstance(transfer_config, dict) else {}).get("predial_timeout_seconds", 30) or 30)
        except (TypeError, ValueError):
            dial_timeout_sec = 30

        destination_key = str(action.get("destination_key") or action.get("target") or "").strip()
        try:
            session = await context.get_session()
            session.current_action = {
                "type": "predial_transfer",
                "deferred_action_id": action.get("id"),
                "destination_key": destination_key,
                "target": action.get("target"),
                "target_name": action.get("description"),
                "transfer_type": action.get("transfer_type"),
                "dialplan_context": action.get("dialplan_context"),
                "endpoint": endpoint,
                "answered": False,
                "ready_to_bridge": False,
                "bridged": False,
            }
            await context.session_store.upsert_call(session)
        except Exception:
            logger.debug("Failed to persist predial transfer action state", call_id=context.call_id, exc_info=True)

        try:
            result = await context.ari_client.send_command(
                method="POST",
                resource="channels",
                params={
                    "endpoint": endpoint,
                    "app": app,
                    "appArgs": f"predial-transfer,{context.call_id},{destination_key}",
                    "callerId": self._caller_id_for_predial(context),
                    "timeout": dial_timeout_sec,
                    "channelVars": {
                        "AGENT_ACTION": "predial_transfer",
                        "AGENT_CALL_ID": context.call_id,
                        "AGENT_TARGET": str(action.get("target") or ""),
                        "AAVA_TRANSFER_DESTINATION_KEY": destination_key,
                    },
                },
            )
        except Exception:
            logger.warning("Predial transfer originate failed", call_id=context.call_id, endpoint=endpoint, exc_info=True)
            return

        if not isinstance(result, dict) or not result.get("id"):
            logger.warning("Predial transfer originate returned no channel", call_id=context.call_id, endpoint=endpoint, response=result)
            return

        predial_channel_id = str(result["id"])
        action["payload"] = {
            **(action.get("payload") if isinstance(action.get("payload"), dict) else {}),
            "predial": {
                "enabled": True,
                "endpoint": endpoint,
                "channel_id": predial_channel_id,
                "destination_key": destination_key,
            },
        }
        try:
            session = await context.get_session()
            if isinstance(session.current_action, dict) and session.current_action.get("type") == "predial_transfer":
                session.current_action["predial_channel_id"] = predial_channel_id
                await context.session_store.upsert_call(session)
            engine = getattr(context.ari_client, "engine", None)
            if engine and hasattr(engine, "register_predial_transfer_channel"):
                engine.register_predial_transfer_channel(context.call_id, predial_channel_id)
        except Exception:
            logger.debug("Failed to register predial transfer channel", call_id=context.call_id, predial_channel_id=predial_channel_id, exc_info=True)

        logger.info(
            "Predial transfer leg originated",
            call_id=context.call_id,
            endpoint=endpoint,
            predial_channel_id=predial_channel_id,
            destination_key=destination_key,
        )

    async def prepare_or_execute_extension_transfer(
        self,
        context: ToolExecutionContext,
        extension: str,
        description: str,
        *,
        source_tool: str = "blind_transfer",
        destination_key: Optional[str] = None,
        dest_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        transfer_config = context.get_config_value("tools.transfer") or {}
        dialplan_context = self._resolve_dialplan_context(
            "extension",
            dest_config or {},
            transfer_config if isinstance(transfer_config, dict) else {},
        )
        return await self._defer_or_commit_transfer(
            context=context,
            source_tool=source_tool,
            transfer_type="extension",
            target=extension,
            description=description,
            dialplan_context=dialplan_context,
            destination_key=destination_key,
        )

    def _resolve_destination_key(self, destination: Any, destinations: Dict[str, Any]) -> Tuple[Optional[str], str]:
        raw = str(destination or "").strip()
        if not raw:
            return None, "empty_input"

        if raw in destinations:
            return raw, "exact_key"

        normalized = self._normalize_text(raw)

        # Case-insensitive exact key match.
        for key in destinations.keys():
            if self._normalize_text(key) == normalized:
                return str(key), "casefold_key"

        # Key prefix/contains matching.
        for key in destinations.keys():
            key_norm = self._normalize_text(key)
            if key_norm.startswith(normalized) or normalized in key_norm:
                return str(key), "partial_key"

        # Direct target number match (e.g., destination="6000" should map
        # to a configured key such as "support_agent" with target=6000).
        target_matches: List[str] = []
        for key, cfg in destinations.items():
            if not isinstance(cfg, dict):
                continue
            target_norm = self._normalize_text(str(cfg.get("target", "")))
            if target_norm and target_norm == normalized:
                target_matches.append(str(key))
        if len(target_matches) == 1:
            return target_matches[0], "exact_target"
        if len(target_matches) > 1:
            return None, "ambiguous_target"

        # Description prefix/contains matching.
        for key, cfg in destinations.items():
            if not isinstance(cfg, dict):
                continue
            description_norm = self._normalize_text(cfg.get("description", ""))
            if description_norm and (description_norm.startswith(normalized) or normalized in description_norm):
                return str(key), "partial_description"

        # Multi-word fallback (e.g., "live agent"): all tokens must match key or description.
        tokens = [t for t in normalized.split() if t]
        if tokens:
            token_matches = []
            for key, cfg in destinations.items():
                if not isinstance(cfg, dict):
                    continue
                haystack = f"{self._normalize_text(key)} {self._normalize_text(cfg.get('description', ''))}".strip()
                if all(token in haystack for token in tokens):
                    token_matches.append(str(key))
            if len(token_matches) == 1:
                return token_matches[0], "token_match"
            if len(token_matches) > 1:
                return None, "ambiguous_token_match"

        # Generic "human transfer" fallback:
        # If the user asks for a person/agent and exactly one extension destination exists,
        # use that destination.
        human_intent_tokens = {"agent", "human", "person", "representative", "rep", "operator", "live"}
        if any(t in human_intent_tokens for t in tokens):
            extension_keys = [
                str(key)
                for key, cfg in destinations.items()
                if isinstance(cfg, dict) and str(cfg.get("type", "")).strip().lower() == "extension"
            ]
            if len(extension_keys) == 1:
                return extension_keys[0], "single_extension_human_fallback"

        return None, "no_match"
    
    async def execute(
        self,
        parameters: Dict[str, Any],
        context: ToolExecutionContext
    ) -> Dict[str, Any]:
        """
        Execute transfer to the specified destination.
        
        Args:
            parameters: {destination: str}
            context: Tool execution context
        
        Returns:
            Dict with status and message
        """
        # Support both 'destination' (canonical) and 'target' (ElevenLabs uses this)
        destination = parameters.get('destination') or parameters.get('target')
        
        # Get destinations from config via context
        config = context.get_config_value("tools.transfer") or {}
        if isinstance(config, dict) and config.get("enabled") is False:
            logger.info("Unified transfer tool disabled by config", call_id=context.call_id)
            return {
                "status": "failed",
                "message": "Transfer service is disabled",
            }
        destinations = (config.get('destinations') or {}) if isinstance(config, dict) else {}
        if not destinations:
            logger.warning("Unified transfer tool not configured", call_id=context.call_id)
            return {
                "status": "failed",
                "message": "Transfer service is not available",
            }
        
        # Resolve exact / fuzzy destination name without hardcoded destination keys.
        if destination and destination not in destinations:
            matched, match_reason = self._resolve_destination_key(destination, destinations)
            if matched:
                dest_cfg = destinations.get(matched) if isinstance(destinations, dict) else {}
                logger.info(
                    "Resolved destination alias",
                    call_id=context.call_id,
                    original=destination,
                    matched=matched,
                    reason=match_reason,
                    matched_type=(dest_cfg or {}).get("type"),
                    matched_target=(dest_cfg or {}).get("target"),
                )
                destination = matched
            else:
                destination_debug = []
                for key, cfg in destinations.items():
                    if not isinstance(cfg, dict):
                        continue
                    destination_debug.append(
                        {
                            "key": str(key),
                            "type": str(cfg.get("type", "")),
                            "target": str(cfg.get("target", "")),
                            "description": str(cfg.get("description", ""))[:80],
                        }
                    )
                logger.warning(
                    "Transfer destination resolution failed",
                    call_id=context.call_id,
                    requested_destination=destination,
                    reason=match_reason,
                    configured_destinations=destination_debug[:12],
                )

        # Validate destination exists
        if destination not in destinations:
            available_keys = [str(k) for k in destinations.keys()]
            logger.error(
                "Invalid destination",
                call_id=context.call_id,
                destination=destination,
                available=available_keys,
            )
            available_hint = ", ".join(sorted(available_keys)[:12])
            message = f"Unknown destination: {destination}"
            if available_hint:
                message += f". Available destinations: {available_hint}"
            return {
                "status": "failed",
                "message": message
            }
        
        dest_config = destinations[destination] or {}
        transfer_type = dest_config.get('type')
        target = dest_config.get('target')
        description = dest_config.get('description', destination)
        
        logger.info(
            "Transfer requested",
            call_id=context.call_id,
            destination=destination,
            type=transfer_type,
            target=target
        )
        
        dialplan_context = self._resolve_dialplan_context(
            str(transfer_type or ""),
            dest_config if isinstance(dest_config, dict) else {},
            config if isinstance(config, dict) else {},
        )

        # Route based on transfer type
        if transfer_type == 'extension':
            return await self._defer_or_commit_transfer(
                context=context,
                source_tool="blind_transfer",
                transfer_type="extension",
                target=target,
                description=description,
                dialplan_context=dialplan_context,
                destination_key=str(destination),
            )
        elif transfer_type == 'queue':
            return await self._defer_or_commit_transfer(
                context=context,
                source_tool="blind_transfer",
                transfer_type="queue",
                target=target,
                description=description,
                dialplan_context=dialplan_context,
                destination_key=str(destination),
            )
        elif transfer_type == 'ringgroup':
            return await self._defer_or_commit_transfer(
                context=context,
                source_tool="blind_transfer",
                transfer_type="ringgroup",
                target=target,
                description=description,
                dialplan_context=dialplan_context,
                destination_key=str(destination),
            )
        else:
            logger.error("Invalid transfer type", type=transfer_type)
            return {
                "status": "failed",
                "message": f"Invalid transfer type: {transfer_type}"
            }

    async def commit_deferred_action(
        self,
        action: Dict[str, Any],
        context: ToolExecutionContext,
    ) -> Dict[str, Any]:
        transfer_type = str(action.get("transfer_type") or "").strip()
        target = str(action.get("target") or "").strip()
        description = str(action.get("description") or target or "").strip()
        dialplan_context = str(action.get("dialplan_context") or "").strip()
        payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
        predial = payload.get("predial") if isinstance(payload.get("predial"), dict) else None

        if predial and predial.get("enabled"):
            engine = getattr(context.ari_client, "engine", None)
            if engine and hasattr(engine, "finalize_predial_transfer"):
                result = await engine.finalize_predial_transfer(context, action)
                if result and result.get("status") == "success":
                    return result
                logger.warning(
                    "Predial transfer finalize failed; falling back to dialplan transfer",
                    call_id=context.call_id,
                    result=result,
                )

        if transfer_type == "extension":
            return await self._transfer_to_extension(context, target, description, dialplan_context=dialplan_context)
        if transfer_type == "queue":
            return await self._transfer_to_queue(context, target, description, dialplan_context=dialplan_context)
        if transfer_type == "ringgroup":
            return await self._transfer_to_ringgroup(context, target, description, dialplan_context=dialplan_context)

        return {
            "status": "failed",
            "message": f"Invalid deferred transfer type: {transfer_type}",
        }
    
    async def _transfer_to_extension(
        self,
        context: ToolExecutionContext,
        extension: str,
        description: str,
        *,
        dialplan_context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Transfer to a direct extension using ARI redirect.
        Channel stays in Stasis, so cleanup waits naturally.
        
        Args:
            context: Execution context
            extension: Extension number
            description: Human-readable description
        
        Returns:
            Result dict
        """
        logger.info("Extension transfer", call_id=context.call_id, 
                   extension=extension, description=description)
        
        # Get dialplan context for extension transfers (default: from-internal for FreePBX)
        config = context.get_config_value("tools.transfer") or {}
        dialplan_context = (
            str(dialplan_context or "").strip()
            or self._resolve_dialplan_context("extension", {}, config if isinstance(config, dict) else {})
        )
        
        # Set transfer_active flag BEFORE continue() - this prevents cleanup
        # from hanging up the caller when StasisEnd fires
        await context.update_session(
            transfer_active=True,
            transfer_state="transferring",
            transfer_target=description
        )
        
        # Use ARI continue to transfer via dialplan (like queue/ringgroup transfers)
        # This properly leaves Stasis and lets Asterisk dialplan handle the call
        await context.ari_client.send_command(
            method="POST",
            resource=f"channels/{context.caller_channel_id}/continue",
            params={
                "context": dialplan_context,
                "extension": extension,
                "priority": 1
            }
        )
        
        logger.info("✅ Extension transfer initiated", 
                   call_id=context.call_id, extension=extension, context=dialplan_context)
        return {
            "status": "success",
            "message": f"Transferring you to {description} now.",
            "destination": extension,
            "type": "extension"
        }
    
    async def _transfer_to_queue(
        self,
        context: ToolExecutionContext,
        queue: str,
        description: str,
        *,
        dialplan_context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Transfer to a queue using ARI continue to FreePBX ext-queues context.
        Channel leaves Stasis, so we must set transfer_active flag first.
        
        Args:
            context: Execution context
            queue: Queue number/name
            description: Human-readable description
        
        Returns:
            Result dict
        """
        logger.info("Queue transfer", call_id=context.call_id,
                   queue=queue, description=description)

        config = context.get_config_value("tools.transfer") or {}
        dialplan_context = (
            str(dialplan_context or "").strip()
            or self._resolve_dialplan_context("queue", {}, config if isinstance(config, dict) else {})
        )
        
        # Set transfer_active flag BEFORE continue() - this prevents cleanup
        # from hanging up the caller when StasisEnd fires
        await context.update_session(
            transfer_active=True,
            transfer_state="in_queue",
            transfer_target=description
        )
        
        # Execute transfer to FreePBX ext-queues context
        await context.ari_client.send_command(
            method="POST",
            resource=f"channels/{context.caller_channel_id}/continue",
            params={
                "context": dialplan_context,
                "extension": queue,
                "priority": 1
            }
        )
        
        logger.info("✅ Queue transfer initiated", call_id=context.call_id, 
                   queue=queue)
        
        return {
            "status": "success",
            "message": f"Transferring you to {description} now.",
            "destination": queue,
            "type": "queue"
        }
    
    async def _transfer_to_ringgroup(
        self,
        context: ToolExecutionContext,
        ringgroup: str,
        description: str,
        *,
        dialplan_context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Transfer to a ring group using ARI continue to FreePBX ext-group context.
        Channel leaves Stasis, so we must set transfer_active flag first.
        
        Args:
            context: Execution context
            ringgroup: Ring group number
            description: Human-readable description
        
        Returns:
            Result dict
        """
        logger.info("Ring group transfer", call_id=context.call_id,
                   ringgroup=ringgroup, description=description)

        config = context.get_config_value("tools.transfer") or {}
        dialplan_context = (
            str(dialplan_context or "").strip()
            or self._resolve_dialplan_context("ringgroup", {}, config if isinstance(config, dict) else {})
        )
        
        # Set transfer_active flag BEFORE continue() - this prevents cleanup
        # from hanging up the caller when StasisEnd fires
        await context.update_session(
            transfer_active=True,
            transfer_state="in_ringgroup",
            transfer_target=description
        )
        
        # Execute transfer to FreePBX ext-group context
        await context.ari_client.send_command(
            method="POST",
            resource=f"channels/{context.caller_channel_id}/continue",
            params={
                "context": dialplan_context,
                "extension": ringgroup,
                "priority": 1
            }
        )
        
        logger.info("✅ Ring group transfer initiated", call_id=context.call_id,
                   ringgroup=ringgroup)
        
        return {
            "status": "success",
            "message": f"Transferring you to {description} now.",
            "destination": ringgroup,
            "type": "ringgroup"
        }
