"""
Call Transfer Tool - Transfer caller to internal extension or external number.

Supports warm and blind transfer modes.
"""

from typing import Dict, Any, Optional
from src.tools.base import Tool, ToolDefinition, ToolParameter, ToolCategory
from src.tools.context import ToolExecutionContext
import structlog
import asyncio
import time

logger = structlog.get_logger(__name__)


class TransferCallTool(Tool):
    """
    Transfer caller to another extension or department.
    
    Features:
    - Warm transfer (AI stays on bridge, confirms connection)
    - Blind transfer (immediate redirect)
    - Hold music during transfer
    - Department name → extension resolution
    """
    
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="transfer_call",
            description="Transfer the caller to another extension or department. Use this when the caller asks to speak with a live person, agent, or specific department.",
            category=ToolCategory.TELEPHONY,
            requires_channel=True,
            max_execution_time=45,
            parameters=[
                ToolParameter(
                    name="target",
                    type="string",
                    description="Extension number or department name (e.g., '2765', 'sales', 'support')",
                    required=True
                ),
                ToolParameter(
                    name="mode",
                    type="string",
                    description="Transfer mode: 'warm' (announce and confirm) or 'blind' (immediate)",
                    enum=["warm", "blind"],
                    default="warm"
                )
            ]
        )
    
    async def execute(
        self,
        parameters: Dict[str, Any],
        context: ToolExecutionContext
    ) -> Dict[str, Any]:
        """
        Execute call transfer.
        
        Workflow (warm transfer):
        1. Resolve target to extension
        2. Validate target is allowed
        3. Start hold music for caller
        4. Originate call to target
        5. Wait for target to answer
        6. Add target to bridge
        7. Stop hold music
        8. Return success (AI will announce)
        
        Args:
            parameters: {target: str, mode: str}
            context: Tool execution context
        
        Returns:
            {
                status: "success" | "failed" | "error",
                message: "Human-readable message",
                extension: "2765",
                transfer_mode: "warm"
            }
        """
        await self.validate_parameters(parameters)

        target = parameters['target']
        # AI can suggest a mode, but YAML config takes precedence
        ai_suggested_mode = parameters.get('mode', 'warm')

        logger.warning(
            "Deprecated telephony tool in use",
            call_id=context.call_id,
            tool="transfer_call",
            replacement="blind_transfer / attended_transfer / live_agent_transfer",
        )

        logger.info(f"🔀 Transfer requested: {target} ({ai_suggested_mode} mode suggested by AI)", 
                   call_id=context.call_id)
        
        try:
            # 1. Resolve target to extension
            extension_info = await self._resolve_target(target, context)
            if not extension_info:
                return {
                    "status": "error",
                    "message": f"I'm sorry, I couldn't find '{target}'. Please try again or ask for help."
                }
            
            extension = extension_info['extension']
            dial_string = extension_info['dial_string']
            
            # YAML config mode takes precedence over AI suggestion
            mode = extension_info.get('mode', 'warm')
            logger.info(f"Resolved {target} → {extension} ({dial_string}), using {mode} mode from config")
            
            # 2. Execute transfer based on mode
            if mode == "warm":
                result = await self._warm_transfer(extension, dial_string, extension_info, context)
            else:
                result = await self._blind_transfer(extension, dial_string, extension_info, context)
            
            return result
            
        except Exception as e:
            logger.error(f"Transfer failed: {e}", exc_info=True, call_id=context.call_id)
            return {
                "status": "error",
                "message": f"I encountered an error while transferring. Please hold while I try again.",
                "error": str(e)
            }
    
    async def _resolve_target(
        self,
        target: str,
        context: ToolExecutionContext
    ) -> Optional[Dict[str, Any]]:
        """
        Resolve target (extension or department name) to dial information.
        
        Args:
            target: Extension number or department name
            context: Execution context
        
        Returns:
            {
                extension: "2765",
                name: "Live Agent",
                dial_string: "PJSIP/2765",
                context: "agent-outbound",
                action_type: "transfer",
                mode: "warm",
                queue: "support_queue",  # Optional fallback queue
                ... all other config fields
            }
            or None if not found
        """
        extensions_config = context.get_config_value('tools.extensions.internal', {})
        logger.info(f"DEBUG: extensions_config result: {extensions_config}", call_id=context.call_id)
        
        if not extensions_config:
            logger.warning("No extensions configured in tools.extensions.internal")
            return None
        
        # Try direct extension lookup
        if target in extensions_config:
            ext_config = extensions_config[target]
            return {
                'extension': target,
                'name': ext_config.get('name', target),
                'dial_string': ext_config.get('dial_string', f"PJSIP/{target}"),
                'action_type': ext_config.get('action_type', 'transfer'),
                'mode': ext_config.get('mode', 'warm'),
                'timeout': ext_config.get('timeout', 30),
                'queue': ext_config.get('queue'),  # Fallback queue
                'pass_caller_info': ext_config.get('pass_caller_info', False),
                **ext_config  # Include all other fields
            }
        
        # Try department name/alias lookup
        target_lower = target.lower()
        for ext_num, ext_config in extensions_config.items():
            # Check name match
            if ext_config.get('name', '').lower() == target_lower:
                return {
                    'extension': ext_num,
                    'name': ext_config.get('name', ext_num),
                    'dial_string': ext_config.get('dial_string', f"PJSIP/{ext_num}"),
                    'action_type': ext_config.get('action_type', 'transfer'),
                    'mode': ext_config.get('mode', 'warm'),
                    'timeout': ext_config.get('timeout', 30),
                    'queue': ext_config.get('queue'),
                    'pass_caller_info': ext_config.get('pass_caller_info', False),
                    **ext_config
                }
            
            # Check aliases
            aliases = ext_config.get('aliases', [])
            if target_lower in [a.lower() for a in aliases]:
                return {
                    'extension': ext_num,
                    'name': ext_config.get('name', ext_num),
                    'dial_string': ext_config.get('dial_string', f"PJSIP/{ext_num}"),
                    'action_type': ext_config.get('action_type', 'transfer'),
                    'mode': ext_config.get('mode', 'warm'),
                    'timeout': ext_config.get('timeout', 30),
                    'queue': ext_config.get('queue'),
                    'pass_caller_info': ext_config.get('pass_caller_info', False),
                    **ext_config
                }
        
        logger.warning(f"Target '{target}' not found in extensions config")
        return None
    
    async def _warm_transfer(
        self,
        extension: str,
        dial_string: str,
        extension_info: Dict[str, Any],
        context: ToolExecutionContext
    ) -> Dict[str, Any]:
        """
        Execute warm transfer using direct SIP origination via ARI.
        
        Workflow:
        1. Resolve target to extension
        2. Validate target is allowed
        3. Start hold music for caller
        4. Originate call to target
        5. Wait for target to answer
        6. Add target to bridge
        7. Stop hold music
        8. Return success (AI will announce)
        
        Args:
            extension: Target extension number
            dial_string: Full dial string
            extension_info: Extension configuration
            context: Execution context
        
        Returns:
            Result dict
        """
        logger.info(f"Starting warm transfer to {extension}", call_id=context.call_id)
        
        session = await context.get_session()
        if not session:
            return {
                "status": "error",
                "message": "Session not found"
            }
        
        # 1. For warm transfers, DON'T start MOH - let AI talk to caller
        # Starting MOH causes bridge to break when ;1 leg enters Stasis
        # because Asterisk auto-stops MOH on answer, removing channels from bridge
        logger.info(f"⏳ Warm transfer: AI continues conversation, no MOH",
                   call_id=context.call_id,
                   target=extension)
        
        # 2. Build transfer context to pass to target
        transfer_context = self._build_transfer_context(session, extension_info, context)
        
        # 3. Queue the action in session
        action = {
            'type': 'transfer',
            'mode': 'warm',
            'target': extension,
            'target_name': extension_info['name'],
            'dial_string': dial_string,
            'action_type': extension_info.get('action_type', 'transfer'),
            'timeout': extension_info.get('timeout', 30),
            'started_at': time.time(),
            'channel_id': None  # Will be filled when channel is originated
        }
        
        session.current_action = action
        session.transfer_context = transfer_context
        await context.session_store.upsert_call(session)
        
        # 4. Originate via direct SIP (no dialplan context)
        result = await self._originate_to_agent_outbound(
            target=extension,
            dial_string=dial_string,
            action_type=extension_info.get('action_type', 'transfer'),
            context_name=None,  # Not used - direct SIP origination
            session=session,
            transfer_context=transfer_context,
            timeout=extension_info.get('timeout', 30),
            context=context
        )
        
        if not result:
            # Originate failed - check for fallback queue
            await self._stop_moh(context.caller_channel_id, context)
            session.current_action = None
            await context.session_store.upsert_call(session)
            
            fallback_queue = extension_info.get('queue')
            if fallback_queue:
                return {
                    "status": "queue_fallback",
                    "message": f"{extension_info['name']} is on another call. Would you like me to place you in the queue?",
                    "extension": extension,
                    "queue": fallback_queue
                }
            else:
                return {
                    "status": "failed",
                    "message": f"{extension_info['name']} is on another call. Would you like to leave a message?",
                    "extension": extension
                }
        
        logger.info(f"✅ Warm transfer initiated to {extension}", call_id=context.call_id)
        
        # Dialplan will handle answer detection and Stasis entry
        # No MOH - AI continues conversation while connecting
        return {
            "status": "success",
            "message": f"I'm connecting you to {extension_info['name']} right now. They'll be with you in just a moment.",
            "extension": extension,
            "transfer_mode": "warm",
            "target_name": extension_info['name']
        }
    
    async def _blind_transfer(
        self,
        extension: str,
        dial_string: str,
        extension_info: Dict[str, Any],
        context: ToolExecutionContext
    ) -> Dict[str, Any]:
        """
        Execute blind transfer (immediate redirect).
        
        Args:
            extension: Target extension number
            dial_string: Full dial string
            extension_info: Extension configuration
            context: Execution context
        
        Returns:
            Result dict
        """
        logger.info(f"Starting blind transfer to {extension}", call_id=context.call_id)
        
        caller_channel_id = context.caller_channel_id
        
        # Redirect channel to extension
        # Using ARI redirect endpoint
        result = await context.ari_client.send_command(
            method="POST",
            resource=f"channels/{caller_channel_id}/redirect",
            data={
                "endpoint": dial_string
            }
        )
        
        if result and result.get('status') == 204:
            logger.info(f"✅ Blind transfer completed to {extension}", call_id=context.call_id)
            return {
                "status": "success",
                "message": f"Transferring you now.",
                "extension": extension,
                "transfer_mode": "blind",
                "target_name": extension_info['name']
            }
        else:
            logger.error(f"Blind transfer failed: {result}")
            return {
                "status": "failed",
                "message": f"Unable to transfer. Please hold.",
                "extension": extension
            }
    
    def _build_transfer_context(
        self,
        session,
        extension_info: Dict[str, Any],
        context: ToolExecutionContext
    ) -> Dict[str, Any]:
        """
        Build transfer context to pass to target.
        
        Returns dict with caller information for warm transfers.
        """
        transfer_ctx = {
            'caller_id': session.call_id,
            'caller_channel': session.caller_channel_id,
            'bridge_id': session.bridge_id
        }
        
        # Add caller info if configured
        if extension_info.get('pass_caller_info', False):
            transfer_ctx.update({
                'caller_name': session.caller_name or 'Unknown',
                'caller_number': session.caller_number or 'Unknown',
                'call_purpose': session.last_transcript or 'General inquiry'
            })
        
        return transfer_ctx
    
    async def _originate_to_agent_outbound(
        self,
        target: str,
        dial_string: str,
        action_type: str,
        context_name: str,  # Kept for signature compatibility but not used
        session,
        transfer_context: Dict[str, Any],
        timeout: int,
        context: ToolExecutionContext
    ) -> Optional[Dict[str, Any]]:
        """
        Originate call via direct SIP endpoint (ARI-based, no dialplan).
        
        Process:
        1. ARI originates direct SIP channel (e.g., SIP/6000)
        2. On answer, channel enters Stasis with app args
        3. Engine receives StasisStart event and handles bridging
        
        Args:
            target: Extension to dial
            action_type: Action type (transfer, voicemail, queue, etc.)
            context_name: Dialplan context
            session: Call session
            transfer_context: Context to pass
            timeout: Origination timeout
            ari_client: ARI client
        
        Returns:
            Channel dict if originated, None if failed
        """
        # DIRECT SIP ENDPOINT ORIGINATION (no Local channels)
        # Use dial_string directly (e.g., "SIP/6000") for clean audio path
        # When answered, SIP channel enters Stasis directly with warm-transfer args
        
        # Get AI identity from config for CallerID (prevents "anonymous" calls)
        ai_name = context.get_config_value('tools.ai_identity.name', 'AI Agent')
        ai_number = context.get_config_value('tools.ai_identity.number', '6789')
        caller_id = f'"{ai_name}" <{ai_number}>'
        
        logger.info(f"🔀 Direct SIP origination for warm transfer",
                   endpoint=dial_string,
                   action_type=action_type,
                   target=target,
                   caller_id=caller_id)
        
        try:
            result = await context.ari_client.send_command(
                method="POST",
                resource="channels",
                data={
                    "endpoint": dial_string,  # Direct SIP endpoint (e.g., "SIP/6000")
                    "callerId": caller_id,  # Set CallerID to AI Agent identity
                    "timeout": timeout,
                    "variables": {
                        "AGENT_ACTION": action_type,
                        "AGENT_CALL_ID": session.call_id,
                        "AGENT_BRIDGE_ID": session.bridge_id,
                        "AGENT_TARGET": target,
                        "CALLER_NAME": transfer_context.get('caller_name', ''),
                        "CALLER_NUMBER": transfer_context.get('caller_number', ''),
                        "CALL_PURPOSE": transfer_context.get('call_purpose', '')
                    }
                },
                params={
                    # SIP channel enters Stasis on answer with these args
                    "app": "asterisk-ai-voice-agent",
                    "appArgs": f"warm-transfer,{session.call_id},{target}"
                }
            )
            
            if result and 'status' in result:
                logger.error(f"Failed to originate: {result}")
                return None
            
            if result and 'id' in result:
                channel_id = result['id']
                logger.info(f"Channel originated: {channel_id}")
                
                # Update session with channel ID
                if session.current_action:
                    session.current_action['channel_id'] = channel_id
                    await context.session_store.upsert_call(session)
                
                return result
            
            logger.error(f"Unexpected originate result: {result}")
            return None
            
        except Exception as e:
            logger.error(f"Error originating: {e}", exc_info=True)
            return None
    
    async def _start_moh(self, channel_id: str, context: ToolExecutionContext):
        """Start music on hold for channel."""
        moh_class = context.get_config_value('tools.transfer_call.hold_music_class', 'default')
        
        try:
            await context.ari_client.send_command(
                method="POST",
                resource=f"channels/{channel_id}/moh",
                params={"mohClass": moh_class}
            )
            logger.debug(f"Started MOH on {channel_id}")
        except Exception as e:
            logger.warning(f"Failed to start MOH: {e}")
    
    async def _stop_moh(self, channel_id: str, context: ToolExecutionContext):
        """Stop music on hold for channel."""
        try:
            await context.ari_client.send_command(
                method="DELETE",
                resource=f"channels/{channel_id}/moh"
            )
            logger.debug(f"Stopped MOH on {channel_id}")
        except Exception as e:
            logger.warning(f"Failed to stop MOH: {e}")
    
    # Note: Old _originate_call and _wait_for_answer methods removed
    # New approach uses direct SIP origination via ARI with event-driven answer detection
