"""
Live Agent Transfer Tool - explicit handoff to configured live agent destination.

This tool is intended to mean: the caller explicitly asked for a human/live agent.

Default behavior (v6.2.0+): route to configured Live Agents (`tools.extensions.internal`).
Advanced/legacy override: route via a transfer destination key (`tools.transfer.*`).
"""

from typing import Any, Dict, Optional, Tuple, List, Mapping

import structlog

from src.tools.base import Tool, ToolCategory, ToolDefinition, ToolParameter
from src.tools.context import ToolExecutionContext
from src.tools.telephony.unified_transfer import UnifiedTransferTool

logger = structlog.get_logger(__name__)


class LiveAgentTransferTool(Tool):
    @staticmethod
    def _normalize_text(value: Any) -> str:
        return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="live_agent_transfer",
            description=(
                "Transfer the caller to a live (human) agent. "
                "By default routes to Tools -> Live Agents. Optionally, an advanced/legacy "
                "override can route live-agent requests via a transfer destination. "
                "An optional target can specify the desired live agent extension, name, or alias. "
                "Use only configured live-agent targets exposed in the runtime prompt/context; "
                "never invent extension numbers."
            ),
            category=ToolCategory.TELEPHONY,
            requires_channel=True,
            max_execution_time=30,
            parameters=[
                ToolParameter(
                    name="target",
                    type="string",
                    description=(
                        "Optional target live agent extension number, configured name, or alias "
                        "(for example '6000', 'Live Agent 2', or 'support')."
                    ),
                    required=False,
                ),
            ],
        )

    @staticmethod
    def _resolve_live_agent_destination_key(
        transfer_cfg: Dict[str, Any],
    ) -> Tuple[Optional[str], str]:
        destinations = (transfer_cfg.get("destinations") or {}) if isinstance(transfer_cfg, dict) else {}
        if not isinstance(destinations, dict) or not destinations:
            return None, "no_destinations"

        configured_key = str(transfer_cfg.get("live_agent_destination_key") or "").strip()
        if configured_key:
            cfg = destinations.get(configured_key)
            # Prevent misrouting "live agent" to arbitrary destinations: only accept
            # keys explicitly marked live_agent (or the conventional key name).
            if isinstance(cfg, dict) and (bool(cfg.get("live_agent")) or configured_key == "live_agent"):
                return configured_key, "config.live_agent_destination_key"
            return None, "configured_key_missing"

        return LiveAgentTransferTool._resolve_live_agent_destination_key_from_destinations(transfer_cfg)

    @staticmethod
    def _resolve_live_agent_destination_key_from_destinations(
        transfer_cfg: Dict[str, Any],
    ) -> Tuple[Optional[str], str]:
        """
        Resolve a live-agent destination from `tools.transfer.destinations` without considering
        `tools.transfer.live_agent_destination_key`.

        This is used for legacy fallback when operators did not configure Live Agents.
        It intentionally bypasses the configured override so a misconfigured override doesn't
        prevent finding a valid `destinations.<key>.live_agent: true` entry.
        """
        destinations = (transfer_cfg.get("destinations") or {}) if isinstance(transfer_cfg, dict) else {}
        if not isinstance(destinations, dict) or not destinations:
            return None, "no_destinations"

        live_agent_keys: List[str] = []
        for key, cfg in destinations.items():
            if isinstance(cfg, dict) and bool(cfg.get("live_agent")):
                live_agent_keys.append(str(key))
        if len(live_agent_keys) == 1:
            return live_agent_keys[0], "destinations.<key>.live_agent"
        if len(live_agent_keys) > 1:
            return None, "destinations.live_agent_ambiguous"

        if "live_agent" in destinations:
            return "live_agent", "default.live_agent_key"

        return None, "unconfigured"

    @classmethod
    def _resolve_live_agent_extension_from_internal_config(
        cls,
        extensions_cfg: Dict[str, Any],
    ) -> Tuple[Optional[str], Dict[str, Any], str]:
        if not isinstance(extensions_cfg, dict) or not extensions_cfg:
            return None, {}, "extensions.internal.empty"

        def _numeric_extension_key(key: Any) -> Optional[str]:
            ext = str(key or "").strip()
            if ext and ext.isdigit():
                return ext
            return None

        def _contains_live_agent(cfg: Mapping[str, Any]) -> bool:
            hay = f"{cls._normalize_text(cfg.get('name'))} {cls._normalize_text(cfg.get('description'))}".strip()
            tokens = [t for t in cls._normalize_text("live agent").split() if t]
            return bool(tokens) and all(t in hay for t in tokens)

        explicit_flag: List[Tuple[str, Dict[str, Any]]] = []
        text_match: List[Tuple[str, Dict[str, Any]]] = []
        alias_match: List[Tuple[str, Dict[str, Any]]] = []

        for key, cfg in extensions_cfg.items():
            extension = _numeric_extension_key(key)
            if not extension or not isinstance(cfg, dict):
                continue
            # Treat missing 'transfer' as enabled (UI defaults to true).
            if cfg.get("transfer") is False:
                continue

            if bool(cfg.get("live_agent")):
                explicit_flag.append((extension, dict(cfg)))

            if _contains_live_agent(cfg):
                text_match.append((extension, dict(cfg)))

            aliases = cfg.get("aliases")
            alias_values = aliases if isinstance(aliases, list) else [aliases] if aliases is not None else []
            if any(cls._normalize_text(alias) == "live agent" for alias in alias_values):
                alias_match.append((extension, dict(cfg)))

        if len(explicit_flag) == 1:
            ext, entry = explicit_flag[0]
            return ext, entry, "extensions.internal.live_agent_flag"
        if len(explicit_flag) > 1:
            return None, {}, "extensions.internal.live_agent_flag_ambiguous"

        if len(text_match) == 1:
            ext, entry = text_match[0]
            return ext, entry, "extensions.internal.text_live_agent"
        if len(text_match) > 1:
            return None, {}, "extensions.internal.text_live_agent_ambiguous"

        if len(alias_match) == 1:
            ext, entry = alias_match[0]
            return ext, entry, "extensions.internal.alias_live_agent"
        if len(alias_match) > 1:
            return None, {}, "extensions.internal.alias_live_agent_ambiguous"

        return None, {}, "extensions.internal.unconfigured"

    @staticmethod
    def _map_extension_to_transfer_destination_key(
        extension: str,
        transfer_cfg: Dict[str, Any],
    ) -> Optional[str]:
        destinations = (transfer_cfg.get("destinations") or {}) if isinstance(transfer_cfg, dict) else {}
        matches: List[str] = []

        for key, cfg in destinations.items():
            if not isinstance(cfg, dict):
                continue
            # Only map to destinations explicitly marked as live-agent (or the conventional key),
            # otherwise we risk labeling a live-agent transfer as "Support agent", etc.
            if key != "live_agent" and not bool(cfg.get("live_agent")):
                continue
            transfer_type = str(cfg.get("type", "") or "").strip().lower()
            target = str(cfg.get("target", "") or "").strip()
            if transfer_type == "extension" and target == extension:
                matches.append(str(key))

        if len(matches) == 1:
            return matches[0]

        return None

    @classmethod
    def _resolve_explicit_target_extension(
        cls,
        *,
        target: str,
        extensions_cfg: Dict[str, Any],
    ) -> Tuple[Optional[str], Dict[str, Any], str]:
        if not isinstance(extensions_cfg, dict) or not extensions_cfg:
            return None, {}, "extensions.internal.empty"

        normalized_target = cls._normalize_text(target)
        if not normalized_target:
            return None, {}, "parameter.target.empty"

        def _extension_key(key: Any) -> Optional[str]:
            ext = str(key or "").strip()
            if ext and ext.isdigit():
                return ext
            return None

        direct_numeric = str(target or "").strip()
        if direct_numeric.isdigit():
            for key, cfg in extensions_cfg.items():
                if str(key or "").strip() != direct_numeric or not isinstance(cfg, dict):
                    continue
                if cfg.get("transfer") is False:
                    return None, {}, "extensions.internal.target_transfer_disabled"
                return direct_numeric, dict(cfg), "parameter.target.extension"

        name_matches: List[Tuple[str, Dict[str, Any]]] = []
        alias_matches: List[Tuple[str, Dict[str, Any]]] = []

        for key, cfg in extensions_cfg.items():
            extension = _extension_key(key)
            if not extension or not isinstance(cfg, dict):
                continue
            if cfg.get("transfer") is False:
                continue

            name = cls._normalize_text(cfg.get("name"))
            if name and name == normalized_target:
                name_matches.append((extension, dict(cfg)))

            aliases = cfg.get("aliases")
            alias_values = aliases if isinstance(aliases, list) else [aliases] if aliases is not None else []
            for alias in alias_values:
                if cls._normalize_text(alias) == normalized_target:
                    alias_matches.append((extension, dict(cfg)))
                    break

        if len(name_matches) == 1:
            ext, cfg = name_matches[0]
            return ext, cfg, "extensions.internal.target_name"
        if len(name_matches) > 1:
            return None, {}, "extensions.internal.target_name_ambiguous"

        if len(alias_matches) == 1:
            ext, cfg = alias_matches[0]
            return ext, cfg, "extensions.internal.target_alias"
        if len(alias_matches) > 1:
            return None, {}, "extensions.internal.target_alias_ambiguous"

        return None, {}, "extensions.internal.target_unconfigured"

    async def execute(self, parameters: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]:
        unified = UnifiedTransferTool()
        transfer_cfg = context.get_config_value("tools.transfer") or {}
        if isinstance(transfer_cfg, dict) and transfer_cfg.get("enabled") is False:
            return {"status": "failed", "message": "Transfer service is disabled"}

        target = str((parameters or {}).get("target", "") or "").strip()
        extensions_cfg = context.get_config_value("tools.extensions.internal") or {}

        # 0) Explicit override: if an operator configured a live_agent_destination_key, use it.
        configured_key = str(transfer_cfg.get("live_agent_destination_key") or "").strip() if isinstance(transfer_cfg, dict) else ""
        if configured_key:
            destination_key, source = self._resolve_live_agent_destination_key(transfer_cfg)
            if destination_key:
                logger.info(
                    "Executing live agent transfer via configured destination override",
                    call_id=context.call_id,
                    destination_key=destination_key,
                    resolution_source=source,
                )
                return await unified.execute({"destination": destination_key}, context)
            logger.warning(
                "Live agent destination override misconfigured; falling back to Live Agents",
                call_id=context.call_id,
                configured_key=configured_key,
                resolution_source=source,
            )

        # 1) Explicit target: transfer to the chosen configured live agent/extension.
        if target:
            extension, ext_entry, ext_source = self._resolve_explicit_target_extension(
                target=target,
                extensions_cfg=extensions_cfg,
            )
            if extension:
                display_name = str(ext_entry.get("name", "") or "").strip()
                display_desc = str(ext_entry.get("description", "") or "").strip()
                description = display_name or display_desc or f"Extension {extension}"

                logger.info(
                    "Executing live agent transfer via explicit target",
                    call_id=context.call_id,
                    target=target,
                    extension=extension,
                    resolution_source=ext_source,
                )
                return await unified._transfer_to_extension(context, extension, description)

            if ext_source.endswith("_ambiguous"):
                logger.warning(
                    "Explicit live agent target is ambiguous",
                    call_id=context.call_id,
                    target=target,
                    resolution_source=ext_source,
                )
                return {
                    "status": "failed",
                    "message": (
                        f"'{target}' matches multiple Live Agents. Please specify the extension number "
                        "or a more specific configured name."
                    ),
                }

            if ext_source.endswith("_transfer_disabled"):
                logger.warning(
                    "Explicit live agent target is not transfer-enabled",
                    call_id=context.call_id,
                    target=target,
                    resolution_source=ext_source,
                )
                return {
                    "status": "failed",
                    "message": f"Live agent target '{target}' is configured but not enabled for transfers.",
                }

            logger.warning(
                "Explicit live agent target not configured",
                call_id=context.call_id,
                target=target,
                resolution_source=ext_source,
            )
            return {
                "status": "failed",
                "message": (
                    f"Live agent target '{target}' is not configured. "
                    "Use a configured extension number, name, or alias from Tools -> Live Agents."
                ),
            }

        # 2) Default: route to Live Agents (tools.extensions.internal) if configured.
        extension, ext_entry, ext_source = self._resolve_live_agent_extension_from_internal_config(extensions_cfg)
        if extension:
            display_name = str(ext_entry.get("name", "") or "").strip()
            display_desc = str(ext_entry.get("description", "") or "").strip()
            description = display_name or display_desc or "Live agent"

            logger.info(
                "Executing live agent transfer via Live Agents",
                call_id=context.call_id,
                extension=extension,
                resolution_source=ext_source,
            )
            return await unified._transfer_to_extension(context, extension, description)

        if ext_source.endswith("_ambiguous"):
            logger.warning(
                "Live agent transfer ambiguous; refusing to fall back to transfer destinations",
                call_id=context.call_id,
                resolution_source=ext_source,
            )
            return {
                "status": "failed",
                "message": (
                    "Multiple Live Agents appear configured, but there is no single default. "
                    "Either leave only one Live Agent enabled, or set tools.transfer.live_agent_destination_key "
                    "to explicitly route live-agent requests via a transfer destination."
                ),
            }

        # 3) Fallback (legacy): if no Live Agents are configured, allow transfer-destination based routing.
        destination_key, source = self._resolve_live_agent_destination_key_from_destinations(transfer_cfg)
        if destination_key:
            logger.info(
                "Executing live agent transfer via destination fallback",
                call_id=context.call_id,
                destination_key=destination_key,
                resolution_source=source,
            )
            return await unified.execute({"destination": destination_key}, context)

        logger.warning(
            "Live agent transfer not configured",
            call_id=context.call_id,
            resolution_source=source,
        )
        return {
            "status": "failed",
            "message": (
                "Live agent transfer is not configured. "
                "Configure Tools -> Live Agents, or set tools.transfer.live_agent_destination_key, "
                "or mark a transfer destination as live_agent."
            ),
        }
