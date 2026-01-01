"""
Queue Transfer Tool - Transfer caller to ACD queue for agent pickup.

Supports queue name resolution, position announcements, and wait time estimates.
"""

from typing import Dict, Any, Optional
from src.tools.base import Tool, ToolDefinition, ToolParameter, ToolCategory
from src.tools.context import ToolExecutionContext
import structlog

logger = structlog.get_logger(__name__)


class TransferToQueueTool(Tool):
    """
    Transfer caller to an ACD (Automatic Call Distribution) queue.
    
    Features:
    - Queue name resolution (e.g., "sales" → "sales-queue")
    - Queue status checking (position, wait time)
    - Position announcements
    - Fallback handling for unavailable queues
    """
    
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="transfer_to_queue",
            description="Transfer the caller to a queue for the next available agent. Use this when the caller asks to speak with a department like sales, support, or billing.",
            category=ToolCategory.TELEPHONY,
            requires_channel=True,
            max_execution_time=30,
            parameters=[
                ToolParameter(
                    name="queue",
                    type="string",
                    description="Queue name (e.g., 'sales', 'support', 'billing')",
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
        Execute queue transfer.
        
        Workflow:
        1. Resolve queue name to Asterisk queue
        2. Check queue status (optional)
        3. Add caller to queue via dialplan continuation
        4. Update session state
        5. Return success with position/wait time
        
        Args:
            parameters: {queue: str}
            context: Tool execution context
        
        Returns:
            {
                status: "success" | "error",
                message: "Human-readable message",
                queue: "sales",
                position: 3,
                estimated_wait_seconds: 120,
                ai_should_speak: True
            }
        """
        await self.validate_parameters(parameters)

        tool_cfg = context.get_config_value("tools.transfer_to_queue") or {}
        if isinstance(tool_cfg, dict) and tool_cfg.get("enabled") is False:
            return {
                "status": "error",
                "message": "Queue transfer is currently disabled.",
                "ai_should_speak": True,
            }

        queue_name = parameters['queue'].lower().strip()
        
        logger.info(
            "📞 Queue transfer requested",
            call_id=context.call_id,
            queue=queue_name
        )
        
        try:
            # 1. Resolve queue name to Asterisk queue configuration
            queue_config = self._resolve_queue(queue_name, context)
            if not queue_config:
                return {
                    "status": "error",
                    "message": f"I'm sorry, I couldn't find the {queue_name} queue. Please try again or ask for help.",
                    "ai_should_speak": True
                }
            
            asterisk_queue = queue_config["asterisk_queue"]
            description = queue_config.get("description", queue_name)
            max_wait_time = queue_config.get("max_wait_time", 300)
            
            logger.info(
                "Queue resolved",
                call_id=context.call_id,
                queue_name=queue_name,
                asterisk_queue=asterisk_queue,
                max_wait_time=max_wait_time
            )
            
            # 2. Check queue status (if available)
            queue_status = await self._get_queue_status(asterisk_queue, context)
            
            # 3. Format brief announcement message
            message = f"Transferring you to {description} now."
            
            # 4. Mark session as transferred BEFORE continue() executes
            # This prevents cleanup from hanging up the caller channel
            # Since continue() causes StasisEnd which triggers cleanup immediately
            await context.update_session(
                transfer_active=True,
                transfer_state="in_queue",
                transfer_target=queue_name
            )
            
            # 5. Execute transfer immediately to FreePBX ext-queues context
            # Must happen while channel is still in Stasis
            # Channel will leave Stasis and continue in dialplan
            await context.ari_client.send_command(
                method="POST",
                resource=f"channels/{context.caller_channel_id}/continue",
                params={
                    "context": "ext-queues",
                    "extension": asterisk_queue,
                    "priority": 1
                }
            )
            
            logger.info(
                "✅ Queue transfer initiated",
                call_id=context.call_id,
                queue=queue_name,
                asterisk_queue=asterisk_queue,
                position=queue_status.get("position"),
                wait_time=queue_status.get("estimated_wait_seconds")
            )
            
            return {
                "status": "success",
                "message": message,
                "queue": queue_name,
                "asterisk_queue": asterisk_queue,
                "position": queue_status.get("position"),
                "estimated_wait_seconds": queue_status.get("estimated_wait_seconds"),
                "ai_should_speak": True
            }
            
        except Exception as e:
            logger.error(
                "Queue transfer failed",
                call_id=context.call_id,
                queue=queue_name,
                error=str(e),
                exc_info=True
            )
            return {
                "status": "error",
                "message": "I'm sorry, I encountered an error while transferring you to the queue. Please try again.",
                "ai_should_speak": True
            }
    
    def _resolve_queue(
        self,
        queue_name: str,
        context: ToolExecutionContext
    ) -> Optional[Dict[str, Any]]:
        """
        Resolve queue name to queue configuration.
        
        Args:
            queue_name: User-friendly queue name (e.g., "sales")
            context: Tool execution context
        
        Returns:
            Queue config dict or None if not found
        """
        config = context.get_config_value("tools.transfer_to_queue") or {}
        queues = (config.get("queues") or {}) if isinstance(config, dict) else {}
        if not queues:
            logger.warning("Queue transfer tool not configured", call_id=context.call_id)
            return None
        
        # Case-insensitive lookup
        queue_name_lower = queue_name.lower()
        for name, queue_config in queues.items():
            if name.lower() == queue_name_lower:
                return queue_config
        
        logger.warning(
            "Queue not found in configuration",
            call_id=context.call_id,
            queue_name=queue_name,
            available_queues=list(queues.keys())
        )
        return None
    
    async def _get_queue_status(
        self,
        asterisk_queue: str,
        context: ToolExecutionContext
    ) -> Dict[str, Any]:
        """
        Get current queue status (position, wait time).
        
        Note: This is a best-effort operation. If queue stats aren't available,
        we return defaults.
        
        Args:
            asterisk_queue: Asterisk queue name
            context: Tool execution context
        
        Returns:
            {
                "position": int or None,
                "estimated_wait_seconds": int or None,
                "agents_available": int or None
            }
        """
        try:
            # In a real implementation, you would query Asterisk ARI for queue stats
            # For now, return defaults since ARI doesn't expose queue stats directly
            # You'd need to use AMI (Asterisk Manager Interface) or custom events
            
            # Placeholder for future implementation
            return {
                "position": None,
                "estimated_wait_seconds": None,
                "agents_available": None
            }
            
        except Exception as e:
            logger.warning(
                "Could not get queue status",
                call_id=context.call_id,
                asterisk_queue=asterisk_queue,
                error=str(e)
            )
            return {
                "position": None,
                "estimated_wait_seconds": None,
                "agents_available": None
            }
    
    def _format_queue_message(
        self,
        description: str,
        queue_status: Dict[str, Any],
        max_wait_time: int
    ) -> str:
        """
        Format human-readable queue transfer message.
        
        Args:
            description: Queue description
            queue_status: Queue status dict
            max_wait_time: Maximum wait time in seconds
        
        Returns:
            Formatted message string
        """
        base_message = f"I'm transferring you to {description}."
        
        position = queue_status.get("position")
        estimated_wait = queue_status.get("estimated_wait_seconds")
        
        if position is not None and position > 0:
            base_message += f" You're number {position} in line."
        
        if estimated_wait is not None and estimated_wait > 0:
            wait_minutes = estimated_wait // 60
            if wait_minutes > 0:
                base_message += f" Estimated wait time is about {wait_minutes} minute{'s' if wait_minutes != 1 else ''}."
            else:
                base_message += " You should be connected shortly."
        else:
            base_message += " Please hold while I connect you to the next available agent."
        
        return base_message
