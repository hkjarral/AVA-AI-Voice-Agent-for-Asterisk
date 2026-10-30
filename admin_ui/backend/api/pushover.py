"""Authenticated credential staging and explicit checks for managed Pushover tools.

Tool definitions use the normal tools/in_call_tools config and managed-tool API.
Only opaque immutable file references, never credential values, leave this API.
"""

import asyncio
import json
import re
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Request
from . import config as config_api
from src.integrations.pushover import (
    PushoverToolConfig,
    get_sender,
    render_message,
    delivery_outcome,
    read_key,
)

from src.integrations.pushover_templates import preview_values

router = APIRouter()


async def _body(request):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 128000:
            raise HTTPException(413, "Request is too large")
        raw.extend(chunk)
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except Exception:
        raise HTTPException(422, "Expected a JSON object") from None


async def _credentials(raw):
    if not isinstance(raw, dict) or set(raw) - {"token", "recipient", "recipients"}:
        raise HTTPException(422, "Enter application and recipient credentials")
    recipients = raw.get("recipients", {})
    if (
        not isinstance(recipients, dict)
        or len(recipients) > 16
        or any(
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", str(key))
            for key in recipients
        )
    ):
        raise HTTPException(422, "Invalid recipient identifiers")
    fields = {key: value for key, value in raw.items() if key != "recipients"}
    # Validate the entire batch before writing any secret, without echoing input.
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9]{30}", value)
        for value in [*fields.values(), *recipients.values()]
    ):
        raise HTTPException(422, "Credentials require 30 alphanumeric characters")
    refs = {}

    async def write(value):
        identifier = "pushover-tool-" + uuid4().hex
        await asyncio.to_thread(
            config_api._write_provider_secret, identifier, "api-key", value.encode()
        )
        return config_api._provider_secret_path(identifier, "api-key")

    for field, value in fields.items():
        refs["token_file" if field == "token" else "key_file"] = await write(value)
    if recipients:
        refs["recipients"] = {
            key: {"key_file": await write(value)} for key, value in recipients.items()
        }
    return refs


@router.post("/credentials")
async def stage_credentials(request: Request):
    # Config is staged separately by the usual Update tool → Save & Apply flow.
    return await _credentials(await _body(request))


def _config(raw):
    try:
        return PushoverToolConfig.model_validate(raw)
    except Exception:
        raise HTTPException(422, "Invalid Pushover tool configuration") from None


@router.post("/templates/validate")
async def review_notification_templates(request: Request):
    from src.integrations.pushover_templates import review_templates
    raw = await _body(request)
    config = raw.get("config")
    if not isinstance(config, dict):
        raise HTTPException(422, "Expected notification template settings")
    try:
        return review_templates(config, raw.get("phase"))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, "Invalid notification template settings") from None


@router.post("/preview")
async def preview(request: Request):
    raw = await _body(request)
    cfg = _config(raw.get("config") or {})
    return {
        **render_message(cfg, cfg.phase, preview_values(cfg.phase, cfg.generate_summary)),
        "preview_only": True,
    }


async def _action(request, validate):
    raw = await _body(request)
    cfg = _config(raw.get("config") or {})
    selected = raw.get("recipient_id")
    if selected is not None and (
        not isinstance(selected, str)
        or selected not in cfg.recipients
        or not cfg.recipients[selected].enabled
    ):
        raise HTTPException(422, "Select an enabled recipient")
    credentials = raw.get("credentials") or {}
    if (
        isinstance(credentials, dict)
        and isinstance(credentials.get("recipients", {}), dict)
        and set(credentials.get("recipients", {})) - set(cfg.recipients)
    ):
        raise HTTPException(422, "Recipient no longer exists")
    refs = await _credentials(credentials)
    data = cfg.model_dump()
    data["token_file"] = refs.get("token_file", cfg.token_file)
    for key, ref in refs.get("recipients", {}).items():
        data["recipients"][key].update(ref)
    if "key_file" in refs:
        if len(cfg.recipients) != 1:
            raise HTTPException(422, "Select a recipient row")
        data["recipients"][next(iter(cfg.recipients))]["key_file"] = refs["key_file"]
    cfg = _config(data)
    message = (
        None
        if validate
        else {
            "title": "AVA test notification",
            "message": "Explicit operator-requested AVA test. Acceptance does not prove display or acknowledgement.",
            "priority": cfg.priority,
            **({"sound": cfg.sound} if cfg.sound else {}),
        }
    )
    recipients = {
        key: entry
        for key, entry in cfg.recipients.items()
        if selected is None or key == selected
    }
    deliveries = {
        key: {"label": entry.label, "delivery_status": "disabled"}
        for key, entry in recipients.items()
        if not entry.enabled
    }
    enabled = {key: entry for key, entry in recipients.items() if entry.enabled}
    token = await read_key(cfg.token_file)
    secrets = await asyncio.gather(
        *(read_key(entry.key_file) for entry in enabled.values())
    )
    owners, aliases, targets = {}, {}, {}
    for (key, entry), secret in zip(enabled.items(), secrets):
        identity = (token, secret) if token and secret else None
        if identity and identity in owners:
            aliases[key] = owners[identity]
        else:
            targets[key] = entry
            if identity:
                owners[identity] = key

    async def send(key, entry):
        try:
            result = await get_sender().send(
                token_file=cfg.token_file,
                key_file=entry.key_file,
                message=message,
                timeout_ms=cfg.timeout_ms,
                validate=validate,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            result = {
                "delivery_status": "uncertain",
                "error_code": "transport_unavailable",
            }
        deliveries[key] = {"label": entry.label, **result}

    await asyncio.gather(*(send(key, entry) for key, entry in targets.items()))
    for key, original in aliases.items():
        deliveries[key] = {
            **deliveries[original],
            "label": enabled[key].label,
            "deduplicated": True,
        }
    return {
        **delivery_outcome(deliveries),
        "notification_deliveries": deliveries,
        "credential_refs": refs,
    }


@router.post("/validate")
async def validate_recipient(request: Request):
    return await _action(request, True)


@router.post("/test")
async def send_test(request: Request):
    return await _action(request, False)
