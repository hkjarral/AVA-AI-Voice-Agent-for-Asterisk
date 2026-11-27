"""Validated conversation completion policy. Routing inventory stays in transfer."""
from __future__ import annotations
import re
from typing import Any

ROUTE_TYPES = {"extension", "queue", "ringgroup", "dialplan"}
KEY = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")
UNSAFE = re.compile(r"[,()\r\n\x00]")

def valid_key(value: Any) -> bool:
    # These are existing inventory labels carried as JSON enum values, not
    # function names or dialplan expressions. Preserve spaces and Unicode.
    return isinstance(value, str) and bool(value.strip()) and value == value.strip() and bool(KEY.fullmatch(value))

def executable_route(route: Any) -> bool:
    if not isinstance(route, dict) or not isinstance(route.get("type"), str) or route["type"] not in ROUTE_TYPES:
        return False
    target = route.get("target")
    return (isinstance(target, (str, int)) and not isinstance(target, bool)
            and bool(str(target).strip()) and not UNSAFE.search(str(target)))

def normalize_completion(raw: Any, *, agent: bool = False) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("Conversation completion must be an object")
    if set(raw) - {"default_next_step", "destination_keys"}:
        raise ValueError("Unknown conversation completion setting")
    result = {}
    if "default_next_step" in raw:
        value = raw["default_next_step"]
        if not valid_key(value):
            raise ValueError("Default next step must be hangup or a destination key")
        result["default_next_step"] = value
    if "destination_keys" in raw:
        keys = raw["destination_keys"]
        if not isinstance(keys, list) or any(not valid_key(k) or k == "hangup" for k in keys):
            raise ValueError("Completion destinations must be a list of destination keys; hangup is reserved")
        result["destination_keys"] = list(dict.fromkeys(keys))
    return result

def validate_completion_config(tools: dict) -> None:
    hangup = tools.get("hangup_call") or {}
    if not isinstance(hangup, dict) or not isinstance(tools.get("transfer") or {}, dict):
        raise ValueError("Hangup and transfer settings must be objects")
    policy = normalize_completion(hangup.get("completion"))
    destinations = (tools.get("transfer") or {}).get("destinations") or {}
    if not isinstance(destinations, dict):
        raise ValueError("Transfer destinations must be an object")
    for key, route in destinations.items():
        if not isinstance(route, dict):
            continue
        if "requires_opt_in" in route and not isinstance(route["requires_opt_in"], bool):
            raise ValueError("Destination requires_opt_in must be a boolean")
        if route.get("type") == "dialplan":
            if key == "hangup":
                raise ValueError("hangup is reserved for conversation completion")
            for field, value in (("context", route.get("dialplan_context") or route.get("context")), ("extension", route.get("target"))):
                if not isinstance(value, str) or not value.strip() or UNSAFE.search(value):
                    raise ValueError(f"Dialplan destination {field} is required and must be safe")
            priority = route.get("priority", 1)
            if isinstance(priority, bool) or not isinstance(priority, int) or priority < 1:
                raise ValueError("Dialplan priority must be a positive integer")
            if route.get("attended_allowed") or route.get("live_agent"):
                raise ValueError("Dialplan destinations support blind transfer and completion only")
    live_agent = (tools.get("transfer") or {}).get("live_agent_destination_key")
    if live_agent is not None and not isinstance(live_agent, str):
        raise ValueError("Live agent destination key must be a string")
    if isinstance(destinations.get(live_agent), dict) and destinations[live_agent].get("type") == "dialplan":
        raise ValueError("Live agent override cannot use a dialplan destination")
    default = policy.get("default_next_step", "hangup")
    if default != "hangup":
        route = destinations.get(default)
        if default not in policy.get("destination_keys", []) or not executable_route(route):
            raise ValueError("Default completion destination must be allowed and configured")
        if route.get("requires_opt_in") is True:
            raise ValueError("Opt-in destinations cannot be automatic completion defaults")

def effective_completion(config: dict, override: Any = None) -> dict:
    tools = config.get("tools") or {}
    hangup = tools.get("hangup_call") or {}
    base = normalize_completion(hangup.get("completion"))
    agent = normalize_completion(override, agent=True)
    keys = base.get("destination_keys", [])
    if "destination_keys" in agent:
        keys = [key for key in keys if key in agent["destination_keys"]]
    transfer = tools.get("transfer") or {}
    routes = transfer.get("destinations") or {}
    keys = [key for key in keys if executable_route(routes.get(key))]
    if transfer.get("enabled") is False or hangup.get("enabled") is False:
        keys = []
    default = agent.get("default_next_step", base.get("default_next_step", "hangup"))
    if default != "hangup" and (default not in keys or routes[default].get("requires_opt_in") is True):
        default = "hangup"
    return {"default_next_step": default, "destination_keys": keys}

def resolve_completion(config: dict, requested: Any = None, *, supplied: bool = False) -> dict:
    policy = effective_completion(config)
    key = requested if supplied else policy["default_next_step"]
    result = {"requested_next_step": key if isinstance(key, str) and (key == "hangup" or key in policy["destination_keys"]) else "<invalid>", "next_step": "hangup", "result": "pending"}
    if key == "hangup":
        return result
    if not isinstance(key, str) or key not in policy["destination_keys"]:
        return {**result, "result": "unavailable"}
    route = {field: value for field, value in config["tools"]["transfer"]["destinations"][key].items()
             if field in {"type", "target", "description", "dialplan_context", "context", "priority", "requires_opt_in"}}
    return {**result, "next_step": key, "route": route}
