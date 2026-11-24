"""Apply the YAML credential policy to serialized operator agent settings."""
import json
import re
from services.yaml_secrets import _AGENT_ROWS, has_markers, mask_config, restore_config
from src.tools.runtime_config import dump_agent_tool_configs
from src.tools.telephony.hangup_policy import dump_agent_hangup_policy

JSON_COLUMNS = ('extra_json', 'mcp_json', 'tools_json', 'tool_configs_json', 'hangup_policy_json')
_SCALAR_COLUMNS = ('display_name', 'extension', 'role_label', 'provider', 'voice', 'greeting', 'prompt', 'audio_profile',
                   'source_file', 'notes', 'email_recipient', 'email_from')
_POLICY = {'tool_configs_json': dump_agent_tool_configs, 'hangup_policy_json': dump_agent_hangup_policy}
_OPAQUE = '__ava_stored_json__'
_PRIVATE = 'opaque_credential'
_ABSENT = object()
_JSON_UNICODE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _unique(pairs):
    doc = {}
    for key, value in pairs:
        if key in doc:
            raise ValueError('Duplicate JSON key')
        doc[key] = value
    return doc


def _invalid_constant(_):
    raise ValueError('Invalid JSON number')


def _decoded(text):
    try:
        value = json.loads(text, object_pairs_hook=_unique, parse_constant=_invalid_constant)
    except (ValueError, TypeError, RecursionError):
        return _ABSENT
    return value


def _invalid_text_has_marker(text):
    if has_markers(text):
        return True
    # A truncated editor document can still contain its ASCII-escaped placeholder.
    # Decode code units only for detection. Never repair or persist invalid input.
    if "\\u" in text:
        return has_markers(_JSON_UNICODE.sub(lambda match: chr(int(match[1], 16)), text))
    return False


def _document(row_id, column, value):
    return {_AGENT_ROWS: {row_id: {column: value}}}


def _opaque(value, column):
    if not isinstance(value, (list, dict)) or (column == 'extra_json' and not isinstance(value, dict)):
        return True
    if column == 'tools_json':
        if isinstance(value, dict):
            # Reconciled legacy contexts also store a map of tool names to settings.
            return not all(isinstance(settings, (dict, bool)) for settings in value.values())
        return not all(isinstance(tool, str) for tool in value)
    if column in _POLICY:
        try:
            _POLICY[column](value)
        except ValueError:
            return True
    return False


def is_opaque_marker(text):
    value = _decoded(text)
    return isinstance(value, dict) and _OPAQUE in value and has_markers(value[_OPAQUE])


def _dump(value):
    return json.dumps(value, ensure_ascii=True, separators=(',', ':'))


def _public_text(row_id, column, stored, display_only):
    value = _decoded(stored)
    if _opaque(value, column):
        if display_only:
            return _dump({_OPAQUE: 'Stored value cannot be displayed'})
        value = {_PRIVATE: stored}
        masked = mask_config(_document(row_id, column, value), raw=True)
        return _dump({_OPAQUE: 'Stored, cannot display. ' + masked[_AGENT_ROWS][row_id][column][_PRIVATE]})
    masked = mask_config(_document(row_id, column, value), raw=True, display_only=display_only)
    result = masked[_AGENT_ROWS][row_id][column]
    return stored if result is value else _dump(result)


def public_agent_row(row, *, display_only=False):
    result = dict(row)
    for column in JSON_COLUMNS:
        if result.get(column) is not None:
            result[column] = _public_text(result['id'], column, result[column], display_only)
    for column in _SCALAR_COLUMNS:
        value = result.get(column)
        if isinstance(value, str):
            masked = mask_config(_document(result['id'], column, value), raw=True, display_only=display_only)
            result[column] = masked[_AGENT_ROWS][result['id']][column]
    return result


def restore_agent_fields(fields, current=None):
    """Retain markers only on their original row, column, path and live value."""
    result = dict(fields)
    row_id = current['id'] if current else 'new'
    for column in _SCALAR_COLUMNS:
        value = fields.get(column)
        if isinstance(value, str) and has_markers(value):
            old_value = current.get(column) if current else None
            restored = restore_config(_document(row_id, column, value), _document(row_id, column, old_value))
            result[column] = restored[_AGENT_ROWS][row_id][column]
    for column in JSON_COLUMNS:
        text = fields.get(column)
        if text is None:
            continue
        edited = _decoded(text)
        old_text = current.get(column) if current else None
        if old_text is not None and text == _public_text(row_id, column, old_text, False):
            result[column] = old_text
            continue
        if edited is _ABSENT:
            if _invalid_text_has_marker(text):
                raise ValueError('Invalid stored agent JSON marker. Reload the agent.')
            if column in ('extra_json', 'mcp_json') and old_text is not None and text != old_text:
                raise ValueError(f'Enter valid JSON to replace {column}.')
            continue
        if isinstance(edited, dict) and _OPAQUE in edited and has_markers(edited[_OPAQUE]):
            if (old_text is not None and len(edited) == 1 and
                    edited == _decoded(_public_text(row_id, column, old_text, False))):
                result[column] = old_text
                continue
            raise ValueError('Replace the entire unavailable JSON value or reload the agent.')
        old = _decoded(old_text) if old_text is not None else None
        if old is _ABSENT:
            old = None
        safe = restore_config(_document(row_id, column, edited), _document(row_id, column, old))
        restored = safe[_AGENT_ROWS][row_id][column]
        if restored is not edited:
            unchanged = old is not None and json.dumps(restored, sort_keys=True) == json.dumps(old, sort_keys=True)
            result[column] = old_text if unchanged else _dump(restored)
    return result
