"""Native Pushover configuration, safe rendering and bounded best-effort transport.

API contract checked against https://pushover.net/api on 2026-10-23.
No request/response body or resolved credential is ever logged or returned.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import time
from string import Formatter
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.tools.summary import DEFAULT_IN_CALL_PROMPT, summary_prompt

PHASES = ("pre_call", "in_call", "post_call")
VARIABLES = frozenset(
    {
        "agent",
        "caller_name",
        "context_name",
        "called_number",
        "call_outcome",
        "call_duration",
        "stage",
        "call_id",
        "caller_number",
        "callback_number",
        "name",
        "identity_source",
        "company",
        "crm_status",
        "destination",
        "outcome",
        "language",
        "meeting",
        "summary",
        "summary_status",
    }
)
DEFAULT_TITLES = {
    "pre_call": "{agent}: incoming call",
    "in_call": "{agent}: call update",
    "post_call": "{agent}: call complete",
}
DEFAULT_BODIES = {
    "pre_call": "Caller: {caller_number}\nCall: {call_id}",
    "in_call": "Caller: {caller_number}\nCall: {call_id}",
    "post_call": "Caller: {caller_number}\nCall: {call_id}\nOutcome: {outcome}",
}


def default_body(phase, *, generate_summary=False):
    """Keep public defaults generic and omit unused summary placeholders."""
    body = DEFAULT_BODIES[phase]
    if phase != "pre_call" and generate_summary:
        body += "\nSummary: {summary}"
    return body


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PhaseSettings(StrictConfig):
    title: str = Field(default="", max_length=2000)
    body: str = Field(default="", max_length=8000)
    generate_summary: bool = False
    summary_provider: str | None = None
    summary_prompt: str = DEFAULT_IN_CALL_PROMPT
    summary_max_words: int = Field(default=100, ge=10, le=1000)
    summary_timeout_ms: int = Field(default=15000, ge=1000, le=120000)
    summary_failure_policy: Literal["continue", "abort"] = "continue"

    @field_validator("title", "body")
    @classmethod
    def template_fields(cls, value):
        for _, name, spec, conversion in Formatter().parse(value):
            if name is not None and (name not in VARIABLES or spec or conversion):
                raise ValueError("Unsupported notification template variable")
        return value

    @model_validator(mode="after")
    def summary_settings(self):
        summary_prompt(self.summary_prompt, self.summary_max_words)
        if self.generate_summary and not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,64}_llm", self.summary_provider or ""
        ):
            raise ValueError("Select a configured summary provider")
        return self


class PushoverRecipient(StrictConfig):
    label: str = Field(default="", max_length=100)
    enabled: bool = True
    key_file: str = ""


class PushoverToolConfig(PhaseSettings):
    kind: Literal["pushover"] = "pushover"
    phase: Literal["pre_call", "in_call", "post_call"]
    enabled: bool = True
    is_global: bool = False
    description: str = Field(
        default="Send the configured internal Pushover notification.", max_length=8000
    )
    token_file: str = ""
    key_file: str = ""
    recipient_label: str = Field(default="", max_length=100)
    recipients: dict[str, PushoverRecipient] = Field(
        default_factory=dict, max_length=16
    )
    priority: Literal[-2, -1, 0, 1] = 0
    sound: str = Field(default="", max_length=64)
    timeout_ms: int = Field(default=5000, ge=100, le=10000)
    destination_key: str = Field(default="", max_length=64)
    suppress_after_tool: str = Field(default="", max_length=64)

    @model_validator(mode="before")
    @classmethod
    def migrate_single_recipient(cls, raw):
        if not isinstance(raw, dict):
            return raw
        data = dict(raw)
        if "recipients" not in data:
            if data.get("key_file") or data.get("recipient_label"):
                data["recipients"] = {
                    "legacy": {
                        "label": data.get("recipient_label", ""),
                        "key_file": data.get("key_file", ""),
                        "enabled": True,
                    }
                }
        else:
            # Explicit empty recipients must not reactivate a removed legacy key.
            data["key_file"], data["recipient_label"] = "", ""
        return data

    @field_validator("recipients")
    @classmethod
    def recipient_identifiers(cls, value):
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", key) for key in value):
            raise ValueError("Invalid recipient identifier")
        return value

    @model_validator(mode="after")
    def validate_tool(self):
        if self.phase == "pre_call" and self.generate_summary:
            raise ValueError("Pre-call notifications do not generate summaries")
        if not re.fullmatch(r"[A-Za-z0-9_-]{0,64}", self.sound):
            raise ValueError("Invalid sound identifier")
        if self.suppress_after_tool and (
            self.phase != "post_call"
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", self.suppress_after_tool)
        ):
            raise ValueError("Select an in-call tool name for post-call suppression")
        return self


def render_message(config, phase, values):
    settings = config

    def render(template, data):
        parts = []
        for literal, name, _, _ in Formatter().parse(template):
            parts.append(literal)
            if name is not None:
                parts.append(str(data.get(name) or "unknown"))
        return "".join(parts)

    title = render(settings.title or DEFAULT_TITLES[phase], values)[:250]
    body_template = settings.body or default_body(
        phase, generate_summary=settings.generate_summary
    )
    base = render(body_template, {**values, "summary": ""})
    count = sum(
        1 for _, name, _, _ in Formatter().parse(body_template) if name == "summary"
    )
    remaining = max(0, 1024 - len(base)) // max(1, count)
    safe_values = {
        **values,
        "summary": str(values.get("summary") or "unavailable")[:remaining],
    }
    return {
        "title": title,
        "message": render(body_template, safe_values)[:1024],
        "priority": config.priority,
        **({"sound": config.sound} if config.sound else {}),
    }


def delivery_outcome(deliveries):
    statuses = {
        row["delivery_status"]
        for row in deliveries.values()
        if row["delivery_status"] != "disabled"
    }
    if not statuses:
        return {"delivery_status": "skipped", "status": "skipped"}
    return {
        "delivery_status": next(iter(statuses)) if len(statuses) == 1 else "partial",
        "status": (
            "success"
            if statuses <= {"accepted", "suppressed"}
            else "skipped" if statuses == {"skipped"} else "error"
        ),
    }


async def read_key(path):
    def read():
        try:
            with open(path, "r", encoding="utf-8") as stream:
                value = stream.read(128).strip()
            return value if re.fullmatch(r"[A-Za-z0-9]{30}", value) else ""
        except (OSError, UnicodeError):
            return ""

    return await asyncio.to_thread(read)


class PushoverSender:
    """Two requests across engine/Admin processes, with at most 16 local waiters.

    File leases use the shared data mount, so UI validation/test requests cannot
    exceed the connection cap alongside engine notification traffic.
    """

    def __init__(self, *, lock_dir=None, client_factory=httpx.AsyncClient):
        self.lock_dir = Path(
            lock_dir or os.getenv("PUSHOVER_LOCK_DIR", "/app/data/pushover/locks")
        )
        self.client_factory = client_factory
        self._semaphore = asyncio.Semaphore(2)
        self._pending = 0
        self._blocked = {}

    @asynccontextmanager
    async def _lease(self):
        def acquire():
            self.lock_dir.mkdir(parents=True, exist_ok=True)
            for slot in range(2):
                fd = os.open(
                    self.lock_dir / f"connection-{slot}.lock",
                    os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return fd
                except BlockingIOError:
                    os.close(fd)
            return None

        fd = None
        try:
            while fd is None:
                # Shield acquisition so cancellation cannot strand an acquired FD.
                task = asyncio.create_task(asyncio.to_thread(acquire))
                try:
                    fd = await asyncio.shield(task)
                except asyncio.CancelledError:

                    def release_acquired(done):
                        try:
                            acquired = done.result()
                            if acquired is not None:
                                os.close(acquired)
                        except Exception:
                            pass

                    task.add_done_callback(release_acquired)
                    raise
                if fd is None:
                    await asyncio.sleep(0.05)
            yield
        finally:
            if fd is not None:
                await asyncio.to_thread(os.close, fd)

    async def send(
        self, *, token_file, key_file, message=None, timeout_ms=5000, validate=False
    ):
        fingerprint = (token_file, key_file)
        if not validate and (
            self._blocked.get(fingerprint, 0) > time.monotonic()
            or self._blocked.get((token_file, "*"), 0) > time.monotonic()
        ):
            return {"delivery_status": "rejected", "error_code": "rejection_cooldown"}
        if self._pending >= 18:
            return {"delivery_status": "capacity", "error_code": "queue_full"}
        self._pending += 1
        dispatched = False

        async def operation():
            nonlocal dispatched
            token, user = await asyncio.gather(read_key(token_file), read_key(key_file))
            if not token or not user:
                return {
                    "delivery_status": "rejected",
                    "error_code": "credentials_unavailable",
                }
            async with self._semaphore, self._lease():
                payload = {"token": token, "user": user, **(message or {})}
                # Authority-bearing fields never come from content or model arguments.
                payload["token"], payload["user"] = token, user
                endpoint = "users/validate" if validate else "messages"
                async with self.client_factory(
                    timeout=timeout_ms / 1000,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    dispatched = True
                    async with client.stream(
                        "POST",
                        f"https://api.pushover.net/1/{endpoint}.json",
                        data=payload,
                    ) as response:
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(raw) + len(chunk) > 16384:
                                return {
                                    "delivery_status": "uncertain",
                                    "error_code": "oversized_response",
                                }
                            raw.extend(chunk)
                        try:
                            data = json.loads(raw)
                        except (ValueError, UnicodeError):
                            data = None
                        if (
                            response.status_code == 200
                            and isinstance(data, dict)
                            and type(data.get("status")) is int
                            and data.get("status") == 1
                        ):
                            return {
                                "delivery_status": "accepted",
                                "http_status": 200,
                            }
                        if (
                            400 <= response.status_code < 500
                            or isinstance(data, dict)
                            and data.get("status") == 0
                        ):
                            if not validate:
                                if len(self._blocked) >= 512:
                                    self._blocked = {
                                        key: expiry
                                        for key, expiry in self._blocked.items()
                                        if expiry > time.monotonic()
                                    }
                                if len(self._blocked) < 512:
                                    blocked_key = (
                                        (token_file, "*")
                                        if response.status_code == 429
                                        else fingerprint
                                    )
                                    self._blocked[blocked_key] = time.monotonic() + 300
                            return {
                                "delivery_status": "rejected",
                                "http_status": response.status_code,
                                "error_code": (
                                    "quota_exceeded"
                                    if response.status_code == 429
                                    else "api_rejected"
                                ),
                            }
                        return {
                            "delivery_status": "uncertain",
                            "http_status": response.status_code,
                            "error_code": "invalid_or_transient_response",
                        }

        try:
            return await asyncio.wait_for(operation(), timeout_ms / 1000)
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, TimeoutError):
            return {
                "delivery_status": "uncertain" if dispatched else "timeout",
                "error_code": "timeout",
            }
        except Exception:
            return {
                "delivery_status": "uncertain" if dispatched else "rejected",
                "error_code": "transport_unavailable",
            }
        finally:
            self._pending -= 1


_sender = None


def get_sender():
    global _sender
    if _sender is None:
        _sender = PushoverSender()
    return _sender
