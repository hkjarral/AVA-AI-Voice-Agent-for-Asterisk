"""
Send Email Summary Tool

Automatically sends call summary emails to admin after call completion.
Uses Resend API for email delivery.
"""

import os
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any
import html
import structlog
from jinja2 import Template

try:
    import resend  # type: ignore
except Exception:
    resend = None

from src.tools.base import Tool, ToolDefinition, ToolCategory, ToolParameter
from src.tools.context import ToolExecutionContext
from src.tools.business.resend_client import send_email

logger = structlog.get_logger(__name__)

# HTML email template
EMAIL_TEMPLATE = """
<html>
<head>
  <style>
    body {
      font-family: Arial, sans-serif;
      line-height: 1.6;
      color: #333;
      max-width: 800px;
      margin: 0 auto;
    }
    .header {
      background: #4F46E5;
      color: white;
      padding: 20px;
      border-radius: 5px 5px 0 0;
    }
    .content {
      padding: 20px;
      background: #ffffff;
      border: 1px solid #e5e7eb;
      border-top: none;
      border-radius: 0 0 5px 5px;
    }
    .greeting {
      font-size: 16px;
      margin-bottom: 20px;
    }
    .metadata {
      background: #F3F4F6;
      padding: 15px;
      border-radius: 5px;
      margin-bottom: 20px;
    }
    .metadata p {
      margin: 5px 0;
    }
    .transcript {
      background: #FAFAFA;
      padding: 15px;
      border-left: 3px solid #4F46E5;
      margin-top: 20px;
      font-family: monospace;
      word-wrap: break-word;
    }
    .footer {
      margin-top: 20px;
      padding-top: 20px;
      border-top: 1px solid #e5e7eb;
      color: #6b7280;
      font-size: 14px;
    }
  </style>
</head>
<body>
  <div class="header">
    <h2>📞 Call Summary</h2>
  </div>
  <div class="content">
    <div class="greeting">
      {% if caller_name %}
      <p>Hello {{ caller_name }},</p>
      {% else %}
      <p>Hello,</p>
      {% endif %}
      <p>This is a summary of your recent call with our AI Voice Agent.</p>
    </div>
    
    <div class="metadata">
      <p><strong>Date:</strong> {{ call_date }}</p>
      <p><strong>Duration:</strong> {{ duration }}</p>
      {% if caller_number %}
      <p><strong>Caller:</strong> {{ caller_number }}</p>
      {% endif %}
      {% if outcome %}
      <p><strong>Outcome:</strong> {{ outcome }}</p>
      {% endif %}
    </div>
    
    {% if include_transcript and transcript %}
    <h3>Conversation Transcript</h3>
    <div class="transcript">{{ transcript_html }}</div>
    {% if transcript_note %}
    <p style="color: #6b7280; font-size: 12px; margin-top: 10px;">
      <em>{{ transcript_note }}</em>
    </p>
    {% endif %}
    {% endif %}
    
    <div class="footer">
      <p><em>Powered by AI Voice Agent</em></p>
    </div>
  </div>
</body>
</html>
"""


class SendEmailSummaryTool(Tool):
    """
    Send call summary email to admin after call completion.
    
    This tool is automatically triggered at the end of each call (if enabled).
    It sends a comprehensive summary including transcript, metadata, and call outcome.
    """
    
    def __init__(self):
        super().__init__()
        self._template = Template(EMAIL_TEMPLATE)
    
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
            # Check if tool is enabled
            config = context.get_config_value("tools.send_email_summary", {})
            if not config.get("enabled", False):
                logger.info(
                    "Email summary tool disabled, skipping send",
                    call_id=call_id
                )
                return {
                    "status": "skipped",
                    "message": "Email summary tool is disabled"
                }
            
            # Get session data
            session = await context.get_session()
            if not session:
                logger.error("No session found", call_id=call_id)
                return {
                    "status": "error",
                    "message": "Session not found"
                }
            
            # Gather call metadata
            email_data = self._prepare_email_data(session, config, call_id)
            
            # Send email asynchronously (don't block call cleanup)
            asyncio.create_task(self._send_email_async(email_data, call_id))
            
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
    
    def _prepare_email_data(
        self,
        session: Any,
        config: Dict[str, Any],
        call_id: str
    ) -> Dict[str, Any]:
        """Prepare email data from session and config."""
        
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
        
        # Get transcript from conversation_history
        transcript = ""
        transcript_html = ""
        transcript_note = None
        if hasattr(session, "conversation_history") and session.conversation_history:
            transcript = self._format_conversation(session.conversation_history)
            transcript_html = self._format_pretty_html(transcript)
            
            # Note: With input_audio_transcription enabled, user transcripts are now captured
            # for all providers including OpenAI Realtime with server_vad
        
        # Render email HTML
        html_content = self._template.render(
            call_date=start_time.strftime("%Y-%m-%d %H:%M:%S"),
            duration=duration_str,
            caller_name=caller_name,
            caller_number=caller_number,
            outcome=outcome,
            include_transcript=config.get("include_transcript", True),
            transcript=transcript,
            transcript_html=transcript_html,
            transcript_note=transcript_note
        )
        
        # Build email data
        admin_email = config.get("admin_email", "admin@company.com")
        from_email = config.get("from_email", "agent@company.com")
        from_name = config.get("from_name", "AI Voice Agent")
        
        return {
            "to": admin_email,
            "from": f"{from_name} <{from_email}>",
            "subject": f"Call Summary - {caller_number if caller_number != 'Unknown' else 'Call'} - {start_time.strftime('%Y-%m-%d %H:%M')}",
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
    
    async def _send_email_async(self, email_data: Dict[str, Any], call_id: str):
        """Send email asynchronously via Resend API."""
        try:
            # Send email
            logger.info(
                "Sending email summary via Resend",
                call_id=call_id,
                recipient=email_data["to"]
            )
            await send_email(
                email_data=email_data,
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
