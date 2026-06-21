import { describe, it, expect } from 'vitest';
import { getEffectiveFullAgentKind } from './providerNaming';

/**
 * Regression coverage for GitHub issue #436: a canonical Google Live provider
 * stored in the supported legacy form `google_live: { type: full }` could not be
 * edited or saved — the provider editor resolved its concrete adapter kind only
 * when `type` was absent, so `type: full` was misrepresented (selector showed
 * OpenAI Realtime) and rejected on save ("Select a full-agent provider type.").
 *
 * getEffectiveFullAgentKind resolves the concrete full-agent kind the same way
 * the engine does, so the editor can display and validate it correctly.
 */
describe('getEffectiveFullAgentKind', () => {
    it('resolves canonical google_live with legacy type:full to google_live (#436)', () => {
        expect(getEffectiveFullAgentKind({ type: 'full' }, 'google_live')).toBe('google_live');
    });

    it('returns the concrete kind when an explicit full-agent type is set', () => {
        expect(getEffectiveFullAgentKind({ type: 'google_live' }, 'google_live')).toBe('google_live');
        expect(getEffectiveFullAgentKind({ type: 'openai_realtime' }, 'my_openai')).toBe('openai_realtime');
    });

    it('resolves canonical local with type:full to local (monolithic Local AI)', () => {
        expect(getEffectiveFullAgentKind({ type: 'full' }, 'local')).toBe('local');
    });

    it('resolves the legacy single-instance form (canonical key, no type) to that kind', () => {
        expect(getEffectiveFullAgentKind({}, 'grok')).toBe('grok');
    });

    it('does not name-guess a kind for a neutral custom key with type:full', () => {
        expect(getEffectiveFullAgentKind({ type: 'full' }, 'my_custom_agent')).toBeNull();
    });

    it('returns null for a modular single-capability provider (type:local is not a full agent)', () => {
        expect(getEffectiveFullAgentKind({ type: 'local', capabilities: ['stt'] }, 'local_stt')).toBeNull();
    });

    it('resolves type:local WITH all three capabilities to local (monolithic Local full agent)', () => {
        // The Provider Type dropdown's "Local" option saves type:'local' with full
        // capabilities; this must remain a savable full agent (regression: #440 review).
        expect(getEffectiveFullAgentKind({ type: 'local', capabilities: ['stt', 'llm', 'tts'] }, 'local')).toBe('local');
    });
});
