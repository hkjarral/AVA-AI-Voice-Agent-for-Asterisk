"""
Request Transcript Tool

Allows callers to request email transcript by providing their email address.
AI captures email via speech, validates, confirms, and sends transcript.
"""

import os
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import html
import structlog

try:
    import resend  # type: ignore
except Exception:
    resend = None

from src.tools.base import Tool, ToolDefinition, ToolCategory, ToolParameter
from src.tools.context import ToolExecutionContext
from src.utils.email_validator import EmailValidator
from src.tools.business.email_dispatcher import send_email, resolve_context_value
from src.tools.business.email_templates import DEFAULT_REQUEST_TRANSCRIPT_HTML_TEMPLATE
from src.tools.business.template_renderer import render_html_template_with_fallback

logger = structlog.get_logger(__name__)

class RequestTranscriptTool(Tool):
    """
    Request transcript tool for caller-initiated email requests.
    
    Workflow:
    1. Caller asks: "Can you email me a transcript?"
    2. AI asks: "What's your email address?"
    3. Caller provides email via speech
    4. Tool validates and confirms email
    5. Sends transcript to caller + admin (BCC)
    """
    
    def __init__(self):
        super().__init__()
        self._validator = EmailValidator()
        # Track sent emails per call to prevent duplicates
        # Note: Dict grows with calls, but cleared on container restart
        # For high-volume production, implement periodic cleanup
        self._sent_emails = {}  # {call_id: set(emails)}
    
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="request_transcript",
            description=(
                "Send call transcript to caller's email address. "
                "IMPORTANT: Before calling this tool, you MUST: "
                "1. Ask caller for their email address, "
                "2. Read back the captured email clearly (e.g., 'I have h-a-i-d-e-r-k-h-a-l-i-l at hotmail dot com'), "
                "3. Ask 'Is that correct?' and wait for confirmation, "
                "4. Only call this tool AFTER caller confirms the email is correct. "
                "Do NOT call this tool multiple times for the same request."
            ),
            category=ToolCategory.BUSINESS,
            parameters=[
                ToolParameter(
                    name="caller_email",
                    type="string",
                    description=(
                        "Caller's email address extracted from speech recognition. "
                        "Examples: 'john dot smith at gmail dot com' should be parsed as 'john.smith@gmail.com'"
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
        Execute the request transcript tool.
        
        Args:
            parameters: Dict containing 'caller_email' from speech
            context: Tool execution context
            
        Returns:
            Result dict with status, message, and next action for AI
        """
        call_id = context.call_id
        
        try:
            # Check if tool is enabled
            config = context.get_config_value("tools.request_transcript", {})
            if not config.get("enabled", False):
                logger.info(
                    "Request transcript tool disabled",
                    call_id=call_id
                )
                return {
                    "status": "disabled",
                    "message": "I'm sorry, but the email transcript feature is not available at the moment.",
                    "ai_should_speak": True
                }
            
            # Get caller email from parameters
            raw_email = parameters.get("caller_email", "").strip()
            if not raw_email:
                logger.warning(
                    "No caller email provided",
                    call_id=call_id
                )
                return {
                    "status": "error",
                    "message": "I didn't catch your email address. Could you please repeat it?",
                    "ai_should_speak": True
                }
            
            # Parse email from speech
            parsed_email = self._validator.parse_from_speech(raw_email)
            if not parsed_email:
                logger.warning(
                    "Failed to parse email from speech",
                    call_id=call_id,
                    raw_email=raw_email
                )
                return {
                    "status": "error",
                    "message": (
                        "I'm sorry, I couldn't understand that email address. "
                        "Could you please spell it again? For example, say 'john dot smith at gmail dot com'"
                    ),
                    "ai_should_speak": True
                }
            
            # Validate email format
            if not self._validator.validate_email(parsed_email):
                logger.warning(
                    "Invalid email format",
                    call_id=call_id,
                    parsed_email=parsed_email
                )
                return {
                    "status": "error",
                    "message": (
                        f"The email address {parsed_email} doesn't seem to be valid. "
                        "Could you please provide it again?"
                    ),
                    "ai_should_speak": True
                }
            
            # Validate domain exists (if configured)
            if config.get("validate_domain", True):
                domain_valid, domain_error = await self._validator.validate_domain(parsed_email)
                if not domain_valid:
                    logger.warning(
                        "Domain validation failed",
                        call_id=call_id,
                        email=parsed_email,
                        error=domain_error
                    )
                    return {
                        "status": "error",
                        "message": (
                            f"I couldn't verify the domain for {parsed_email}. "
                            "Could you please check and provide your email again?"
                        ),
                        "ai_should_speak": True
                    }

            # Get session data
            session = await context.get_session()
            if not session:
                logger.error("No session found", call_id=call_id)
                return {
                    "status": "error",
                    "message": "I'm sorry, I couldn't access the call data to send the transcript.",
                    "ai_should_speak": True
                }

            allow_multiple = bool(config.get("allow_multiple_recipients", False))
            normalized_email = parsed_email.lower()
            existing_emails = getattr(session, "transcript_emails", None)
            existing_single: Optional[str] = None
            if isinstance(existing_emails, set) and len(existing_emails) == 1:
                try:
                    existing_single = next(iter(existing_emails))
                except Exception:
                    existing_single = None

            # Idempotency / duplicate handling:
            # - Default behavior is "set/update": only the latest email is kept for end-of-call send.
            # - When allow_multiple_recipients is true, we keep additive behavior and suppress duplicates.
            if not allow_multiple and existing_single and existing_single == normalized_email:
                email_for_speech = self._validator.format_for_speech(parsed_email)
                logger.info(
                    "Transcript email already set (no-op)",
                    call_id=call_id,
                    caller_email=parsed_email,
                )
                return {
                    "status": "success",
                    "message": f"Got it. I'll send the complete transcript to {email_for_speech} when our call ends.",
                    "ai_should_speak": True,
                    "caller_email": parsed_email,
                    "email_for_speech": email_for_speech,
                }

            if allow_multiple:
                if call_id not in self._sent_emails:
                    self._sent_emails[call_id] = set()
                if normalized_email in self._sent_emails[call_id]:
                    logger.info(
                        "Duplicate transcript recipient detected, skipping",
                        call_id=call_id,
                        email=parsed_email,
                    )
                    return {
                        "status": "success",
                        "message": f"I already added {parsed_email} for the transcript. Please check your inbox after the call ends.",
                        "ai_should_speak": True,
                    }
            
            # Format email for speech readback
            email_for_speech = self._validator.format_for_speech(parsed_email)
            
            # IMPORTANT: Store email address in session for end-of-call transcript sending
            # Do NOT send immediately - conversation isn't complete yet!
            # Engine will check for this attribute in cleanup and send complete transcript
            if not hasattr(session, 'transcript_emails') or not isinstance(getattr(session, 'transcript_emails', None), set):
                session.transcript_emails = set()
            if allow_multiple:
                session.transcript_emails.add(normalized_email)
            else:
                # Default: last email wins (set/update semantics).
                session.transcript_emails.clear()
                session.transcript_emails.add(normalized_email)
            
            # Save session with transcript email
            await context.session_store.upsert_call(session)
            
            # Mark as requested to prevent noisy repeats when allow_multiple_recipients is enabled.
            if allow_multiple:
                self._sent_emails[call_id].add(normalized_email)
            
            logger.info(
                "Transcript email saved for end-of-call sending",
                call_id=call_id,
                caller_email=parsed_email,
                admin_bcc=config.get("admin_email")
            )
            
            return {
                "status": "success",
                "message": (
                    f"Perfect! I'll send the complete transcript to {email_for_speech} when our call ends."
                ),
                "ai_should_speak": True,
                "caller_email": parsed_email,
                "email_for_speech": email_for_speech
            }
            
        except Exception as e:
            logger.error(
                "Failed to process transcript request",
                call_id=call_id,
                error=str(e),
                exc_info=True
            )
            return {
                "status": "error",
                "message": (
                    "I'm sorry, I encountered an error while trying to send the transcript. "
                    "Please contact support for assistance."
                ),
                "ai_should_speak": True
            }
    
    def _prepare_email_data(
        self,
        caller_email: str,
        session: Any,
        config: Dict[str, Any],
        call_id: str
    ) -> Dict[str, Any]:
        """Prepare email data for transcript."""
        context_name = getattr(session, "context_name", None)
        called_number = getattr(session, "called_number", None)
        
        # Extract metadata
        caller_name = getattr(session, "caller_name", None)
        caller_number = getattr(session, "caller_number", "Unknown")

        # Jinja2 Template() does not enable autoescape: sanitize user-provided fields.
        if caller_name is not None:
            caller_name = html.escape(str(caller_name))
        if caller_number is not None:
            caller_number = html.escape(str(caller_number))
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
        if hasattr(session, "conversation_history") and session.conversation_history:
            transcript = self._format_conversation(session.conversation_history)
            transcript_html = self._format_pretty_html(transcript)
        else:
            transcript = "Transcript not available for this call."
            transcript_html = self._format_pretty_html(transcript)
        
        variables = {
            "call_id": call_id,
            "context_name": context_name,
            "recipient_email": caller_email,
            "call_date": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "call_start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "call_end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration": duration_str,
            "duration_seconds": duration_seconds,
            "caller_name": caller_name,
            "caller_number": caller_number,
            "called_number": called_number,
            "outcome": getattr(session, "call_outcome", ""),
            "call_outcome": getattr(session, "call_outcome", ""),
            "hangup_initiator": (
                "caller"
                if getattr(session, "call_outcome", "") == "caller_hangup"
                else "agent"
                if getattr(session, "call_outcome", "") == "agent_hangup"
                else "system"
                if getattr(session, "call_outcome", "") == "transferred"
                else ""
            ),
            "transcript": transcript,
            "transcript_html": transcript_html,
        }

        html_content = render_html_template_with_fallback(
            template_override=config.get("html_template"),
            default_template=DEFAULT_REQUEST_TRANSCRIPT_HTML_TEMPLATE,
            variables=variables,
            call_id=call_id,
            tool_name="request_transcript",
        )
        
        # Build email data
        from_email = resolve_context_value(
            tool_config=config,
            key="from_email",
            context_name=context_name,
            default=config.get("from_email", "agent@company.com"),
        )
        from_name = config.get("from_name", "AI Voice Agent")
        admin_email = resolve_context_value(
            tool_config=config,
            key="admin_email",
            context_name=context_name,
            default=None,
        )

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
        
        email_data = {
            "to": caller_email,
            "from": f"{from_name} <{from_email}>",
            "subject": f"{subject_prefix}{context_tag}Your Call Transcript - {start_time.strftime('%Y-%m-%d %H:%M')}",
            "html": html_content
        }
        
        # Add BCC for admin if configured
        if admin_email:
            email_data["bcc"] = admin_email
        
        return email_data

    def _format_pretty_html(self, text: str) -> str:
        """
        Convert plain text into HTML that preserves newlines in Outlook.

        Outlook desktop often ignores CSS `white-space: pre-wrap`, so we must render
        newlines as explicit `<br/>` tags.
        """
        safe = html.escape(text or "")
        safe = safe.replace("\r\n", "\n").replace("\r", "\n")
        return safe.replace("\n", "<br/>\n")
    
    async def _send_transcript_async(self, email_data: Dict[str, Any], call_id: str, tool_config: Dict[str, Any]):
        """Send transcript email asynchronously via configured provider."""
        try:
            logger.info(
                "Sending transcript",
                call_id=call_id,
                recipient=email_data["to"],
                bcc=email_data.get("bcc")
            )
            await send_email(
                email_data=email_data,
                tool_config=tool_config,
                call_id=call_id,
                log_label="Transcript",
                recipient=str(email_data.get("to") or ""),
            )
            
        except Exception as e:
            logger.error(
                "Failed to send transcript",
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
