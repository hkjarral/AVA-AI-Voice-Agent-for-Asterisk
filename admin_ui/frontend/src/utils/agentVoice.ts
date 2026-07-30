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
    /** Normalized full-agent conversation language (Deepgram only). */
    agent_language?: string | null;
}

export interface VoiceControlState {
    /** select = closed list; combo = suggestions + free text; disabled = no per-agent voice */
    control: 'select' | 'combo' | 'disabled';
    options: VoiceOption[];
    /** True when the stored value is not in a static provider's catalog. */
    unrecognized: boolean;
    /** Deepgram rejects unknown explicit Aura values instead of falling back. */
    unrecognizedFailsClosed: boolean;
    /** A known Deepgram voice whose language conflicts with the provider. */
    languageMismatch: boolean;
    /** Normalized provider language when the voice catalog is constrained. */
    requiredLanguage: string | null;
    note: string;
}

const normalizeLanguage = (value: unknown): string =>
    String(value || 'en')
        .trim()
        .toLowerCase()
        .replace('_', '-')
        .split('-', 1)[0] || 'en';

const deepgramVoiceLanguage = (voiceId: string): string | null => {
    const match = voiceId.trim().toLowerCase().match(/-([a-z]{2})$/);
    return match?.[1] || null;
};

export function voiceControlState(
    meta: ProviderVoiceMeta[] | null,
    engineValue: string,
    currentVoice: string,
): VoiceControlState {
    if (!engineValue) {
        return {
            control: 'disabled', options: [], unrecognized: false,
            unrecognizedFailsClosed: false,
            languageMismatch: false, requiredLanguage: null,
            note: 'Select an AI Engine to choose a voice.',
        };
    }
    if (engineValue.startsWith('pipeline:')) {
        return {
            control: 'disabled', options: [], unrecognized: false,
            unrecognizedFailsClosed: false,
            languageMismatch: false, requiredLanguage: null,
            note: "Voice comes from the pipeline's TTS provider configuration.",
        };
    }

    const providerName = engineValue.slice('provider:'.length);
    const entry = meta?.find((m) => m.name === providerName);
    if (!meta || !entry) {
        // Metadata unavailable (endpoint failed / unknown instance): degrade to free text.
        return {
            control: 'combo', options: [], unrecognized: false,
            unrecognizedFailsClosed: false,
            languageMismatch: false, requiredLanguage: null, note: '',
        };
    }

    switch (entry.voice_mode) {
        case 'static': {
            const known = new Set(entry.voices.map((v) => v.id));
            const unrecognized = !!currentVoice && !known.has(currentVoice);
            const unrecognizedFailsClosed = unrecognized && entry.kind === 'deepgram';
            const requiredLanguage = entry.kind === 'deepgram'
                ? normalizeLanguage(entry.agent_language)
                : null;
            const selectableVoices = requiredLanguage
                ? entry.voices.filter(
                    (candidate) => deepgramVoiceLanguage(candidate.id) === requiredLanguage
                )
                : entry.voices;
            const languageMismatch = !!currentVoice
                && !unrecognized
                && requiredLanguage !== null
                && deepgramVoiceLanguage(currentVoice) !== requiredLanguage;
            const defaultLabel = entry.default_voice
                ? `— provider default (${entry.default_voice}) —`
                : '— provider default —';
            const options: VoiceOption[] = [{ id: '', label: defaultLabel }];
            if (unrecognized) {
                options.push({
                    id: currentVoice,
                    label: unrecognizedFailsClosed
                        ? `${currentVoice} (unrecognized — call will fail)`
                        : `${currentVoice} (unrecognized — will fall back to provider default)`,
                });
            } else if (languageMismatch) {
                options.push({
                    id: currentVoice,
                    label: `${currentVoice} (does not match provider language ${requiredLanguage})`,
                });
            }
            options.push(...selectableVoices);
            return {
                control: 'select', options, unrecognized, unrecognizedFailsClosed,
                languageMismatch,
                requiredLanguage, note: '',
            };
        }
        case 'freeform':
            return {
                control: 'combo', options: entry.voices, unrecognized: false,
                unrecognizedFailsClosed: false,
                languageMismatch: false, requiredLanguage: null,
                note: 'Pick a suggestion or enter a custom value (e.g. a cloned voice ID). Leave empty for the provider default.',
            };
        case 'platform_managed':
            return {
                control: 'disabled', options: [], unrecognized: false,
                unrecognizedFailsClosed: false,
                languageMismatch: false, requiredLanguage: null,
                note: 'Voice is managed on the ElevenLabs platform (agent configuration) and cannot be set per AVA agent.',
            };
        default:
            return {
                control: 'disabled', options: [], unrecognized: false,
                unrecognizedFailsClosed: false,
                languageMismatch: false, requiredLanguage: null,
                note: 'Per-agent voice is not supported for this provider.',
            };
    }
}
