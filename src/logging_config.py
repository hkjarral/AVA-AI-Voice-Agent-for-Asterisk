"""
Structured Logging Configuration

This module configures structured logging using the 'structlog' library.
It sets up processors for adding timestamps, log levels, correlation IDs,
and renders logs in JSON (default) or colorized console format based on env.
"""

import os
import logging
import sys
import contextvars
import uuid
import time
import datetime

import structlog
from structlog import dev as structlog_dev
from logging.handlers import RotatingFileHandler

# Context variable for correlation ID
correlation_id_var = contextvars.ContextVar('correlation_id', default=None)

# Shared with the provider-prompt producer so a heading change cannot silently
# disable lead-context redaction in structured diagnostics.
OUTBOUND_LEAD_CONTEXT_MARKER = "## Lead Context (read-only)"
OUTBOUND_LEAD_CONTEXT_REDACTION = "[lead context redacted]"


def get_correlation_id():
    """Get the current correlation ID."""
    return correlation_id_var.get()

def set_correlation_id(value=None):
    """Set the correlation ID."""
    if value is None:
        value = str(uuid.uuid4())
    correlation_id_var.set(value)

def add_correlation_id(logger, method_name, event_dict):
    """Add correlation ID to the log record."""
    correlation_id = get_correlation_id()
    if correlation_id:
        event_dict['correlation_id'] = correlation_id
    return event_dict

def add_service_context(logger, method_name, event_dict):
    """Add service context to the log record."""
    event_dict['service'] = 'ai-engine'
    # Prefer stdlib logger name injected by structlog.stdlib.add_logger_name
    component = event_dict.get('logger')
    if not component:
        # Fallbacks for various logger wrappers
        try:
            component = getattr(getattr(logger, 'logger', None), 'name', None) or getattr(logger, 'name')
        except Exception:
            component = 'unknown'
    event_dict['component'] = component
    return event_dict

def sanitize_secrets(logger, method_name, event_dict):
    """
    Redact sensitive information from log events.
    
    SECURITY: Prevents API keys, passwords, tokens, and other secrets from
    appearing in logs (files, stdout, monitoring systems).
    
    Redaction patterns:
    - API keys: api_key, apikey, api-key
    - Tokens: token, access_token, refresh_token, auth_token, bearer
    - Passwords: password, passwd, pwd
    - Authorization headers: authorization, auth
    - Credentials: credential, secret, private_key
    
    Values are replaced with '***REDACTED***' while preserving log context.
    """
    # List of keys that should be redacted (case-insensitive)
    SENSITIVE_KEYS = {
        'api_key', 'apikey', 'api-key', 'api_keys',
        'token', 'access_token', 'refresh_token', 'auth_token', 'bearer',
        'password', 'passwd', 'pwd', 'pass',
        'authorization', 'auth',
        'credential', 'credentials', 'secret', 'secrets',
        'private_key', 'private-key', 'privatekey',
        'client_secret', 'client-secret', 'clientsecret',
    }

    # Outbound lead context is intentionally appended to provider prompts so the
    # model can use it, but it must not be copied into structured debug logs.
    # The block is always appended at the end of the prompt by the engine.
    def redact_lead_context(value):
        """Remove the appended per-lead context block from logged strings."""
        if not isinstance(value, str):
            return value
        marker_index = value.find(OUTBOUND_LEAD_CONTEXT_MARKER)
        if marker_index < 0:
            return value
        prefix = value[:marker_index]
        return (
            f"{prefix}{OUTBOUND_LEAD_CONTEXT_MARKER}\n"
            f"{OUTBOUND_LEAD_CONTEXT_REDACTION}"
        )
    
    def redact_value(value):
        """Redact a sensitive value, preserving structure for debugging."""
        if value is None:
            return None
        if isinstance(value, bool):
            return value  # Don't redact booleans
        # Preserve type hints but redact content
        if isinstance(value, str):
            if not value:  # Empty string
                return ''
            # Show first 2 chars for debugging (e.g., "sk" for OpenAI keys)
            if len(value) > 4:
                return f"{value[:2]}***REDACTED***"
            return "***REDACTED***"
        if isinstance(value, (int, float)):
            return "***REDACTED***"
        if isinstance(value, (list, tuple)):
            return [redact_value(v) for v in value]
        if isinstance(value, dict):
            return {k: redact_value(v) if k.lower() in SENSITIVE_KEYS else v 
                    for k, v in value.items()}
        return "***REDACTED***"
    
    def sanitize_value(value):
        """Recursively sanitize nested logged values without mutating inputs."""
        if isinstance(value, dict):
            return sanitize_dict(value)
        if isinstance(value, list):
            return [sanitize_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(sanitize_value(item) for item in value)
        return redact_lead_context(value)

    def sanitize_dict(d):
        """Recursively sanitize dictionary keys."""
        if not isinstance(d, dict):
            return d

        # ChannelVarSet encodes the variable name and value as sibling fields,
        # so a key-only secret matcher cannot identify lead context. Apply the
        # pair-aware check at every nesting level and normalize inherited
        # Asterisk variable prefixes before matching.
        variable_name = str(d.get("variable") or "").lstrip("_").upper()
        
        sanitized = {}
        for key, value in d.items():
            if variable_name == "AAVA_CUSTOM_VARS_JSON" and key == "value":
                sanitized[key] = OUTBOUND_LEAD_CONTEXT_REDACTION
                continue

            # Normalize key for comparison (remove separators, lowercase)
            key_normalized = str(key).lower().replace('_', '').replace('-', '')
            
            # Check if key matches any sensitive pattern (exact match only)
            # This prevents false positives like "passthrough" matching "pass"
            is_sensitive = False
            for pattern in SENSITIVE_KEYS:
                pattern_normalized = pattern.replace('_', '').replace('-', '')
                # Match if pattern is the key itself, or ends with pattern (e.g., "user_password")
                if key_normalized == pattern_normalized or key_normalized.endswith(pattern_normalized):
                    is_sensitive = True
                    break
            
            if is_sensitive:
                sanitized[key] = redact_value(value)
            else:
                sanitized[key] = sanitize_value(value)
        return sanitized

    # Sanitize the entire event_dict
    return sanitize_dict(event_dict)

def add_local_timestamp(logger, method_name, event_dict):
    """
    Add a timezone-aware local timestamp.

    This respects the container's timezone configuration (TZ + tzdata), aligning timestamps
    across the stack (Admin UI, ai_engine, local_ai_server) when users set TZ in .env.
    """
    event_dict["timestamp"] = datetime.datetime.now().astimezone().isoformat()
    return event_dict

def configure_logging(log_level="INFO", log_to_file=False, log_file_path="service.log", service_name="ai-engine"):
    """
    Set up structured logging with enhanced context for troubleshooting.

    Environment overrides (optional):
      - LOG_LEVEL: debug|info|warning|error|critical (default: INFO)
      - LOG_FORMAT: json|console (default: json)
      - LOG_COLOR:  0|1 (console only; default: 1)
      - LOG_TO_FILE: 0|1 (default: 0)
      - LOG_FILE_PATH: path (default: service.log)
    """
    # Read env overrides
    env_level = os.getenv("LOG_LEVEL")
    if env_level:
        log_level = env_level.upper()
    try:
        log_to_file = bool(int(os.getenv("LOG_TO_FILE", "0"))) if os.getenv("LOG_TO_FILE") is not None else log_to_file
    except Exception:
        pass
    log_file_path = os.getenv("LOG_FILE_PATH", log_file_path)
    log_format = os.getenv("LOG_FORMAT", "json").strip().lower()
    log_color = os.getenv("LOG_COLOR", "1").strip() not in ("0", "false", "False")

    # Determine when to render tracebacks
    # Default policy: only show stack traces when LOG_LEVEL=debug
    log_level_upper = log_level.upper() if isinstance(log_level, str) else str(log_level)
    tb_mode = os.getenv("LOG_SHOW_TRACEBACKS", "auto").strip().lower()  # auto|always|never
    if tb_mode == "always":
        show_tracebacks = True
    elif tb_mode == "never":
        show_tracebacks = False
    else:
        show_tracebacks = (log_level_upper == "DEBUG")

    def suppress_exc_info_if_disabled(logger, method_name, event_dict):
        """Remove exc_info from event when tracebacks are disabled by policy."""
        if not show_tracebacks and event_dict.get("exc_info"):
            event_dict.pop("exc_info", None)
        return event_dict

    # Derive numeric level for stdlib root logger
    try:
        level_value = getattr(logging, log_level_upper, logging.INFO) if isinstance(log_level, str) else int(log_level)
    except Exception:
        level_value = logging.INFO

    # Configure structlog to integrate with stdlib logging
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.add_log_level,
            add_local_timestamp,
            add_service_context,
            add_correlation_id,
            sanitize_secrets,  # AAVA-37: Redact sensitive information
            suppress_exc_info_if_disabled,
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=structlog.threadlocal.wrap_dict(dict),
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Choose final renderer
    renderer = structlog_dev.ConsoleRenderer(colors=log_color) if log_format == "console" else structlog.processors.JSONRenderer()

    # Stdlib ProcessorFormatter for both structlog and foreign loggers
    processor_formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=[
            structlog.stdlib.add_logger_name,
            structlog.processors.add_log_level,
            add_local_timestamp,
        ],
    )

    # Set up root logger and handlers
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level_value)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(processor_formatter)
    root_logger.addHandler(console_handler)

    if log_to_file:
        try:
            # Allow directory or templated file path. If a directory is provided (endswith slash
            # or path exists and isdir), generate a timestamped file name using service_name.
            ts = time.strftime("%Y%m%d-%H%M%S")
            path = log_file_path
            try:
                looks_like_dir = path.endswith(os.sep) or (os.path.exists(path) and os.path.isdir(path))
            except Exception:
                looks_like_dir = path.endswith(os.sep)
            if looks_like_dir:
                dirpath = path
                if not dirpath.endswith(os.sep):
                    dirpath = dirpath + os.sep
                filename = f"{service_name}-{ts}.log"
                path = os.path.join(dirpath, filename)
            else:
                # Optional placeholder replacement
                if "{ts}" in path:
                    path = path.replace("{ts}", ts)
            # Ensure directory exists
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
            except Exception:
                pass
            file_handler = RotatingFileHandler(
                path, maxBytes=10*1024*1024, backupCount=5
            )
            file_handler.setFormatter(processor_formatter)
            root_logger.addHandler(file_handler)
            root_logger.info("File logging configured", log_file_path=path)
        except Exception as e:
            # Fall back to console-only if file logging fails
            try:
                root_logger.warning(
                    "File logging disabled due to error; continuing with console only",
                    error=str(e),
                    configured_path=log_file_path,
                )
            except Exception:
                pass

    # Reduce noisy third-party loggers
    try:
        logging.getLogger('websockets').setLevel(logging.WARNING)
        logging.getLogger('websockets.client').setLevel(logging.WARNING)
        logging.getLogger('websockets.protocol').setLevel(logging.WARNING)
        logging.getLogger('aiohttp').setLevel(logging.WARNING)
        logging.getLogger('asyncio').setLevel(logging.WARNING)
    except Exception:
        pass

def get_logger(name: str):
    """Get a structlog logger."""
    return structlog.get_logger(name)
