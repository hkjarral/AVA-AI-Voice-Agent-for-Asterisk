"""
Send Email Summary Tool

Automatically sends call summary emails to admin after call completion.
"""

import os
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any
import html
import structlog

try:
    import resend  # type: ignore
except Exception:
    resend = None

from src.tools.base import Tool, ToolDefinition, ToolCategory, ToolParameter
from src.tools.context import ToolExecutionContext
from src.tools.business.email_dispatcher import send_email, resolve_context_value
from src.tools.business.email_templates import DEFAULT_SEND_EMAIL_SUMMARY_HTML_TEMPLATE
from src.tools.business.template_renderer import render_html_template_with_fallback

logger = structlog.get_logger(__name__)


def should_send_email_summary(session: Any, config: Dict[str, Any]) -> bool:
    """Single source of truth for the post-call email decision (H5 / Codex P2).

    Per-agent ``session.email_enabled`` is a tri-state TRUE override of the global
    ``tools.send_email_summary.enabled`` gate:
    - ``True``  -> SEND (overrides a globally-disabled tool).
    - ``False`` -> SKIP (overrides a globally-enabled tool).
    - ``None``  -> inherit the global ``enabled`` gate (today's behavior, unchanged).

    Used by BOTH the engine's post-call invocation gate and the tool itself so they
    always agree.
    """
    email_enabled = getattr(session, "email_enabled", None)
    if email_enabled is True:
        return True
    if email_enabled is False:
        return False
    return bool(config.get("enabled", False))


class SendEmailSummaryTool(Tool):
    """
    Send call summary email to admin after call completion.
    
    This tool is automatically triggered at the end of each call (if enabled).
    It sends a comprehensive summary including transcript, metadata, and call outcome.
    """
    
    def __init__(self):
        super().__init__()
    
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="send_email_summary",
            description="Send call summary email to admin. NOTE: This does NOT end the call. If the user wants to end the call, you MUST also use the 'hangup_call' tool.",
            category=ToolCategory.BUSINESS,
            parameters=[]  # No parameters - auto-triggered by engine
        )
    
    async def execute(
        self,
        parameters: Dict[str, Any],
        context: ToolExecutionContext
    ) -> Dict[str, Any]:
        """
        Execute the send email summary tool.
        
        Args:
            parameters: Empty dict (no parameters needed)
            context: Tool execution context with session data
            
        Returns:
            Result dict with status and details
        """
        call_id = context.call_id
        
        try:
            config = context.get_config_value("tools.send_email_summary", {})

            # Get session data
            session = await context.get_session()
            if not session:
                logger.error("No session found", call_id=call_id)
                return {
                    "status": "error",
                    "message": "Session not found"
                }

            # Gate: global enable + per-agent tri-state toggle (H5).
            if not self._should_send(session, config):
                logger.info(
                    "Email summary disabled for this call, skipping send",
                    call_id=call_id,
                )
                return {
                    "status": "skipped",
                    "message": "Email summary is disabled"
                }

            # Gather call metadata
            email_data = self._prepare_email_data(session, config, call_id)
            
            # Send email asynchronously (don't block call cleanup)
            asyncio.create_task(self._send_email_async(email_data, call_id, config))
            
            logger.info(
                "Email summary scheduled for sending",
                call_id=call_id,
                recipient=email_data["to"]
            )
            
            return {
                "status": "success",
                "message": "Email summary will be sent shortly",
                "recipient": email_data["to"]
            }
            
        except Exception as e:
            logger.error(
                "Failed to schedule email summary",
                call_id=call_id,
                error=str(e),
                exc_info=True
            )
            return {
                "status": "error",
                "message": f"Failed to send email: {str(e)}"
            }
    
    def _should_send(self, session: Any, config: Dict[str, Any]) -> bool:
        """Decide whether the summary email should be sent (H5 / Codex P2).

        Delegates to :func:`should_send_email_summary` so the tool's gate and the
        engine's post-call invocation gate share one source of truth.
        """
        return should_send_email_summary(session, config)

    def _prepare_email_data(
        self,
        session: Any,
        config: Dict[str, Any],
        call_id: str
    ) -> Dict[str, Any]:
        """Prepare email data from session and config."""
        context_name = getattr(session, "context_name", None)
        called_number = getattr(session, "called_number", None)
        
        # Extract metadata
        caller_name = getattr(session, "caller_name", None)
        caller_number = getattr(session, "caller_number", "Unknown")
        outcome = getattr(session, "call_outcome", "Completed")

        # Jinja2 Template() does not enable autoescape: sanitize user-provided fields.
        if caller_name is not None:
            caller_name = html.escape(str(caller_name))
        if caller_number is not None:
            caller_number = html.escape(str(caller_number))
        if outcome is not None:
            outcome = html.escape(str(outcome))
        start_time = getattr(session, "start_time", None) or datetime.now(timezone.utc)
        end_time = datetime.now(timezone.utc)
        
        # Calculate duration - ensure both datetimes are timezone-aware
        if hasattr(session, "start_time") and session.start_time:
            # Handle both naive and aware datetimes for backward compatibility
            session_start = session.start_time
            if session_start.tzinfo is None:
                session_start = session_start.replace(tzinfo=timezone.utc)
            duration_seconds = int((end_time - session_start).total_seconds())
            duration_str = self._format_duration(duration_seconds)
        else:
            duration_str = "Unknown"
            duration_seconds = None
        
        # Get transcript from conversation_history
        transcript = ""
        transcript_html = ""
        transcript_note = None
        if hasattr(session, "conversation_history") and session.conversation_history:
            transcript = self._format_conversation(session.conversation_history)
            transcript_html = self._format_pretty_html(transcript)
            
            # Note: With input_audio_transcription enabled, user transcripts are now captured
            # for all providers including OpenAI Realtime with server_vad
        
        include_transcript = bool(config.get("include_transcript", True))
        call_outcome = outcome
        hangup_initiator = ""
        if isinstance(call_outcome, str):
            if call_outcome == "caller_hangup":
                hangup_initiator = "caller"
            elif call_outcome == "agent_hangup":
                hangup_initiator = "agent"
            elif call_outcome == "transferred":
                hangup_initiator = "system"
            elif call_outcome == "no_input_timeout":
                hangup_initiator = "system"

        variables = {
            "call_id": call_id,
            "context_name": context_name,
            "call_date": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "call_start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "call_end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration": duration_str,
            "duration_seconds": duration_seconds,
            "caller_name": caller_name,
            "caller_number": caller_number,
            "called_number": called_number,
            "outcome": outcome,
            "call_outcome": call_outcome,
            "hangup_initiator": hangup_initiator,
            "include_transcript": include_transcript,
            "transcript": transcript,
            "transcript_html": transcript_html,
            "transcript_note": transcript_note,
        }

        html_content = render_html_template_with_fallback(
            template_override=config.get("html_template"),
            default_template=DEFAULT_SEND_EMAIL_SUMMARY_HTML_TEMPLATE,
            variables=variables,
            call_id=call_id,
            tool_name="send_email_summary",
        )
        
        # Build email data. Per-agent override (session.email_*) wins; falls back to
        # the existing per-context-map -> global resolution (H5).
        admin_email = getattr(session, "email_recipient", None) or resolve_context_value(
            tool_config=config,
            key="admin_email",
            context_name=context_name,
            default="admin@company.com",
        )
        from_email = getattr(session, "email_from", None) or resolve_context_value(
            tool_config=config,
            key="from_email",
            context_name=context_name,
            default=config.get("from_email", "agent@company.com"),
        )
        from_name = config.get("from_name", "AI Voice Agent")

        subject_prefix = resolve_context_value(
            tool_config=config,
            key="subject_prefix",
            context_name=context_name,
            default="",
        )
        subject_prefix = str(subject_prefix or "").strip()
        if subject_prefix and not subject_prefix.endswith(" "):
            subject_prefix = subject_prefix + " "
        include_context_in_subject = bool(config.get("include_context_in_subject", True))
        context_tag = f"[{context_name}] " if (include_context_in_subject and context_name) else ""
        
        return {
            "to": admin_email,
            "from": f"{from_name} <{from_email}>",
            "subject": f"{subject_prefix}{context_tag}Call Summary - {caller_number if caller_number != 'Unknown' else 'Call'} - {start_time.strftime('%Y-%m-%d %H:%M')}",
            "html": html_content
        }

    def _format_pretty_html(self, text: str) -> str:
        """
        Convert plain text into HTML that preserves newlines in Outlook.

        Outlook desktop often ignores CSS `white-space: pre-wrap`, so we must render
        newlines as explicit `<br/>` tags.
        """
        safe = html.escape(text or "")
        safe = safe.replace("\r\n", "\n").replace("\r", "\n")
        return safe.replace("\n", "<br/>\n")
    
    async def _send_email_async(self, email_data: Dict[str, Any], call_id: str, tool_config: Dict[str, Any]):
        """Send email asynchronously via configured provider."""
        try:
            logger.info(
                "Sending email summary",
                call_id=call_id,
                recipient=email_data["to"]
            )
            await send_email(
                email_data=email_data,
                tool_config=tool_config,
                call_id=call_id,
                log_label="Email summary",
                recipient=str(email_data.get("to") or ""),
            )
            
        except Exception as e:
            logger.error(
                "Failed to send email summary",
                call_id=call_id,
                recipient=email_data.get("to"),
                error=str(e),
                exc_info=True
            )
    
    def _format_duration(self, seconds: int) -> str:
        """Format duration in seconds to human-readable string."""
        if seconds < 60:
            return f"{seconds} seconds"
        elif seconds < 3600:
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes}m {secs}s"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}h {minutes}m"
    
    def _format_transcript(self, transcript_entries: list) -> str:
        """Format transcript entries into readable text."""
        if not transcript_entries:
            return "No transcript available"
        
        lines = []
        for entry in transcript_entries:
            speaker = entry.get("speaker", "Unknown")
            text = entry.get("text", "")
            timestamp = entry.get("timestamp", "")
            
            if timestamp:
                lines.append(f"[{timestamp}] {speaker}: {text}")
            else:
                lines.append(f"{speaker}: {text}")
        
        return "\n".join(lines)
    
    def _format_conversation(self, conversation_history: list) -> str:
        """Format conversation history into readable text."""
        if not conversation_history:
            return "No conversation history available"
        
        lines = []
        for msg in conversation_history:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            
            if role == "assistant":
                lines.append(f"AI: {content}")
            elif role == "user":
                lines.append(f"Caller: {content}")
            else:
                lines.append(f"{role}: {content}")
        
        return "\n".join(lines)
