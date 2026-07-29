/**
 * Per-agent voice control logic (v7.3.0).
 *
 * Decides how the Agent form's Voice field renders for the selected AI
 * Engine, driven by GET /api/config/providers/meta (see
 * src/utils/voice_catalog.py — the single-source catalog shared with the
 * engine's soft validation).
 */

export interface VoiceOption {
    id: string;
    label: string;
}

export interface ProviderVoiceMeta {
    name: string;
    kind: string | null;
    is_full_agent: boolean;
    enabled: boolean;
    voice_mode: 'static' | 'freeform' | 'platform_managed' | 'unsupported';
    voices: VoiceOption[];
    default_voice: string | null;
}

export interface VoiceControlState {
    /** select = closed list; combo = suggestions + free text; disabled = no per-agent voice */
    control: 'select' | 'combo' | 'disabled';
    options: VoiceOption[];
    /** true when the stored value is not in a static provider's catalog (will fall back at runtime) */
    unrecognized: boolean;
    note: string;
}

export function voiceControlState(
    meta: ProviderVoiceMeta[] | null,
    engineValue: string,
    currentVoice: string,
): VoiceControlState {
    if (!engineValue) {
        return {
            control: 'disabled', options: [], unrecognized: false,
            note: 'Select an AI Engine to choose a voice.',
        };
    }
    if (engineValue.startsWith('pipeline:')) {
        return {
            control: 'disabled', options: [], unrecognized: false,
            note: "Voice comes from the pipeline's TTS provider configuration.",
        };
    }

    const providerName = engineValue.slice('provider:'.length);
    const entry = meta?.find((m) => m.name === providerName);
    if (!meta || !entry) {
        // Metadata unavailable (endpoint failed / unknown instance): degrade to free text.
        return { control: 'combo', options: [], unrecognized: false, note: '' };
    }

    switch (entry.voice_mode) {
        case 'static': {
            const known = new Set(entry.voices.map((v) => v.id));
            const unrecognized = !!currentVoice && !known.has(currentVoice);
            const defaultLabel = entry.default_voice
                ? `— provider default (${entry.default_voice}) —`
                : '— provider default —';
            const options: VoiceOption[] = [{ id: '', label: defaultLabel }];
            if (unrecognized) {
                options.push({
                    id: currentVoice,
                    label: `${currentVoice} (unrecognized — will fall back to provider default)`,
                });
            }
            options.push(...entry.voices);
            return { control: 'select', options, unrecognized, note: '' };
        }
        case 'freeform':
            return {
                control: 'combo', options: entry.voices, unrecognized: false,
                note: 'Pick a suggestion or enter a custom value (e.g. a cloned voice ID). Leave empty for the provider default.',
            };
        case 'platform_managed':
            return {
                control: 'disabled', options: [], unrecognized: false,
                note: 'Voice is managed on the ElevenLabs platform (agent configuration) and cannot be set per AVA agent.',
            };
        default:
            return {
                control: 'disabled', options: [], unrecognized: false,
                note: 'Per-agent voice is not supported for this provider.',
            };
    }
}
