"""
Tool call parser for local LLMs.

Parses LLM responses to extract tool calls in the format:
<tool_call>
{"name": "tool_name", "arguments": {"param": "value"}}
</tool_call>

This is model-agnostic and works with any LLM that can output structured text.
"""

import re
import json
import logging
from typing import List, Dict, Any, Optional, Tuple, Iterable

logger = logging.getLogger(__name__)

# Pattern to match tool calls in LLM output
TOOL_CALL_PATTERN = re.compile(
    r'<tool_call>\s*(\{.*?\})\s*</tool_call>',
    re.DOTALL | re.IGNORECASE
)

# Some models occasionally wrap tool calls in a tag named after the tool, e.g.
# <hangup_call>{...}</hangup_call>. We treat these as tool calls too, but the
# caller should validate allowlisted tool names before executing anything.
NAMED_TOOL_CALL_PATTERN = re.compile(
    r'<(?P<tag>[a-zA-Z0-9_]+)>\s*(?P<json>\{.*?\})\s*</(?P=tag)>',
    re.DOTALL
)

# Sometimes models emit a malformed wrapper where they start with the closing
# tag (e.g., `</tool_call> {...}`) or omit the closing tag entirely. We'll try
# to recover a JSON object adjacent to either tag.
TOOL_CALL_TAG_PATTERN = re.compile(
    r'</?tool_call>',
    re.IGNORECASE
)

# Some models output a "tool name" prefix followed by a JSON payload, without
# any XML-like wrapper, e.g:
#   hangup_call {"name":"hangup_call","arguments":{"farewell_message":"Bye"}}
# We'll parse this as a best-effort fallback and rely on downstream allowlisting
# to decide whether it can be executed.
BARE_TOOL_CALL_PREFIX_PATTERN = re.compile(
    r'(?P<tool>[a-zA-Z0-9_]{2,64})\s*\{',
)

# Some local models emit markdown-wrapped tool names followed by JSON fragments, e.g:
#   *hangup_call* {"name":"hangup_call","arguments":{"farewell_message":"Bye"
# Parse this best-effort to avoid speaking tool syntax back to callers.
MARKDOWN_TOOL_CALL_PREFIX_PATTERN = re.compile(
    r'(?P<decor>[\*\_`~]+)\s*(?P<tool>[a-zA-Z0-9_]{2,64})\s*(?P=decor)\s*(?=\{)',
)

# Alternative patterns for fallback parsing
FUNCTOOLS_PATTERN = re.compile(
    r'functools\[(\[.*?\])\]',
    re.DOTALL | re.IGNORECASE
)

JSON_FUNCTION_PATTERN = re.compile(
    r'\{\s*"function"\s*:\s*"([^"]+)"\s*,\s*"function_parameters"\s*:\s*(\{.*?\})\s*\}',
    re.DOTALL
)

_CONTROL_TOKEN_PREFIXES = ("<|system|>", "<|user|>", "<|assistant|>", "<|enduser|>", "<|end|>")


def has_tool_intent_markers(response: str, tool_names: Optional[Iterable[str]] = None) -> bool:
    """
    Best-effort detector for malformed tool-call attempts.

    Used to decide whether to trigger a hidden repair pass when primary parsing
    fails (Tier-2 recovery).
    """
    text = str(response or "")
    if not text:
        return False

    lowered = text.lower()
    if (
        "<tool_call" in lowered
        or "</tool_call" in lowered
        or "\"arguments\"" in lowered
        or "\"name\"" in lowered
        or "functools[" in lowered
    ):
        return True

    if re.search(r"[\*\_`~]+\s*[a-z0-9_]{2,64}\s*[\*\_`~]+\s*\{", lowered):
        return True

    if tool_names:
        for name in tool_names:
            tool_name = str(name or "").strip().lower()
            if not tool_name:
                continue
            if tool_name in lowered:
                return True
    return False


def _extract_json_object(text: str, start_index: int) -> Optional[Tuple[str, int]]:
    """
    Extract the first JSON object starting at or after start_index.

    Returns (json_string, end_index_exclusive) or None.
    """
    if not text:
        return None
    n = len(text)
    i = start_index
    while i < n and text[i] != "{":
        i += 1
    if i >= n:
        return None

    depth = 0
    in_string = False
    escape = False
    for j in range(i, n):
        ch = text[j]
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                return text[i : j + 1], j + 1

    return None


def _strip_control_tokens(text: str) -> str:
    """
    Remove common chat-template control tokens that occasionally leak into outputs.
    """
    if not text:
        return text
    # If a control token appears, truncate at its first occurrence to avoid speaking garbage.
    lowest = None
    for token in _CONTROL_TOKEN_PREFIXES:
        idx = text.find(token)
        if idx != -1:
            lowest = idx if lowest is None else min(lowest, idx)
    if lowest is not None:
        text = text[:lowest]
    return text.strip()


def _extract_partial_arguments(text: str) -> Dict[str, Any]:
    """
    Best-effort argument extraction from malformed/truncated JSON payloads.
    """
    if not text:
        return {}

    # Prefer content inside an `arguments` object if present.
    m = re.search(r'["\']arguments["\']\s*:\s*\{(?P<body>.*)', text, flags=re.IGNORECASE | re.DOTALL)
    body = m.group("body") if m else text

    # Truncate at likely boundaries to avoid over-capturing prose/instructions.
    for token in ("</tool_call>", "<|", "\n\n"):
        idx = body.find(token)
        if idx != -1:
            body = body[:idx]
    # If there is a closing brace, keep up to it; otherwise keep truncated tail.
    if "}" in body:
        body = body[: body.find("}")]

    params: Dict[str, Any] = {}
    for key, value in re.findall(
        r'["\']([a-zA-Z_][a-zA-Z0-9_]*)["\']\s*:\s*["\']([^"\n\r]*)',
        body,
    ):
        k = str(key or "").strip()
        if not k or k.lower() in {"name", "arguments", "parameters", "function"}:
            continue
        params[k] = value
    return params


def parse_tool_calls(response: str) -> List[Dict[str, Any]]:
    """
    Extract tool calls from LLM response.
    
    Supports multiple formats:
    1. <tool_call>{"name": "...", "arguments": {...}}</tool_call>
    2. functools[{"name": "...", "arguments": {...}}]
    3. {"function": "...", "function_parameters": {...}}
    
    Args:
        response: Raw LLM response text
        
    Returns:
        List of tool call dictionaries with 'name' and 'parameters' keys
    """
    tool_calls = []
    
    # Try primary format: <tool_call>...</tool_call>
    matches = TOOL_CALL_PATTERN.findall(response)
    for match in matches:
        try:
            tool_data = json.loads(match)
            if "name" in tool_data:
                tool_calls.append({
                    "name": tool_data["name"],
                    "parameters": tool_data.get("arguments", tool_data.get("parameters", {}))
                })
                logger.debug(
                    "Parsed tool call (primary format): tool=%s",
                    tool_data["name"],
                )
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse tool call JSON: %s", e)
            continue
    
    if tool_calls:
        return tool_calls

    # Try bare "toolname {json}" format.
    try:
        text = response or ""
        for m in BARE_TOOL_CALL_PREFIX_PATTERN.finditer(text):
            tool_hint = (m.group("tool") or "").strip()
            json_start = m.start() + (m.group(0).rfind("{"))
            extracted = _extract_json_object(text, json_start)
            if not extracted:
                continue
            json_str, _end = extracted
            try:
                tool_data = json.loads(json_str)
            except json.JSONDecodeError:
                continue

            if not isinstance(tool_data, dict):
                continue

            # Prefer explicit name in JSON; otherwise fall back to the prefix token.
            name = tool_data.get("name") or tool_hint
            if not name:
                continue

            parameters = tool_data.get("arguments", tool_data.get("parameters", {}))
            tool_calls.append({
                "name": name,
                "parameters": parameters if isinstance(parameters, dict) else {},
            })
            logger.debug(
                "Parsed tool call (bare prefix): tool=%s hint=%s",
                name,
                tool_hint,
            )
    except Exception:
        pass

    if tool_calls:
        return tool_calls

    # Try markdown-wrapped prefix format, including malformed/truncated JSON.
    # Example: *hangup_call* {"name":"hangup_call","arguments":{"farewell_message":"Bye"
    try:
        text = response or ""
        for m in MARKDOWN_TOOL_CALL_PREFIX_PATTERN.finditer(text):
            tool_hint = (m.group("tool") or "").strip()
            extracted = _extract_json_object(text, m.end())
            if extracted:
                json_str, _end = extracted
                try:
                    tool_data = json.loads(json_str)
                except json.JSONDecodeError:
                    tool_data = {}

                if isinstance(tool_data, dict):
                    name = tool_data.get("name") or tool_hint
                    if name:
                        parameters = tool_data.get("arguments", tool_data.get("parameters", {}))
                        if not isinstance(parameters, dict):
                            parameters = {}
                        tool_calls.append({"name": name, "parameters": parameters})
                        logger.debug("Parsed tool call (markdown prefix): tool=%s hint=%s", name, tool_hint)
                        continue

            # Fallback for partial JSON: recover tool name from marker and extract any partial args.
            params = _extract_partial_arguments(text[m.end():])
            if tool_hint:
                tool_calls.append({"name": tool_hint, "parameters": params})
                logger.debug(
                    "Parsed tool call (markdown prefix partial): tool=%s",
                    tool_hint,
                )
    except Exception:
        pass

    if tool_calls:
        return tool_calls

    # Try named-tag format: <hangup_call>{...}</hangup_call>
    # Note: This is a best-effort fallback. Downstream must validate tool names.
    for tag, json_str in NAMED_TOOL_CALL_PATTERN.findall(response):
        if tag.lower() == "tool_call":
            continue
        try:
            tool_data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse named tool call JSON: %s", e)
            continue

        name = tool_data.get("name") or tag
        parameters = tool_data.get("arguments") or tool_data.get("parameters")
        if parameters is None:
            # Allow compact forms like: <hangup_call>{"farewell_message":"Bye"}</hangup_call>
            parameters = {k: v for k, v in tool_data.items() if k != "name"}

        tool_calls.append({
            "name": name,
            "parameters": parameters if isinstance(parameters, dict) else {},
        })
        logger.debug(
            "Parsed tool call (named tag): tool=%s tag=%s",
            name,
            tag,
        )

    if tool_calls:
        return tool_calls

    # Try malformed tool_call tags (e.g., `</tool_call> {...}` or `<tool_call> {...}` without close).
    try:
        for match in TOOL_CALL_TAG_PATTERN.finditer(response or ""):
            extracted = _extract_json_object(response, match.end())
            if not extracted:
                continue
            json_str, _end = extracted
            try:
                tool_data = json.loads(json_str)
            except json.JSONDecodeError:
                continue

            if isinstance(tool_data, dict) and "name" in tool_data:
                tool_calls.append({
                    "name": tool_data["name"],
                    "parameters": tool_data.get("arguments", tool_data.get("parameters", {})),
                })
                logger.debug(
                    "Parsed tool call (adjacent tag): tool=%s",
                    tool_data.get("name"),
                )
    except Exception:
        # Defensive: never let parsing crash the engine.
        pass

    if tool_calls:
        return tool_calls
    
    # Try functools format: functools[{...}]
    functools_matches = FUNCTOOLS_PATTERN.findall(response)
    for match in functools_matches:
        try:
            tools_list = json.loads(match)
            if isinstance(tools_list, list):
                for tool_data in tools_list:
                    if "name" in tool_data:
                        tool_calls.append({
                            "name": tool_data["name"],
                            "parameters": tool_data.get("arguments", {})
                        })
        except json.JSONDecodeError:
            continue
    
    if tool_calls:
        return tool_calls
    
    # Try function format: {"function": "...", "function_parameters": {...}}
    func_matches = JSON_FUNCTION_PATTERN.findall(response)
    for func_name, params_str in func_matches:
        try:
            params = json.loads(params_str)
            tool_calls.append({
                "name": func_name,
                "parameters": params
            })
        except json.JSONDecodeError:
            continue
    
    return tool_calls


def extract_text_without_tools(response: str) -> str:
    """
    Remove tool call markers from response and return clean text.
    
    Args:
        response: Raw LLM response with potential tool calls
        
    Returns:
        Clean text suitable for TTS
    """
    # Remove <tool_call>...</tool_call> blocks
    clean = TOOL_CALL_PATTERN.sub('', response)

    # Remove <tool_name>...</tool_name> blocks with embedded JSON
    clean = NAMED_TOOL_CALL_PATTERN.sub('', clean)

    # Remove stray tool_call tags and any adjacent JSON object.
    try:
        while True:
            m = TOOL_CALL_TAG_PATTERN.search(clean)
            if not m:
                break
            # Remove tag itself.
            start = m.start()
            end = m.end()
            # Also remove an immediate JSON object if present.
            extracted = _extract_json_object(clean, end)
            if extracted:
                _json_str, json_end = extracted
                clean = clean[:start] + clean[json_end:]
            else:
                clean = clean[:start] + clean[end:]
    except Exception:
        pass

    # Remove bare "toolname {json}" blocks (best-effort).
    try:
        while True:
            m = BARE_TOOL_CALL_PREFIX_PATTERN.search(clean)
            if not m:
                break
            json_start = m.start() + (m.group(0).rfind("{"))
            extracted = _extract_json_object(clean, json_start)
            if not extracted:
                break
            _json_str, json_end = extracted
            # Drop from the start of the tool token through the end of JSON.
            clean = clean[: m.start()] + clean[json_end:]
    except Exception:
        pass

    # Remove markdown-wrapped "toolname {json...}" fragments (including partial JSON).
    try:
        while True:
            m = MARKDOWN_TOOL_CALL_PREFIX_PATTERN.search(clean)
            if not m:
                break
            extracted = _extract_json_object(clean, m.end())
            if extracted:
                _json_str, json_end = extracted
                clean = clean[: m.start()] + clean[json_end:]
                continue
            # Partial JSON / malformed output: drop until newline (or end of text).
            nl = clean.find("\n", m.start())
            end = nl if nl != -1 else len(clean)
            clean = clean[: m.start()] + clean[end:]
    except Exception:
        pass
    
    # Remove functools[...] blocks
    clean = FUNCTOOLS_PATTERN.sub('', clean)
    
    # Remove {"function": ...} blocks
    clean = JSON_FUNCTION_PATTERN.sub('', clean)
    
    # Clean up extra whitespace
    clean = re.sub(r'\n\s*\n', '\n', clean)
    clean = clean.strip()

    # Finally, strip leaked chat-template control tokens.
    clean = _strip_control_tokens(clean)
    
    return clean


def parse_response_with_tools(response: str) -> Tuple[Optional[str], Optional[List[Dict]]]:
    """
    Parse LLM response and separate text from tool calls.
    
    Args:
        response: Raw LLM response
        
    Returns:
        Tuple of (clean_text, tool_calls)
        - clean_text: Text suitable for TTS (None if empty)
        - tool_calls: List of tool call dicts (None if no tools)
    """
    tool_calls = parse_tool_calls(response)
    clean_text = extract_text_without_tools(response)
    
    return (
        clean_text if clean_text else None,
        tool_calls if tool_calls else None
    )


def validate_tool_call(tool_call: Dict[str, Any], available_tools: List[str]) -> bool:
    """
    Validate that a tool call references a known tool.
    
    Args:
        tool_call: Tool call dictionary with 'name' key
        available_tools: List of valid tool names
        
    Returns:
        True if valid, False otherwise
    """
    name = tool_call.get("name", "")
    if name not in available_tools:
        logger.warning(
            "Unknown tool in LLM response: %s (available: %s)",
            name,
            available_tools
        )
        return False
    return True
