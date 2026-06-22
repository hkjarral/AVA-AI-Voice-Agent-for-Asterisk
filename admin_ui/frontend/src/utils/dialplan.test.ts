import { describe, it, expect } from 'vitest';
import { getDialplanProviderOverride, buildAgentDialplan } from './dialplan';

describe('getDialplanProviderOverride', () => {
    it('passes through supported providers', () => {
        expect(getDialplanProviderOverride('grok')).toBe('grok');
        expect(getDialplanProviderOverride('deepgram')).toBe('deepgram');
        expect(getDialplanProviderOverride('local')).toBe('local');
    });

    it('falls back to openai_realtime for unsupported providers', () => {
        expect(getDialplanProviderOverride('made_up')).toBe('openai_realtime');
        expect(getDialplanProviderOverride('')).toBe('openai_realtime');
    });
});

describe('buildAgentDialplan', () => {
    it('emits AI_AGENT (slug) and never the legacy AI_CONTEXT', () => {
        const snippet = buildAgentDialplan('grok');
        expect(snippet).toContain('Set(AI_AGENT=default)');
        expect(snippet).not.toContain('AI_CONTEXT');
    });

    it('keeps a per-provider AI_PROVIDER override line', () => {
        expect(buildAgentDialplan('deepgram')).toContain('Set(AI_PROVIDER=deepgram)');
        // unsupported provider still resolves to a valid override
        expect(buildAgentDialplan('made_up')).toContain('Set(AI_PROVIDER=openai_realtime)');
    });

    it('produces a complete from-ai-agent context', () => {
        const snippet = buildAgentDialplan('grok');
        expect(snippet).toContain('[from-ai-agent]');
        expect(snippet).toContain('Stasis(asterisk-ai-voice-agent)');
        expect(snippet).toContain('Hangup()');
    });
});
