"""
Email validation and parsing utilities.

Handles email address validation, DNS domain checks, and speech-to-email parsing.
"""

import re
from typing import Optional, Tuple
import structlog

logger = structlog.get_logger(__name__)


class EmailValidator:
    """Validates and parses email addresses from speech recognition."""
    
    # Common speech patterns for email components
    SPEECH_REPLACEMENTS = {
        " dot ": ".",
        " at ": "@",
        " underscore ": "_",
        " dash ": "-",
        " hyphen ": "-",
    }
    
    # Email regex pattern
    EMAIL_PATTERN = re.compile(
        r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    )
    
    @classmethod
    def parse_from_speech(cls, speech_text: str) -> Optional[str]:
        """
        Parse email address from speech recognition text.
        
        Examples:
            "john dot smith at gmail dot com" -> "john.smith@gmail.com"
            "jane underscore doe at yahoo dot com" -> "jane_doe@yahoo.com"
            "mike dash jones at company dot co dot uk" -> "mike-jones@company.co.uk"
        
        Args:
            speech_text: Raw speech recognition text
            
        Returns:
            Parsed email address or None if invalid
        """
        if not speech_text:
            return None
        
        # Convert to lowercase and strip whitespace
        text = speech_text.lower().strip()
        
        # Replace speech patterns
        for pattern, replacement in cls.SPEECH_REPLACEMENTS.items():
            text = text.replace(pattern, replacement)
        
        # Remove extra spaces
        text = " ".join(text.split())
        
        # Remove spaces around @ and dots (common in speech)
        text = text.replace(" ", "")
        
        # Validate format
        if not cls.EMAIL_PATTERN.match(text):
            logger.warning(
                "Invalid email format after parsing",
                original=speech_text,
                parsed=text
            )
            return None
        
        logger.info(
            "Parsed email from speech",
            original=speech_text,
            parsed=text
        )
        return text
    
    @classmethod
    def validate_email(cls, email: str) -> bool:
        """
        Validate email format.
        
        Args:
            email: Email address to validate
            
        Returns:
            True if valid format, False otherwise
        """
        if not email:
            return False
        
        return bool(cls.EMAIL_PATTERN.match(email.strip()))
    
    @classmethod
    async def validate_domain(cls, email: str) -> Tuple[bool, Optional[str]]:
        """
        Validate email domain exists via DNS MX record lookup.
        
        Args:
            email: Email address to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not email or "@" not in email:
            return False, "Invalid email format"

        domain = email.split("@")[1]

        # Lazy import: dnspython is only needed for the MX/A-record lookup. Keeping
        # it out of module scope lets the admin_ui container (which has no dnspython)
        # import EmailValidator for format-only validation without ModuleNotFoundError.
        import dns.resolver

        try:
            # Check for MX records
            mx_records = dns.resolver.resolve(domain, "MX")
            if mx_records:
                logger.info(
                    "Domain validation successful",
                    domain=domain,
                    mx_count=len(mx_records)
                )
                return True, None
        except dns.resolver.NXDOMAIN:
            error = f"Domain {domain} does not exist"
            logger.warning("Domain validation failed", domain=domain, error="NXDOMAIN")
            return False, error
        except dns.resolver.NoAnswer:
            # No MX record, try A record as fallback
            try:
                a_records = dns.resolver.resolve(domain, "A")
                if a_records:
                    logger.info(
                        "Domain validation successful via A record",
                        domain=domain
                    )
                    return True, None
            except Exception as e:
                error = f"Domain {domain} has no mail server"
                logger.warning(
                    "Domain validation failed",
                    domain=domain,
                    error=str(e)
                )
                return False, error
        except Exception as e:
            error = f"DNS lookup failed for {domain}"
            logger.error(
                "Domain validation error",
                domain=domain,
                error=str(e)
            )
            return False, error
        
        return False, f"No mail server found for {domain}"
    
    @classmethod
    def format_for_speech(cls, email: str) -> str:
        """
        Format email address for text-to-speech readback.
        
        Args:
            email: Email address to format
            
        Returns:
            Speech-friendly format
            
        Example:
            "john.smith@gmail.com" -> "john dot smith at gmail dot com"
        """
        if not email:
            return ""
        
        # Split email into local and domain parts
        if "@" not in email:
            return email
        
        local, domain = email.split("@", 1)
        
        # Replace special characters with words
        local = local.replace(".", " dot ")
        local = local.replace("_", " underscore ")
        local = local.replace("-", " dash ")
        
        domain = domain.replace(".", " dot ")
        
        return f"{local} at {domain}"
