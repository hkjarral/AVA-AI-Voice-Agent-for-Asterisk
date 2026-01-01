"""
Unified Transfer Tool - Transfer calls to extensions, queues, or ring groups.

This tool replaces the separate transfer_call and transfer_to_queue tools
with a single unified interface for all transfer types.
"""

from typing import Dict, Any, Optional
import structlog

from ..base import Tool, ToolDefinition, ToolParameter, ToolCategory
from ..context import ToolExecutionContext

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
            name="transfer",
            description=(
                "Transfer the caller to another destination. "
                "Choose from configured destinations like: "
                "'sales_agent', 'support_agent', 'sales_queue', 'support_queue', "
                "'sales_team', 'support_team'. "
                "The system will validate the destination exists."
            ),
            category=ToolCategory.TELEPHONY,
            requires_channel=True,
            max_execution_time=30,
            parameters=[
                ToolParameter(
                    name="destination",
                    type="string",
                    description=(
                        "Name of the destination to transfer to. "
                        "Examples: 'sales_agent' for direct agent, "
                        "'sales_queue' for ACD queue, 'sales_team' for ring group"
                    ),
                    required=True
                )
            ]
        )
    
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
        destinations = (config.get('destinations') or {}) if isinstance(config, dict) else {}
        if not destinations:
            logger.warning("Unified transfer tool not configured", call_id=context.call_id)
            return {
                "status": "failed",
                "message": "Transfer service is not available",
            }
        
        # Fuzzy matching for common destination patterns
        # Maps generic terms to configured destination keys
        if destination and destination not in destinations:
            dest_lower = destination.lower().strip()
            matched = None
            
            # Try exact match first (case-insensitive)
            for key in destinations:
                if key.lower() == dest_lower:
                    matched = key
                    break
            
            # Try prefix/contains matching
            if not matched:
                for key in destinations:
                    key_lower = key.lower()
                    # "sales" matches "sales_agent", "sales_team", "sales_queue"
                    if key_lower.startswith(dest_lower) or dest_lower in key_lower:
                        matched = key
                        logger.info("Fuzzy matched destination", 
                                   original=destination, matched=matched)
                        break
            
            # Try common aliases
            if not matched:
                alias_map = {
                    'sales': 'sales_team',
                    'support': 'support_team', 
                    'live agent': 'sales_agent',
                    'live person': 'sales_agent',
                    'agent': 'sales_agent',
                    'human': 'sales_agent',
                    'person': 'sales_agent',
                    'representative': 'support_agent',
                    'rep': 'support_agent',
                }
                if dest_lower in alias_map and alias_map[dest_lower] in destinations:
                    matched = alias_map[dest_lower]
                    logger.info("Alias matched destination",
                               original=destination, matched=matched)
            
            if matched:
                destination = matched
        
        # Validate destination exists
        if destination not in destinations:
            logger.error("Invalid destination", destination=destination, 
                        available=list(destinations.keys()))
            return {
                "status": "failed",
                "message": f"Unknown destination: {destination}"
            }
        
        dest_config = destinations[destination]
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
        
        # Route based on transfer type
        if transfer_type == 'extension':
            return await self._transfer_to_extension(context, target, description)
        elif transfer_type == 'queue':
            return await self._transfer_to_queue(context, target, description)
        elif transfer_type == 'ringgroup':
            return await self._transfer_to_ringgroup(context, target, description)
        else:
            logger.error("Invalid transfer type", type=transfer_type)
            return {
                "status": "failed",
                "message": f"Invalid transfer type: {transfer_type}"
            }
    
    async def _transfer_to_extension(
        self,
        context: ToolExecutionContext,
        extension: str,
        description: str
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
        dialplan_context = config.get("extension_context", "from-internal")
        
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
        description: str
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
                "context": "ext-queues",
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
        description: str
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
                "context": "ext-group",
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
