"""
Runtime tool guidance helpers.

Builds compact, provider-agnostic prompt additions that expose configured
telephony inventories (live agents, transfer destinations, voicemail box)
to providers that otherwise only see tool schemas.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())


def _stringify_list(values: Iterable[Any]) -> str:
    rendered = [str(v).strip() for v in values if str(v or "").strip()]
    return ", ".join(rendered)


def _build_live_agent_lines(config: Dict[str, Any]) -> List[str]:
    tools_cfg = (config or {}).get("tools") if isinstance(config, dict) else {}
    internal = ((tools_cfg or {}).get("extensions") or {}).get("internal") or {}
    if not isinstance(internal, dict):
        return []

    lines: List[str] = []
    for key, raw_cfg in internal.items():
        extension = str(key or "").strip()
        cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
        if not extension.isdigit():
            continue
        if cfg.get("transfer") is False:
            continue

        name = str(cfg.get("name") or "").strip()
        aliases = cfg.get("aliases")
        alias_values = aliases if isinstance(aliases, list) else [aliases] if aliases is not None else []
        pieces = [f"- `{extension}`"]
        if name:
            pieces.append(f"name: {name}")
        alias_text = _stringify_list(alias_values)
        if alias_text:
            pieces.append(f"aliases: {alias_text}")
        lines.append(", ".join(pieces))

    return lines


def _build_check_extension_status_lines(config: Dict[str, Any]) -> List[str]:
    tools_cfg = (config or {}).get("tools") if isinstance(config, dict) else {}
    internal = ((tools_cfg or {}).get("extensions") or {}).get("internal") or {}
    transfer_cfg = (tools_cfg or {}).get("transfer") or {}
    destinations = (transfer_cfg or {}).get("destinations") or {}

    allowed: Dict[str, Dict[str, Any]] = {}

    if isinstance(internal, dict):
        for key, raw_cfg in internal.items():
            extension = str(key or "").strip()
            cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
            if not extension.isdigit():
                continue
            if cfg.get("transfer") is False:
                continue
            allowed[extension] = cfg

    if isinstance(destinations, dict):
        for raw_cfg in destinations.values():
            cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
            if str(cfg.get("type") or "").strip().lower() != "extension":
                continue
            extension = str(cfg.get("target") or "").strip()
            if not extension.isdigit():
                continue
            allowed.setdefault(extension, {})

    lines: List[str] = []
    for extension in sorted(allowed.keys(), key=lambda v: int(v)):
        cfg = allowed.get(extension) or {}
        name = str(cfg.get("name") or "").strip()
        aliases = cfg.get("aliases")
        alias_values = aliases if isinstance(aliases, list) else [aliases] if aliases is not None else []
        pieces = [f"- `{extension}`"]
        if name:
            pieces.append(f"name: {name}")
        alias_text = _stringify_list(alias_values)
        if alias_text:
            pieces.append(f"aliases: {alias_text}")
        lines.append(", ".join(pieces))
    return lines


def _build_transfer_destination_lines(config: Dict[str, Any]) -> List[str]:
    tools_cfg = (config or {}).get("tools") if isinstance(config, dict) else {}
    transfer_cfg = (tools_cfg or {}).get("transfer") or {}
    destinations = (transfer_cfg or {}).get("destinations") or {}
    if not isinstance(destinations, dict):
        return []

    lines: List[str] = []
    for key, raw_cfg in destinations.items():
        cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
        destination_key = str(key or "").strip()
        if not destination_key:
            continue
        destination_type = str(cfg.get("type") or "").strip() or "unknown"
        target = str(cfg.get("target") or "").strip()
        description = str(cfg.get("description") or "").strip()
        pieces = [f"- `{destination_key}`", f"type: {destination_type}"]
        if target:
            pieces.append(f"target: {target}")
        if description:
            pieces.append(f"description: {description}")
        if bool(cfg.get("attended_allowed")):
            pieces.append("attended_transfer: allowed")
        if bool(cfg.get("live_agent")):
            pieces.append("live_agent: true")
        lines.append(", ".join(pieces))

    return lines


def _build_attended_destination_lines(config: Dict[str, Any]) -> List[str]:
    tools_cfg = (config or {}).get("tools") if isinstance(config, dict) else {}
    transfer_cfg = (tools_cfg or {}).get("transfer") or {}
    destinations = (transfer_cfg or {}).get("destinations") or {}
    if not isinstance(destinations, dict):
        return []

    lines: List[str] = []
    for key, raw_cfg in destinations.items():
        cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
        if str(cfg.get("type") or "").strip().lower() != "extension":
            continue
        if not bool(cfg.get("attended_allowed", False)):
            continue
        destination_key = str(key or "").strip()
        target = str(cfg.get("target") or "").strip()
        description = str(cfg.get("description") or "").strip()
        pieces = [f"- `{destination_key}`"]
        if target:
            pieces.append(f"target: {target}")
        if description:
            pieces.append(f"description: {description}")
        lines.append(", ".join(pieces))

    return lines


def build_in_call_tool_runtime_guidance(config: Dict[str, Any], allowed_tools: Iterable[str]) -> str:
    """
    Build provider-agnostic runtime prompt guidance for config-backed in-call tools.

    The goal is to expose valid configured targets so providers do not invent
    extension numbers or destination keys.
    """

    allowed = {str(name or "").strip() for name in (allowed_tools or []) if str(name or "").strip()}
    if not allowed:
        return ""

    sections: List[str] = []
    header = [
        "## Runtime Tool Target Inventory",
        "- Never invent extension numbers, destination keys, aliases, queue names, or ring groups.",
        "- Use only the exact configured values listed below when calling telephony tools.",
    ]
    sections.append("\n".join(header))

    if "live_agent_transfer" in allowed:
        live_agent_lines = _build_live_agent_lines(config)
        if live_agent_lines:
            lines = [
                "Configured live agents:",
                *live_agent_lines,
            ]
            lines.append("- Use `live_agent_transfer.target` with one of the listed extensions, names, or aliases.")
            sections.append("\n".join(lines))
        else:
            sections.append(
                "\n".join(
                    [
                        "Configured live agents:",
                        "- None configured. Do not call `live_agent_transfer` unless a live agent is configured.",
                    ]
                )
            )

    if "check_extension_status" in allowed:
        check_lines = _build_check_extension_status_lines(config)
        if check_lines:
            sections.append(
                "\n".join(
                    [
                        "Configured extensions allowed for `check_extension_status`:",
                        *check_lines,
                        "- Only query the listed configured extensions or transfer-destination extension targets.",
                    ]
                )
            )
        else:
            sections.append(
                "\n".join(
                    [
                        "Configured extensions allowed for `check_extension_status`:",
                        "- None configured. Do not call `check_extension_status` unless a live agent or transfer destination is configured.",
                    ]
                )
            )

    if "blind_transfer" in allowed:
        transfer_lines = _build_transfer_destination_lines(config)
        if transfer_lines:
            sections.append(
                "\n".join(
                    [
                        "Configured blind-transfer destinations:",
                        *transfer_lines,
                        "- Use the exact destination key with `blind_transfer.destination` whenever possible.",
                    ]
                )
            )
        else:
            sections.append(
                "\n".join(
                    [
                        "Configured blind-transfer destinations:",
                        "- None configured. Do not call `blind_transfer` unless destinations are configured.",
                    ]
                )
            )

    if "attended_transfer" in allowed:
        attended_lines = _build_attended_destination_lines(config)
        if attended_lines:
            sections.append(
                "\n".join(
                    [
                        "Configured attended-transfer destinations:",
                        *attended_lines,
                        "- Use the exact destination key with `attended_transfer.destination`.",
                    ]
                )
            )
        else:
            sections.append(
                "\n".join(
                    [
                        "Configured attended-transfer destinations:",
                        "- None configured. Do not call `attended_transfer` unless an attended-enabled extension destination exists.",
                    ]
                )
            )

    if "leave_voicemail" in allowed:
        tools_cfg = (config or {}).get("tools") if isinstance(config, dict) else {}
        voicemail_cfg = (tools_cfg or {}).get("leave_voicemail") or {}
        extension = str((voicemail_cfg or {}).get("extension") or "").strip()
        if extension:
            sections.append(
                "\n".join(
                    [
                        "Configured voicemail target:",
                        f"- `leave_voicemail` routes to voicemail box `{extension}`.",
                    ]
                )
            )

    return "\n\n".join(section for section in sections if str(section or "").strip())
