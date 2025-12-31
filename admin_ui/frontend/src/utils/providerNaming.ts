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
 * Check if a provider is a Full Agent (handles STT+LLM+TTS together).
 * Full agents can be used as default_provider but NOT in modular pipeline slots.
 * 
 * A provider is a full agent if:
 * - type is one of: openai_realtime, deepgram, google_live, full
 * - OR has all three capabilities: stt, llm, tts
 * 
 * Note: 'local' with type='full' is a full agent (Local AI Server monolithic mode)
 *       'local' with type='local' is modular (local_stt, local_llm, local_tts)
 */
export const isFullAgentProvider = (provider: any): boolean => {
    const type = (provider?.type || '').toLowerCase();
    const caps = provider?.capabilities || [];
    const hasAllCaps = caps.includes('stt') && caps.includes('llm') && caps.includes('tts');
    // Full agent types - these are always full agents
    const fullAgentTypes = ['openai_realtime', 'deepgram', 'deepgram_agent', 'google_live', 'elevenlabs_agent', 'full'];
    if (fullAgentTypes.includes(type)) return true;
    // Any provider with all 3 capabilities is a full agent
    if (hasAllCaps) return true;
    return false;
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
    'full',
    // Modular provider types (single capability)
    'local',
    'openai',
    'groq',
    'google',
    'elevenlabs',
    'ollama',
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
