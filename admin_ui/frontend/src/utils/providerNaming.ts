export type Capability = 'stt' | 'llm' | 'tts';

export const capabilitySuffix = (cap: Capability): string => {
    switch (cap) {
        case 'stt':
            return 'stt';
        case 'llm':
            return 'llm';
        case 'tts':
            return 'tts';
        default:
            return '';
    }
};

export const buildProviderKey = (baseName: string, cap: Capability): string => {
    const suffix = capabilitySuffix(cap);
    const trimmed = (baseName || '').trim();
    if (!trimmed) return '';
    return trimmed.toLowerCase().endsWith(`_${suffix}`) ? trimmed : `${trimmed}_${suffix}`;
};

export const ensureModularKey = (name: string, cap: Capability): string => {
    return buildProviderKey(name, cap);
};

export const capabilityFromKey = (name: string): Capability | null => {
    const lower = (name || '').toLowerCase();
    if (lower.endsWith('_stt')) return 'stt';
    if (lower.endsWith('_llm')) return 'llm';
    if (lower.endsWith('_tts')) return 'tts';
    return null;
};

export const getModularCapability = (provider: any): Capability | null => {
    const caps = provider?.capabilities || [];
    if (caps.length === 1 && (caps[0] === 'stt' || caps[0] === 'llm' || caps[0] === 'tts')) {
        return caps[0];
    }
    const inferred = capabilityFromKey(provider?.name || '');
    if (inferred) return inferred;
    return null;
};

/**
 * Canonical YAML keys for built-in full-agent provider kinds. When a YAML
 * provider entry uses one of these keys with NO explicit `type` field (the
 * legacy single-instance form), the engine treats it as a full agent of that
 * kind. The frontend mirrors that behavior here so the UI categorizes those
 * entries correctly.
 */
const CANONICAL_FULL_AGENT_KEYS = new Set([
    'local',
    'openai_realtime',
    'deepgram',
    'google_live',
    'elevenlabs_agent',
    'grok',
]);

/**
 * Check if a provider is a Full Agent (handles STT+LLM+TTS together).
 * Full agents can be used as default_provider but NOT in modular pipeline slots.
 *
 * A provider is a full agent if:
 * - explicit `type` is one of: openai_realtime, deepgram, google_live,
 *   elevenlabs_agent, grok, full
 * - OR it has all three capabilities: stt, llm, tts
 * - OR (legacy single-instance form) the YAML key matches a canonical
 *   full-agent kind AND no `type` field is set that contradicts it
 *
 * Note: non-modular provider keys with type='local' are Local AI full agents;
 *       modular local adapters are identified by _stt/_llm/_tts key suffixes.
 *
 * @param provider The provider config object
 * @param key Optional YAML key for the provider (e.g. 'grok', 'acme_grok').
 *            When supplied, legacy single-instance form (canonical key with
 *            no explicit `type` field) is recognized.
 */
export const isFullAgentProvider = (provider: any, key?: string): boolean => {
    const type = (provider?.type || '').toLowerCase();
    const caps = provider?.capabilities || [];
    if (key && capabilityFromKey(key)) return false;
    const hasAllCaps = caps.includes('stt') && caps.includes('llm') && caps.includes('tts');
    // Full agent types - these are always full agents
    const fullAgentTypes = ['openai_realtime', 'deepgram', 'google_live', 'elevenlabs_agent', 'grok', 'local'];
    if (fullAgentTypes.includes(type)) return true;
    if (type === 'full') return !key || CANONICAL_FULL_AGENT_KEYS.has(key);
    // Any provider with all 3 capabilities is a full agent
    if (hasAllCaps) return true;
    // Legacy single-instance form: YAML key matches a canonical full-agent kind
    // AND no `type` field is set (so we don't override an explicit `type: modular`).
    if (key && !type && CANONICAL_FULL_AGENT_KEYS.has(key)) return true;
    return false;
};

/**
 * Concrete full-agent kinds that are unambiguous from the `type` field alone.
 * 'local' is resolved separately because modular local adapters are identified
 * by key suffix, while non-modular `type: local` entries are full agents.
 */
const UNAMBIGUOUS_FULL_AGENT_KINDS = [
    'openai_realtime',
    'deepgram',
    'google_live',
    'elevenlabs_agent',
    'grok',
];

/**
 * Resolve the concrete full-agent kind for a provider, mirroring how the engine
 * interprets canonical legacy entries. Returns null when the provider is not a
 * full agent or its kind cannot be determined without name-guessing.
 *
 * - explicit unambiguous full-agent `type` (e.g. google_live) → that type
 * - non-modular `type: local` (the monolithic Local AI selection) → 'local';
 *   suffix-based local adapters stay modular → null
 * - `type: full` (or no `type`) on a canonical key (e.g. google_live, local) → the key
 * - neutral custom keys are NOT guessed → null
 *
 * The disabled provider-type selector and save validation use this so a canonical
 * `google_live: { type: full }` entry displays and saves as Google Live.
 *
 * @see GitHub issue #436
 */
export const getEffectiveFullAgentKind = (provider: any, key?: string): string | null => {
    const type = (provider?.type || '').toLowerCase();
    if (key && capabilityFromKey(key)) return null;
    if (UNAMBIGUOUS_FULL_AGENT_KINDS.includes(type)) return type;
    if (type === 'local') return 'local';
    if ((type === 'full' || type === '') && key && CANONICAL_FULL_AGENT_KEYS.has(key)) {
        return key;
    }
    return null;
};

/**
 * Provider types that have registered adapter factories in the engine.
 * Only these providers can be used in pipelines.
 * 
 * Full Agents: openai_realtime, deepgram, google_live
 * Modular: local, openai, deepgram, google
 */
export const REGISTERED_PROVIDER_TYPES = [
    // Full agent types (monolithic)
    'openai_realtime',
    'deepgram',
    'google_live',
    'elevenlabs_agent',
    'grok',
    'full',
    // Modular provider types (single capability)
    'local',
    'openai',
    'groq',
    'google',
    'elevenlabs',
    'ollama',
    'telnyx',
    'telenyx',
    'azure',
    'minimax',
    // AAVA-182: Admin UI creates providers with type 'modular'
    'modular',
] as const;

export type RegisteredProviderType = typeof REGISTERED_PROVIDER_TYPES[number];

/**
 * Check if a provider has a registered adapter factory in the engine.
 * Unregistered providers can be saved but will not work in pipelines.
 */
export const isRegisteredProvider = (provider: any): boolean => {
    const type = (provider?.type || '').toLowerCase();
    if (!type) return false;
    return REGISTERED_PROVIDER_TYPES.includes(type as RegisteredProviderType);
};

/**
 * Get a human-readable description of why a provider is unregistered.
 */
export const getUnregisteredReason = (provider: any): string => {
    const type = provider?.type;
    if (!type) {
        return 'No provider type specified. Set a type (e.g., local, openai, deepgram, google).';
    }
    return `Provider type "${type}" does not have an adapter implemented in the engine.`;
};
