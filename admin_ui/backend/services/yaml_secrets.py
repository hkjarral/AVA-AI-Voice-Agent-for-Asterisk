"""Mask inline YAML credentials and resolve editor markers against their own path."""

from copy import copy
from io import StringIO
import hashlib
import hmac
import json
import os
import secrets
import re
from urllib.parse import parse_qsl, quote, unquote, urlsplit

from ruamel.yaml import YAML

from services.credential_spans import credential_spans, is_reference, private_name


_PREFIX = "«secret:"
_MISSING = object()
# A configured deployment key survives worker restarts. In an unconfigured
# process, a restart invalidates open editor markers and requires a reload.
_configured_key = os.getenv("JWT_SECRET", "").strip()
_SIGNING_KEY = (hmac.digest(_configured_key.encode(), b"admin-yaml-markers-v1", "sha256")
                if _configured_key and _configured_key not in {"changeme", "change-me-please", "dev-secret-key-change-in-prod"}
                else secrets.token_bytes(32))
_MARKER = re.compile(r"«secret:([^«»~]*)~(-?\d+)~([0-9a-f]{24})~([0-9a-f]{24})(?:~([0-9a-f]{24}))?~([0-9a-f]{24})»")
_PUBLIC_HEADERS = {"accept", "content-type", "accept-encoding", "accept-language", "user-agent"}
# These schema fields identify credentials, destinations, or other resources.
_SELECTORS = {
    "api_key_file", "api_key_env", "api_key_ref", "password_env", "credentials_path",
    "key_file", "token_file", "bearer_token_env", "bearer_token_file",
    "destination_key", "live_agent_destination_key", "appointment_key", "default_mailbox_key", "token_cache_path",
}
_TOOLS = {"tools", "tool_overrides", "in_call_http_tools", "pre_call_tools", "post_call_tools", "in_call_tools"}
_AGENT_ROWS = "__ava_agent_rows__"
_AUTH_HEADERS = {"authorization", "proxy_authorization", "cookie", "set_cookie"}


def _field_keys(path):
    # Instance IDs can be arbitrary text, including "prompt" and "api_token".
    owner = _owner(path)
    if owner:
        return path[len(owner):]
    if path and path[0] in _TOOLS | {"providers", "contexts"}:
        return path[2:]
    if path[:2] == ("mcp", "servers"):
        return path[3:]
    return path


def _secret_field(path):
    # Per-agent voicemail's validated mailbox key selects a configured mailbox.
    if path[:1] == (_AGENT_ROWS,) and path[2:] == ("tool_configs_json", "voicemail", "mailbox_key"):
        return False
    path = _field_keys(path)
    if not path or not isinstance(path[-1], str):
        return False
    field = path[-1].lower()
    parent = str(path[-2]).lower() if len(path) > 1 else ""
    if parent in {"headers", "extra_headers"} or parent.endswith("_extra_headers"):
        return field not in _PUBLIC_HEADERS
    if parent in {"env", "query_params"}:
        return True
    name = field.replace("-", "_")
    return name not in _SELECTORS and (private_name(field) or name in _AUTH_HEADERS or name == "service_account_json")


def _name(path):
    # Encode separators in mapping keys so "a.b" cannot stand for two levels.
    parts = []
    for key in path:
        if isinstance(key, int):
            parts.append(f"[{key}]")
        else:
            parts.append(("." if parts else "") + quote(str(key), safe="").replace(".", "%2E").replace("~", "%7E"))
    return "".join(parts)


def _mac(data):
    return hmac.new(_SIGNING_KEY, data.encode(), hashlib.sha256).hexdigest()[:24]


def _fingerprint(value):
    return _mac("value:" + json.dumps(value, sort_keys=True, default=str, ensure_ascii=False))


def _marker(path, index, value, destination, tool_revision=None):
    payload = f"{_name(path)}~{index}~{_fingerprint(value)}~{destination}"
    if tool_revision:
        payload += "~" + tool_revision
    return f"{_PREFIX}{payload}~{_mac('marker:' + payload)}»"


def _walk(value, visit, path=(), active=None):
    replacement = visit(path, value)
    if replacement is not _MISSING:
        return replacement
    if not isinstance(value, (dict, list)):
        return value
    if active is None:
        active = set()
    if id(value) in active:
        raise ValueError("Configuration contains a recursive YAML alias")
    active.add(id(value))
    try:
        result = value
        for key, child in (value.items() if isinstance(value, dict) else enumerate(value)):
            updated = _walk(child, visit, path + (key,), active)
            if updated is not child:
                if result is value:
                    result = copy(value)
                result[key] = updated
        return result
    finally:
        active.remove(id(value))


def _is_tool_url(path, value=None):
    if not path or not isinstance(path[-1], str):
        return False
    field = path[-1].lower()
    if field.endswith("webhook_url"):
        return True
    owner = _owner(path)
    if not owner or _receiver_kind(owner) not in {"tool", "mcp"}:
        return False
    if _secret_field(path) or _prose(path):
        return False
    return (field in {"url", "uri", "endpoint"} or field.endswith(("_url", "_uri", "_endpoint"))
            or isinstance(value, str) and bool(re.match(r"^(?:https?|wss?)://[^\s]+$", value, re.I)))


def url_origin(value):
    """An opaque or environment-supplied authority has no displayable origin."""
    if not isinstance(value, str):
        return ""
    try:
        u = urlsplit(value)
        if not u.hostname or u.scheme not in {"http", "https", "ws", "wss"} or any(c in u.netloc for c in "{}\\"):
            return ""
        host = u.hostname
        if ":" in host:
            host = f"[{host}]"
        return f"{u.scheme}://{host}" + (f":{u.port}" if u.port is not None else "")
    except ValueError:
        return ""


def _prose(path):
    return any(isinstance(k, str) and k.lower().split("_")[-1] in {
        "prompt", "greeting", "description", "instructions",
    } for k in _field_keys(path))


def _template(path):
    return any(isinstance(k, str) and (k in {"args", "command", "body", "payload", "templates"} or k.endswith("_template")) for k in _field_keys(path))


def field_classification(path):
    """Structural policy shared by config editors and their schema inventory."""
    if _is_tool_url(path):
        return "stored URL"
    if _secret_field(path):
        return "credential"
    if _prose(path):
        return "visible prose"
    if _template(path):
        return "template or arguments"
    return "connection userinfo only"


def _flag_value(config, path):
    if len(path) < 2 or path[-2] not in ("args", "command") or not isinstance(path[-1], int) or path[-1] < 1:
        return False
    node = config
    for key in path[:-1]:
        node = node[key]
    flag = node[path[-1]-1]
    return isinstance(flag, str) and flag.startswith("-") and "=" not in flag and private_name(flag.lstrip("-"))


def _field_spans(path, value):
    owner = _owner(path)
    provider_url = bool(owner and _receiver_kind(owner) == "provider"
                        and isinstance(value, str) and re.match(r"^(?:https?|wss?)://", value, re.I))
    return credential_spans(value, template=_template(path) and not _prose(path),
                            provider_url=provider_url)


def _stored_paths(config):
    whole = {}
    parts = {}

    def collect(path, value):
        policy = field_classification(path)
        private_field = policy == "credential" or _flag_value(config, path)
        if ((_is_tool_url(path, value) and value is not None) or path[:-1] in whole
                or (private_field and not is_reference(value))):
            whole[path] = value
        elif ranges := _field_spans(path, value):
            parts[path] = [(a, b, value[a:b]) for a, b in ranges]
        return _MISSING

    _walk(config, collect)
    # An anchor can expose the same credential under an ordinary field name.
    aliased = {id(value) for value in whole.values() if isinstance(value, (str, dict, list)) and value}
    def aliases(path, value):
        if id(value) in aliased and not _prose(path):
            whole[path] = value
        return _MISSING
    _walk(config, aliases)
    return whole, parts


def credential_literals(config):
    """Values a probe receiver or diagnostic can reflect at an unrelated path."""
    whole, parts = _stored_paths(config)
    values = {value for value in whole.values() if isinstance(value, str) and value}
    for spans in parts.values():
        values.update(secret for _, _, secret in spans)
    for path, value in whole.items():
        if isinstance(value, str):
            values.update(value[a:b] for a, b in credential_spans(value, template=True))
            if _is_tool_url(path, value) and url_origin(value):
                u = urlsplit(value)
                values.update(part for part in (u.password, u.username, u.path.lstrip("/"), u.query) if part)
                values.update(unquote(part) for part in u.path.split("/") if part)
                values.update(v for _, v in parse_qsl(u.query) if v)
    return values


def omit_private_fields(config):
    """Drop credential-bearing fields from a copy intended for portable export."""
    whole, embedded = _stored_paths(config)
    private = set(whole) | set(embedded)
    # Removing a positional argument could assign another value to its flag.
    # Omit the enclosing list whenever one of its entries contains a credential.
    for path in tuple(private):
        node = config
        for index, key in enumerate(path):
            if isinstance(node, list):
                private.add(path[:index])
                break
            node = node[key]

    def copy_public(node, path):
        if isinstance(node, dict):
            return {k: copy_public(v, path + (k,)) for k, v in node.items() if path + (k,) not in private}
        if isinstance(node, list):
            return [copy_public(v, path + (i,)) for i, v in enumerate(node)]
        return node

    return copy_public(config, ())


def mask_config(config, *, raw=False, display_only=False):
    whole, parts = _stored_paths(config)
    destinations = {}
    revisions = {}
    def marker(path, index, secret):
        owner = _owner(path)
        if owner not in destinations:
            destinations[owner] = _fingerprint(_destination(config, owner))
            revisions[owner] = _tool_revision(config, owner, whole, parts)
        return _marker(path, index, secret, destinations[owner], revisions[owner])

    def mask(path, value):
        if path in whole:
            if _is_tool_url(path, value):
                origin = url_origin(value)
                if display_only:
                    return origin
                if raw:
                    return (origin + "/" if origin else "") + marker(path, -1, value)
                return {"origin": origin, "set": bool(value)}
            if display_only:
                return "<redacted>"
            return marker(path, -1, value) if raw else {"set": value is not None and value != ""}
        if path in parts:
            for index in reversed(range(len(parts[path]))):
                start, end, secret = parts[path][index]
                replacement = "<redacted>" if display_only else marker(path, index, secret)
                value = value[:start] + replacement + value[end:]
            return value
        return _MISSING
    return _walk(config, mask)


def has_markers(value):
    if isinstance(value, str):
        return _PREFIX in value
    if isinstance(value, dict):
        return set(value) in ({"set"}, {"origin", "set"}) or any(has_markers(item) for item in value.values())
    if isinstance(value, list):
        return any(has_markers(item) for item in value)
    return False


def _owner(path):
    # Only a node's descendants are its fields. A map key naming the tool or
    # server itself can be any string, including a credential-shaped name.
    owner = ()
    index = 0
    while index < len(path) - 2:
        key = path[index]
        if (key in _TOOLS or key == "providers" and index == 0 or
                key == "tools_json" and path[:1] == (_AGENT_ROWS,) and index == 2):
            owner = path[:index + 2]
            index += 2
        elif key == "servers" and index and path[index - 1] in {"mcp", "mcp_json"}:
            owner = path[:index + 2]
            index += 2
        else:
            index += 1
    if not owner and len(path) >= 4 and path[0] == _AGENT_ROWS:
        owner = path[:3]
    return owner


def _receiver_kind(owner):
    if owner[-2] in _TOOLS or owner[-2] == "tools_json":
        return "tool"
    if owner[-2] == "servers" or owner[-1] == "mcp_json":
        return "mcp"
    if owner[0] == "providers":
        return "provider"
    return "agent"


def _origin_identity(location):
    # Opaque/environment-supplied authorities have to match the complete
    # configured expression. Never resolve a secret to compare destinations.
    origin = url_origin(location)
    if not origin:
        return location
    scheme = urlsplit(origin).scheme
    return origin.removesuffix(":443" if scheme in {"https", "wss"} else ":80")


def _tool_revision(document, owner, whole, parts):
    """Bind a YAML tool's name to its definition, including its complete receiver URL."""
    if not owner or owner[0] == _AGENT_ROWS or _receiver_kind(owner) != "tool":
        return None
    for key in owner:
        document = document[key]
    fields = {}

    def record(path, value):
        name = _name(path[len(owner):])
        if path in whole:
            # The endpoint itself identifies the receiver, even on a shared host.
            # Only its keyed digest leaves the server.
            fields[name] = ["receiver", value] if _is_tool_url(path, value) else ["credential"]
            return value
        if path in parts:
            for start, end, _ in reversed(parts[path]):
                value = value[:start] + "<redacted>" + value[end:]
        if isinstance(value, (dict, list)):
            fields[name] = ["map" if isinstance(value, dict) else "list"]
        else:
            fields[name] = ["value", value]
        return _MISSING

    _walk(document, record, owner)
    return _fingerprint(fields)


def _destination(document, owner):
    if not owner:
        return {}
    for key in owner:
        if isinstance(document, list) and isinstance(key, int) and 0 <= key < len(document):
            document = document[key]
        else:
            document = document.get(key, {}) if isinstance(document, dict) else {}
    if not isinstance(document, dict):
        return {}
    kind = _receiver_kind(owner)
    if kind in {"tool", "agent"}:
        location = document.get("url") or document.get("agent_url") or document.get("webhook_url") or ""
        settings = {"origin": _origin_identity(location)}
    elif kind == "mcp":
        settings = {"transport": document.get("transport") or "stdio"}
        if document.get("url"):
            settings["url"] = _origin_identity(document["url"])
        if settings["transport"] == "stdio":
            for key in ("command", "args", "cwd"):
                if document.get(key):
                    settings[key] = document[key]
    else:
        from src.config.provider_instances import credential_provider_kind
        settings = {"provider_type": credential_provider_kind(str(owner[1]), document)}
        for key, value in document.items():
            if not isinstance(key, str) or value is None or value == "":
                continue
            if key in {"url", "endpoint", "proxy"} or key.endswith(("_url", "_endpoint")):
                settings[key] = _origin_identity(value)
            elif key in {"host", "port", "region", "vertex_location"} or key.endswith(("_host", "_region")):
                settings[key] = value
        if document.get("use_vertex_ai"):
            settings["use_vertex_ai"] = True
    for key, headers in document.items():
        if isinstance(key, str) and (key in {"headers", "extra_headers"} or key.endswith("_extra_headers")) and isinstance(headers, dict):
            host = next((v for k, v in headers.items() if str(k).lower() == "host"), None)
            if host is not None:
                settings[key + ".host"] = host
    if kind in {"tool", "mcp", "agent"}:
        def endpoints(path, value):
            full = owner + path
            if path and _owner(full) == owner and _is_tool_url(full, value) and value:
                settings["endpoint:" + _name(path)] = _origin_identity(value)
            return _MISSING
        _walk(document, endpoints)
    return settings


# Only fixed field names can reach a refusal. In particular, header and env
# map keys, tool/instance IDs and credential reference strings are user data.
_CREDENTIAL_SOURCES = {"api_key_env", "api_key_file", "api_key_ref", "password_env", "credentials_path",
                       "bearer_token_env", "bearer_token_file", "agent_id_file", "token_file", "key_file"}
_DIAGNOSTIC_FIELDS = _CREDENTIAL_SOURCES | {
    "url", "agent_url", "webhook_url", "api_key", "password", "token", "secret", "credentials",
    "headers", "extra_headers", "ws_extra_headers", "env", "query_params", "body", "payload",
    "body_template", "payload_template", "args", "command", "service_account_json",
}


def _retained_field(path):
    return next((key for key in _field_keys(path) if key in _DIAGNOSTIC_FIELDS), "credential field")


class CredentialDestinationError(ValueError):
    """A public refusal identifying only the affected configuration fields."""

    def __init__(self, fields):
        self.public_message = "Re-enter the stored value for the new destination in " + ", ".join(fields) + "."
        super().__init__(self.public_message)


def _credential_sources(document, path, value):
    """Identify the saved source independently of the syntax used to select it."""
    if not _owner(path) or not isinstance(value, str) or not value:
        return ()
    private = _secret_field(path)
    if not private and path[-1] in _CREDENTIAL_SOURCES:
        return (("env" if path[-1].endswith("_env") else "file", value),)
    if not private and not _flag_value(document, path):
        return ()
    if "${" in value:
        from src.config.loaders import substitute_braced_env_vars
        names = set()

        def collect(name, operator, default, original):
            names.add(("env", name))
            return original

        substitute_braced_env_vars(value, collect)
        return names
    if is_reference(value):
        return (("env", value.rsplit("$", 1)[1]),)
    return ()


def credential_references(document):
    """Find explicit credential sources without reading their contents."""
    references = {}

    def collect(path, value):
        sources = _credential_sources(document, path, value)
        if sources:
            references[path] = sources
        return _MISSING

    _walk(document, collect)
    return references


def _field_shape(name):
    # Only collection indexes and instance IDs may move. Escaped dots are
    # literal key characters and therefore never match the delimiters below.
    name = re.sub(r"^((?:providers|tools|pre_call_tools|post_call_tools|in_call_tools|mcp\.servers)\.)[^.]+", r"\1*", name)
    name = re.sub(r"^(?:pre_call_tools|post_call_tools|in_call_tools)\.", "tools.", name)
    return re.sub(r"\[\d+\]", "[]", name)


def restore_config(edited, current):
    """Resolve authenticated markers, accepting a move only for the same live value."""
    whole, parts = _stored_paths(current)
    entries = {(_name(path), -1): (path, value) for path, value in whole.items()}
    entries.update({(_name(path), index): (path, part[2])
                    for path, values in parts.items() for index, part in enumerate(values)})
    bindings = []
    by_fingerprint = {}
    references = credential_references(current)
    source_destinations = {}
    source_revisions = {}

    def saved_destination(owner):
        if owner not in source_destinations:
            source_destinations[owner] = _fingerprint(_destination(current, owner))
        return source_destinations[owner]

    source_owners = {}
    for path, sources in references.items():
        owner = _owner(path)
        for source in sources:
            source_owners.setdefault(source, set()).add(owner)

    def mcp_server_label(owner):
        if len(owner) == 5 and owner[0] == _AGENT_ROWS and owner[2:4] == ("mcp_json", "servers"):
            servers = edited[_AGENT_ROWS][owner[1]]["mcp_json"]["servers"]
            if isinstance(servers, dict) and owner[-1] in servers:
                # A submitted server name can itself contain private text.
                return f"MCP server {list(servers).index(owner[-1]) + 1}"
        return None

    def bind(path, destinations):
        bindings.append((_owner(path), _retained_field(path), destinations))

    def restore(path, value):
        owner = _owner(path)
        for source in _credential_sources(edited, path, value):
            owners = source_owners.get(source, ())
            if owner in owners:
                bind(path, {saved_destination(owner)})
            elif owners:
                bind(path, {saved_destination(previous) for previous in owners})
        if path[:1] == (_AGENT_ROWS,) and isinstance(value, dict) and set(value) in ({"set"}, {"origin", "set"}):
            # Agent editors receive a signed, row-bound string. An unsigned
            # YAML presence flag cannot stand in for a stored agent value.
            raise ValueError("A signed stored agent marker is required. Reload the agent.")
        if isinstance(value, dict) and set(value) == {"origin", "set"}:
            if (not _is_tool_url(path, whole.get(path)) or path not in whole or type(value["set"]) is not bool
                    or value["origin"] != url_origin(whole[path]) or value["set"] != bool(whole[path])):
                raise ValueError("Invalid stored URL. Re-enter the complete URL for the new destination.")
            if whole[path]:
                bind(path, {saved_destination(owner)})
            return whole[path]
        if isinstance(value, dict) and set(value) == {"set"}:
            if type(value["set"]) is bool and path in whole and not _is_tool_url(path, whole.get(path)):
                if owner and whole[path]:
                    bind(path, {saved_destination(owner)})
                return whole[path]
            raise ValueError("No stored credential for the presence marker")
        if not isinstance(value, str) or _PREFIX not in value:
            return _MISSING
        offsets = []
        result = []
        cursor = 0
        for match in _MARKER.finditer(value):
            origin, number, fingerprint, destination, revision, tag = match.groups()
            index = int(number)
            payload = f"{origin}~{number}~{fingerprint}~{destination}"
            if revision:
                payload += "~" + revision
            entry = entries.get((origin, index))
            if not hmac.compare_digest(tag, _mac("marker:" + payload)) or _PREFIX in value[cursor:match.start()]:
                raise ValueError("Invalid secret placeholder. Reload the configuration.")
            if origin != _name(path):
                if label := mcp_server_label(owner):
                    raise ValueError(f"{label} cannot retain a moved credential. Re-enter it or reload the agent.")
                if _field_shape(origin) != _field_shape(_name(path)):
                    raise ValueError("A moved credential belongs to a different field.")
                if entry is None or _fingerprint(entry[1]) != fingerprint:
                    if not by_fingerprint:
                        for (name, slot), candidate in entries.items():
                            by_fingerprint[(_field_shape(name), slot, _fingerprint(candidate[1]))] = candidate
                    entry = by_fingerprint.get((_field_shape(origin), index, fingerprint))
                if index == -1 and not (_secret_field(path) or _is_tool_url(path) or _flag_value(edited, path)):
                    raise ValueError("A stored credential requires a secret destination field.")
            if entry is None:
                raise ValueError("The stored credential changed. Reload the configuration.")
            source_path, secret = entry
            if path[:1] == (_AGENT_ROWS,) and (origin != _name(path) or _fingerprint(secret) != fingerprint):
                raise ValueError("The stored agent field changed. Reload the agent.")
            if (owner and owner[0] != _AGENT_ROWS and _receiver_kind(owner) == "tool"
                    and origin == _name(path) and _fingerprint(secret) != fingerprint):
                if owner not in source_revisions:
                    source_revisions[owner] = _tool_revision(current, owner, whole, parts)
                if revision != source_revisions[owner]:
                    raise ValueError("The stored tool changed. Reload the configuration.")
            if owner and secret:
                bind(path, {destination} & {saved_destination(_owner(source_path))})
            if index == -1:
                if _is_tool_url(path, secret):
                    origin_text = url_origin(secret)
                    expected = (origin_text + "/" if origin_text else "") + match.group()
                    if value != expected:
                        raise ValueError("Re-enter the complete URL for the new destination.")
                    return secret
                if match.span() != (0, len(value)):
                    raise ValueError("A whole credential placeholder must occupy its original field.")
                return secret
            result.append(value[cursor:match.start()])
            offsets.append((sum(map(len, result)), len(secret)))
            result.append(secret)
            cursor = match.end()
        if _PREFIX in value[cursor:]:
            raise ValueError("Invalid secret placeholder. Reload the configuration.")
        result.append(value[cursor:])
        rebuilt = "".join(result)
        secure = _field_spans(path, rebuilt)
        if not _secret_field(path) and any(not any(a <= start and start + size <= b for a, b in secure)
                                           for start, size in offsets):
            raise ValueError("The pasted credential would be visible in this field.")
        return rebuilt

    result = _walk(edited, restore)
    target_destinations = {owner: _fingerprint(_destination(result, owner)) for owner in {entry[0] for entry in bindings}}
    refused = sorted({(f"{label} {field}" if (label := mcp_server_label(owner)) else field)
                      for owner, field, allowed in bindings if target_destinations[owner] not in allowed})
    if refused:
        raise CredentialDestinationError(refused)
    return result


def _yaml():
    editor = YAML(typ="rt")
    editor.preserve_quotes = True
    editor.width = 4096
    return editor


def _render(editor, value):
    stream = StringIO()
    editor.dump(value, stream)
    return stream.getvalue()


def mask_yaml_secrets(text: str) -> str:
    editor = _yaml()
    original = editor.load(text)
    masked = mask_config(original, raw=True)
    return text if masked == original else _render(editor, masked)


def restore_yaml_secrets(new_text: str, current_text: str) -> str:
    if new_text == mask_yaml_secrets(current_text):
        return current_text
    editor = _yaml()
    edited = editor.load(new_text)
    restored = restore_config(edited, editor.load(current_text))
    return new_text if edited == restored else _render(editor, restored)


def repair_revision(base: bytes, local: bytes) -> str:
    """A repair applies to precisely the two files that produced its preview."""
    digest = hmac.new(_SIGNING_KEY, b"yaml-override-repair-v1", hashlib.sha256)
    for document in (base, local):
        digest.update(len(document).to_bytes(8, "big"))
        digest.update(document)
    return digest.hexdigest()


def recover_flat_override(text, parse, allowed_keys):
    """Keep independent scalar lines only when exactly one flat entry is broken.

    Nested/multiline constructs, unknown keys, aliases and duplicate keys can
    change scope across lines. In those cases the editor starts a fresh override.
    Original comments and the malformed line are never included in the preview.
    """
    recovered = {}
    seen = set()
    broken = 0
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        field = re.match(r"^([a-z][a-z0-9_]*):(?: |$)", line)
        if not field or field[1] not in allowed_keys or field[1] in seen:
            return {}
        key = field[1]
        seen.add(key)
        try:
            entry = parse(line)
        except ValueError:
            broken += 1
            continue
        if not isinstance(entry, dict) or set(entry) != {key}:
            return {}
        value = entry[key]
        if isinstance(value, (dict, list)) or re.match(r"[|>!&*]", line[field.end():].lstrip()):
            return {}
        # Malformed text can make ordinary strings look like private material.
        # Limit recoverable display to credentials, booleans/numbers and fixed
        # public log levels. Fully ambiguous documents can still be replaced.
        if isinstance(value, str) and not _secret_field((key,)) and value not in {
                "debug", "info", "warning", "error", "critical"}:
            return {}
        recovered[key] = value
    return recovered if broken == 1 else {}
