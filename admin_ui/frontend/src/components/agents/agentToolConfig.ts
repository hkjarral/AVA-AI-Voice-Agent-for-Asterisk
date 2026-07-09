export interface ToolDef {
    name: string;
    description?: string;
    phase?: string;     // 'pre_call' | 'in_call' | 'post_call'
    source?: string;    // 'builtin' | 'http' | 'mcp'
    is_global?: boolean;
}

export interface AgentToolState {
    provider: string;                 // '' when a pipeline is selected
    pipeline: string;                 // '' when a provider is selected
    inCallTools: string[];            // tools_json (builtin + mcp in-call names)
    inCallHttpTools: string[];        // extra.in_call_http_tools when it is a LIST
    inCallHttpToolsIsObject: boolean; // true when extra.in_call_http_tools is an inline-config OBJECT
    preCallTools: string[];           // extra.pre_call_tools
    postCallTools: string[];          // extra.post_call_tools
    disableGlobalPreCall: string[];   // extra.disable_global_pre_call_tools
    disableGlobalInCall: string[];    // extra.disable_global_in_call_tools
    disableGlobalPostCall: string[];  // extra.disable_global_post_call_tools
    backgroundMusic: string;          // extra.background_music
    noInput: Record<string, unknown>; // extra.no_input per-agent policy overrides
    extraPassthrough: Record<string, unknown>; // extra keys we do not own (+ object-form in_call_http_tools)
    mcpJsonRaw: string;               // mcp_json preserved verbatim — NOTE: no runtime effect, MCP is configured globally not per-agent (audit LOW-T2)
}

const OWNED_EXTRA_KEYS = [
    'pipeline', 'background_music', 'pre_call_tools', 'post_call_tools',
    'in_call_http_tools', 'disable_global_pre_call_tools',
    'disable_global_in_call_tools', 'disable_global_post_call_tools',
    'no_input',
];

const asStrArray = (v: unknown): string[] =>
    Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];

const asString = (v: unknown): string => (typeof v === 'string' ? v : '');
const asObject = (v: unknown): Record<string, unknown> =>
    v && typeof v === 'object' && !Array.isArray(v) ? { ...(v as Record<string, unknown>) } : {};

const sanitizeNoInputOverrides = (raw: Record<string, unknown>): Record<string, unknown> => {
    const sanitized: Record<string, unknown> = {};
    const knownKeys = new Set([
        'enabled', 'inbound_enabled', 'outbound_enabled',
        'initial_timeout_sec', 'grace_timeout_sec', 'max_check_ins',
        'check_in_message', 'final_message',
    ]);
    const unsafePassthroughKeys = new Set(['__proto__', 'constructor', 'prototype']);

    for (const [key, value] of Object.entries(raw)) {
        if (!knownKeys.has(key) && !unsafePassthroughKeys.has(key)) sanitized[key] = value;
    }
    for (const key of ['enabled', 'inbound_enabled', 'outbound_enabled']) {
        if (typeof raw[key] === 'boolean') sanitized[key] = raw[key];
    }
    for (const key of ['initial_timeout_sec', 'grace_timeout_sec']) {
        const value = raw[key];
        if (typeof value === 'number' && Number.isFinite(value) && value >= 1 && value <= 3600) {
            sanitized[key] = value;
        }
    }
    const attempts = raw['max_check_ins'];
    if (typeof attempts === 'number' && Number.isInteger(attempts) && attempts >= 0 && attempts <= 10) {
        sanitized['max_check_ins'] = attempts;
    }
    for (const key of ['check_in_message', 'final_message']) {
        const value = raw[key];
        if (typeof value === 'string' && value.trim() && value.trim().length <= 500) {
            sanitized[key] = value.trim();
        }
    }
    return sanitized;
};

function safeParseObject(raw?: string | null): Record<string, unknown> {
    if (!raw || !raw.trim()) return {};
    try {
        const v = JSON.parse(raw);
        return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
    } catch {
        return {};
    }
}

export interface AgentLike {
    provider?: string;
    tools_json?: string;
    mcp_json?: string;
    extra_json?: string;
}

export function parseAgentConfig(agent: AgentLike | null | undefined): AgentToolState {
    const extra = safeParseObject(agent?.extra_json);

    const ichRaw = extra['in_call_http_tools'];
    const ichIsObject = !!ichRaw && typeof ichRaw === 'object' && !Array.isArray(ichRaw);

    const passthrough: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(extra)) {
        if (!OWNED_EXTRA_KEYS.includes(k)) passthrough[k] = v;
    }
    // Object-form in_call_http_tools is preserved verbatim (the picker shows its keys read-only).
    if (ichIsObject) passthrough['in_call_http_tools'] = ichRaw;

    let inCallTools: string[] = [];
    if (agent?.tools_json && agent.tools_json.trim()) {
        try { inCallTools = asStrArray(JSON.parse(agent.tools_json)); } catch { inCallTools = []; }
    }

    return {
        provider: agent?.provider || '',
        pipeline: asString(extra['pipeline']),
        inCallTools,
        inCallHttpTools: ichIsObject ? Object.keys(ichRaw as object) : asStrArray(ichRaw),
        inCallHttpToolsIsObject: ichIsObject,
        preCallTools: asStrArray(extra['pre_call_tools']),
        postCallTools: asStrArray(extra['post_call_tools']),
        disableGlobalPreCall: asStrArray(extra['disable_global_pre_call_tools']),
        disableGlobalInCall: asStrArray(extra['disable_global_in_call_tools']),
        disableGlobalPostCall: asStrArray(extra['disable_global_post_call_tools']),
        backgroundMusic: asString(extra['background_music']),
        noInput: asObject(extra['no_input']),
        extraPassthrough: passthrough,
        mcpJsonRaw: agent?.mcp_json || '',
    };
}

export interface SerializedAgentConfig {
    provider: string;
    tools_json: string | null;
    mcp_json: string | null;
    extra_json: string | null;
}

export function serializeAgentConfig(state: AgentToolState): SerializedAgentConfig {
    const extra: Record<string, unknown> = { ...state.extraPassthrough };

    const setArr = (key: string, arr: string[]) => { if (arr.length) extra[key] = arr; else delete extra[key]; };
    const setStr = (key: string, val: string) => { if (val.trim()) extra[key] = val.trim(); else delete extra[key]; };

    setStr('pipeline', state.pipeline);
    setStr('background_music', state.backgroundMusic);
    setArr('pre_call_tools', state.preCallTools);
    setArr('post_call_tools', state.postCallTools);
    if (!state.inCallHttpToolsIsObject) setArr('in_call_http_tools', state.inCallHttpTools);
    setArr('disable_global_pre_call_tools', state.disableGlobalPreCall);
    setArr('disable_global_in_call_tools', state.disableGlobalInCall);
    setArr('disable_global_post_call_tools', state.disableGlobalPostCall);
    const noInput = sanitizeNoInputOverrides(state.noInput);
    if (Object.keys(noInput).length) extra['no_input'] = noInput;
    else delete extra['no_input'];

    return {
        provider: state.pipeline ? '' : state.provider,
        tools_json: state.inCallTools.length ? JSON.stringify(state.inCallTools) : null,
        mcp_json: state.mcpJsonRaw.trim() || null,
        extra_json: Object.keys(extra).length ? JSON.stringify(extra) : null,
    };
}

type PhaseKey = 'pre_call' | 'in_call' | 'post_call';

export function phaseOf(tool: ToolDef): PhaseKey {
    if (tool.phase === 'pre_call') return 'pre_call';
    if (tool.phase === 'post_call') return 'post_call';
    return 'in_call';
}

function ownedListName(tool: ToolDef): keyof AgentToolState {
    const phase = phaseOf(tool);
    if (phase === 'pre_call') return 'preCallTools';
    if (phase === 'post_call') return 'postCallTools';
    return tool.source === 'http' ? 'inCallHttpTools' : 'inCallTools';
}

function disableListName(tool: ToolDef): keyof AgentToolState {
    const phase = phaseOf(tool);
    if (phase === 'pre_call') return 'disableGlobalPreCall';
    if (phase === 'post_call') return 'disableGlobalPostCall';
    return 'disableGlobalInCall';
}

export function isToolLocked(state: AgentToolState, tool: ToolDef): boolean {
    // Inline-configured in-call HTTP tools are preserved read-only (never clobbered).
    return state.inCallHttpToolsIsObject && phaseOf(tool) === 'in_call' && tool.source === 'http';
}

export function isToolChecked(state: AgentToolState, tool: ToolDef): boolean {
    if (isToolLocked(state, tool)) return true;
    if (tool.is_global) return !(state[disableListName(tool)] as string[]).includes(tool.name);
    return (state[ownedListName(tool)] as string[]).includes(tool.name);
}

export function toggleTool(state: AgentToolState, tool: ToolDef): AgentToolState {
    if (isToolLocked(state, tool)) return state;
    const listName = tool.is_global ? disableListName(tool) : ownedListName(tool);
    const list = state[listName] as string[];
    const next = list.includes(tool.name) ? list.filter((n) => n !== tool.name) : [...list, tool.name];
    return { ...state, [listName]: next };
}
