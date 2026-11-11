import { isFullAgentProvider } from '../../utils/providerNaming';

export interface GreetingAgent {
    provider?: string | null;
    extra_json?: string | null;
    is_active?: number | boolean;
}

interface GreetingConfig {
    audio_transport?: string;
    default_provider?: string;
    providers?: Record<string, { enabled?: boolean; capabilities?: string[]; type?: string }>;
    pipelines?: Record<string, unknown>;
}

// Describe saved routes, never claim knowledge of per-call dialplan overrides.
export function greetingRtpSupport(config: GreetingConfig, agents: GreetingAgent[] | null) {
    if (config.audio_transport !== 'externalmedia') return {
        editable: false,
        help: 'Only supported ExternalMedia provider greetings use this setting. AudioSocket and WebSocket do not use it.',
    };
    if (agents === null) return {
        editable: false,
        help: 'Agent routes are loading or unavailable. Applicability cannot be verified; the saved value is preserved.',
    };
    let provider = false;
    let pipeline = false;
    let unknown = false;
    for (const agent of agents) {
        try {
            if (!agent || typeof agent !== 'object') throw new Error('Invalid Agent');
            if (agent.is_active === false || agent.is_active === 0) continue;
            const extra = JSON.parse(agent.extra_json || '{}');
            if (!extra || typeof extra !== 'object' || Array.isArray(extra)) throw new Error('Invalid Agent settings');
            const target = (agent.provider || config.default_provider || '').trim();
            if (extra.pipeline) pipeline = true;
            else {
                const settings = config.providers?.[target];
                if (settings && settings.enabled !== false && isFullAgentProvider(settings, target)) provider = true;
                else if (target && config.pipelines?.[target]) pipeline = true;
                else unknown = true;
            }
        } catch {
            unknown = true;
        }
    }
    const help = provider
        ? pipeline
            ? 'Partial support: configured Agents mix provider and pipeline routes. Only ExternalMedia provider streams tagged as greetings use this wait; pipeline greetings do not.'
            : 'Configured provider Agents can use this wait only for ExternalMedia streams tagged as greetings, when the remote RTP endpoint is missing.'
        : pipeline
            ? 'Configured Agents use pipeline greetings, which do not use this setting.'
            : 'No supported provider Agent route was found. The saved value is preserved.';
    return { editable: provider, help: help + (unknown ? ' Some Agent routes could not be resolved.' : '') + ' Per-call routing overrides can change applicability.' };
}
