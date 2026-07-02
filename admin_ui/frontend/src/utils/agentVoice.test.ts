import { describe, expect, it } from 'vitest';
import { voiceControlState, type ProviderVoiceMeta } from './agentVoice';

const META: ProviderVoiceMeta[] = [
    {
        name: 'openai_realtime', kind: 'openai_realtime', is_full_agent: true, enabled: true,
        voice_mode: 'static',
        voices: [{ id: 'alloy', label: 'Alloy' }, { id: 'marin', label: 'Marin' }],
        default_voice: 'alloy',
    },
    {
        name: 'grok', kind: 'grok', is_full_agent: true, enabled: true,
        voice_mode: 'freeform',
        voices: [{ id: 'eve', label: 'eve' }],
        default_voice: 'eve',
    },
    {
        name: 'elevenlabs', kind: 'elevenlabs_agent', is_full_agent: true, enabled: true,
        voice_mode: 'platform_managed', voices: [], default_voice: null,
    },
];

describe('voiceControlState', () => {
    it('disables with guidance when no engine is selected', () => {
        const s = voiceControlState(META, '', '');
        expect(s.control).toBe('disabled');
        expect(s.note).toMatch(/select an ai engine/i);
    });

    it('disables for pipelines and points at the pipeline TTS provider', () => {
        const s = voiceControlState(META, 'pipeline:local_hybrid', '');
        expect(s.control).toBe('disabled');
        expect(s.note).toMatch(/pipeline/i);
    });

    it('renders a select for static providers with a provider-default option', () => {
        const s = voiceControlState(META, 'provider:openai_realtime', 'marin');
        expect(s.control).toBe('select');
        expect(s.options[0].id).toBe('');
        expect(s.options[0].label).toMatch(/provider default \(alloy\)/i);
        expect(s.options.some((o) => o.id === 'marin')).toBe(true);
        expect(s.unrecognized).toBe(false);
    });

    it('keeps an unrecognized stored value selectable but flags it', () => {
        const s = voiceControlState(META, 'provider:openai_realtime', 'Jenny - British');
        expect(s.control).toBe('select');
        expect(s.unrecognized).toBe(true);
        const kept = s.options.find((o) => o.id === 'Jenny - British');
        expect(kept?.label).toMatch(/unrecognized/i);
    });

    it('renders a combo for freeform providers (custom values allowed)', () => {
        const s = voiceControlState(META, 'provider:grok', 'myCloneID');
        expect(s.control).toBe('combo');
        expect(s.options.some((o) => o.id === 'eve')).toBe(true);
        expect(s.note).toMatch(/custom/i);
    });

    it('disables for platform-managed providers with an explanation', () => {
        const s = voiceControlState(META, 'provider:elevenlabs', '');
        expect(s.control).toBe('disabled');
        expect(s.note).toMatch(/elevenlabs platform/i);
    });

    it('degrades to a free-text combo when metadata is unavailable', () => {
        const s = voiceControlState(null, 'provider:openai_realtime', 'alloy');
        expect(s.control).toBe('combo');
        expect(s.options).toEqual([]);
    });

    it('disables for providers whose kind does not support per-agent voice', () => {
        const meta: ProviderVoiceMeta[] = [{
            name: 'local', kind: 'local', is_full_agent: true, enabled: true,
            voice_mode: 'unsupported', voices: [], default_voice: null,
        }];
        const s = voiceControlState(meta, 'provider:local', '');
        expect(s.control).toBe('disabled');
        expect(s.note).toMatch(/not supported/i);
    });
});
