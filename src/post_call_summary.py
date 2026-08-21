"""Provider-backed post-call summary generation.

This module deliberately keeps the transcript as user data and the operator's
summary instructions as a system prompt.  A configured provider is never
silently substituted: doing so could send a transcript to an unintended
third-party endpoint.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, List

from .logging_config import get_logger
from .pipelines.orchestrator import PipelineOrchestrator

logger = get_logger(__name__)


@dataclass(frozen=True)
class SummaryGenerationResult:
    text: str = ""
    provider: str = ""
    model: str = ""
    status: str = "error"
    duration_ms: float = 0.0
    error_code: str | None = None


class PostCallSummaryService:
    """Generate summaries with an isolated, short-lived configured LLM."""

    def __init__(self, orchestrator: PipelineOrchestrator):
        self._orchestrator = orchestrator

    async def generate(
        self,
        *,
        provider: str,
        call_id: str,
        conversation_history: List[Dict[str, Any]],
        system_prompt: str,
        max_words: int,
        timeout_ms: int,
    ) -> SummaryGenerationResult:
        started = time.monotonic()
        component_key = str(provider or "").strip()
        if not component_key:
            return self._result(started, provider="", status="error", error_code="provider_required")

        transcript = "\n".join(
            f"{str(message.get('role', 'unknown'))}: {str(message.get('content', ''))}"
            for message in (conversation_history or [])
            if isinstance(message, dict) and str(message.get("content", "")).strip()
        )
        if not transcript.strip():
            return self._result(started, provider=component_key, status="skipped", error_code="empty_transcript")

        try:
            text, model = await asyncio.wait_for(
                self._orchestrator.generate_once(
                    component_key=component_key,
                    call_id=f"post-call-summary:{call_id}",
                    transcript=transcript,
                    system_prompt=system_prompt,
                    max_words=max_words,
                    timeout_sec=timeout_ms / 1000.0,
                ),
                timeout=timeout_ms / 1000.0,
            )
            return self._result(
                started,
                provider=component_key,
                model=model,
                status="ok" if text else "error",
                text=text,
                error_code=None if text else "empty_response",
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Post-call summary generation timed out",
                call_id=call_id,
                provider=component_key,
                timeout_ms=timeout_ms,
            )
            return self._result(started, provider=component_key, status="timeout", error_code="timeout")
        except Exception as exc:
            # Do not include exception text: upstream SDK errors can contain a
            # request URL or credential-bearing diagnostic details.
            logger.warning(
                "Post-call summary generation failed",
                call_id=call_id,
                provider=component_key,
                error_type=exc.__class__.__name__,
            )
            return self._result(
                started,
                provider=component_key,
                status="error",
                error_code=exc.__class__.__name__,
            )

    @staticmethod
    def _result(
        started: float,
        *,
        provider: str,
        status: str,
        text: str = "",
        model: str = "",
        error_code: str | None = None,
    ) -> SummaryGenerationResult:
        return SummaryGenerationResult(
            text=text.strip(),
            provider=provider,
            model=model,
            status=status,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
            error_code=error_code,
        )
