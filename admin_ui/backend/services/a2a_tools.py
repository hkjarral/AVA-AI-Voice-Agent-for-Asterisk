"""A2A configuration validation and read-only Agent usage checks."""

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi import HTTPException

from src.tools.a2a_config import parse_a2a_agent_config


def validate_document(name, doc, previous=None):
    """Apply shared runtime validation plus Admin phase and credential policy."""
    if doc.get('phase', 'in_call') != 'in_call' or doc.get('is_global', False):
        raise HTTPException(422, 'A2A tools run in-call and require per-agent assignment')
    if any(key in doc for key in ('bearer_token', 'token', 'api_key')):
        raise HTTPException(422, 'Save A2A tokens through the credential field')
    previous = previous if isinstance(previous, dict) else {}
    env = doc.get('bearer_token_env')
    if env and not str(env).startswith(('AVA_A2A_', 'AAVA_A2A_')) and env != (previous or {}).get('bearer_token_env'):
        raise HTTPException(422, 'New A2A environment references must start with AVA_A2A_ or AAVA_A2A_')
    try:
        parse_a2a_agent_config(name, doc)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from None
    return dict(doc)


def agent_usage(names):
    """Read both saved assignment lists without migrating the Agent database."""
    path = Path(os.getenv('AGENTS_DB_PATH', '/app/data/operator/agents.db'))
    usage = {name: [] for name in names}
    if not path.exists():
        return usage
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            rows = conn.execute('SELECT slug, display_name, tools_json, extra_json FROM agents').fetchall()
        for slug, display_name, tools_raw, extra_raw in rows:
            tools = json.loads(tools_raw or '[]')
            extra = json.loads(extra_raw or '{}')
            if not isinstance(tools, list) or not isinstance(extra, dict):
                raise TypeError('Invalid Agent configuration')
            inline = extra.get('in_call_http_tools') or []
            if not isinstance(inline, (list, dict)):
                raise TypeError('Invalid Agent tool assignments')
            for name in names:
                if name in tools or name in inline:
                    usage[name].append({'slug': slug, 'name': display_name})
    except (OSError, sqlite3.Error, ValueError, TypeError):
        raise HTTPException(503, 'Cannot verify Agent tool usage. Repair the Agent store before deleting A2A tools.') from None
    return usage


def protect_assigned_tools(old, new):
    """Reject removal or kind changes for A2A tools referenced by saved Agents."""
    before = old.get('in_call_tools') or {}
    after = new.get('in_call_tools') or {}
    removed = {
        name for name, doc in before.items()
        if isinstance(doc, dict) and doc.get('kind') == 'a2a_agent'
        and (not isinstance(after.get(name), dict) or after[name].get('kind') != 'a2a_agent')
    }
    if not removed:
        return
    for name, agents in agent_usage(removed).items():
        if agents:
            raise HTTPException(409, f'Remove A2A tool {name} from Agents before deleting it: ' + ', '.join(agent['name'] for agent in agents))
