import { describe, expect, it } from 'vitest';
import { deriveTopologyHealth, type TopologyHealthInput } from './topologyHealth';

const baseInput: TopologyHealthInput = {
    aiEngineStatus: 'connected',
    ariConnected: true,
    localAIStatus: 'error',
    configuredProviders: [{ name: 'google_live', enabled: true }],
    providerReady: { google_live: 'ready' },
    configuredPipelines: [
        { name: 'local_hybrid', stt: 'local_stt', llm: 'openai_llm', tts: 'local_tts' },
    ],
    defaultProvider: 'google_live',
    defaultPipeline: null,
    activePipeline: null,
    activeProviderNames: [],
    activePipelineNames: [],
};

const derive = (overrides: Partial<TopologyHealthInput> = {}) =>
    deriveTopologyHealth({ ...baseInput, ...overrides });

describe('deriveTopologyHealth', () => {
    it('does not mark cloud-only Google Live unhealthy when optional Local AI is absent (#468)', () => {
        const result = derive();

        expect(result.localAIRequired).toBe(false);
        expect(result.localAIOptionalUnavailable).toBe(true);
        expect(result.overallStatus).toBe('healthy');
        expect(result.issues).toEqual([]);
        expect(result.warnings).toEqual([
            expect.objectContaining({
                key: 'local_ai_server_optional',
                target: 'models',
            }),
        ]);
    });

    it('does not require Local AI just because an inactive local pipeline exists', () => {
        const result = derive({
            configuredPipelines: [
                { name: 'local_hybrid', stt: 'local_stt', llm: 'openai_llm', tts: 'local_tts' },
                { name: 'local_only', stt: 'local_stt', llm: 'local_llm', tts: 'local_tts' },
            ],
            defaultProvider: 'google_live',
            activePipeline: null,
        });

        expect(result.localAIRequired).toBe(false);
        expect(result.overallStatus).toBe('healthy');
    });

    it('does not require Local AI for a cloud default provider with a legacy local active pipeline', () => {
        const result = derive({
            activePipeline: 'local_hybrid',
            defaultProvider: 'google_live',
        });

        expect(result.localAIRequired).toBe(false);
        expect(result.localAIOptionalUnavailable).toBe(true);
        expect(result.overallStatus).toBe('healthy');
        expect(result.issues).toEqual([]);
        expect(result.warnings.map(w => w.key)).toEqual(['local_ai_server_optional']);
    });

    it('requires Local AI when the default provider is local', () => {
        const result = derive({
            configuredProviders: [{ name: 'local', enabled: true }],
            providerReady: { local: 'ready' },
            defaultProvider: 'local',
        });

        expect(result.localAIRequired).toBe(true);
        expect(result.overallStatus).toBe('issue');
        expect(result.issues.map(i => i.key)).toContain('local_ai_server');
        expect(result.warnings).toEqual([]);
    });

    it('does not require Local AI when a non-local default context pipeline precedes a local fallback provider', () => {
        const result = derive({
            configuredProviders: [{ name: 'local', kind: 'local', enabled: true }],
            providerReady: { local: 'ready' },
            configuredPipelines: [
                { name: 'cambai_pipeline', stt: 'cambai_stt', llm: 'cambai_llm', tts: 'cambai_tts' },
            ],
            defaultProvider: 'local',
            defaultPipeline: 'cambai_pipeline',
            activePipeline: null,
        });

        expect(result.localAIRequired).toBe(false);
        expect(result.localAIOptionalUnavailable).toBe(true);
        expect(result.overallStatus).toBe('healthy');
        expect(result.issues).toEqual([]);
        expect(result.warnings.map(w => w.key)).toEqual(['local_ai_server_optional']);
    });

    it('requires Local AI when a custom-key default provider has local kind', () => {
        const result = derive({
            configuredProviders: [{ name: 'office_local', kind: 'local', enabled: true }],
            providerReady: { office_local: 'ready' },
            defaultProvider: 'office_local',
        });

        expect(result.localAIRequired).toBe(true);
        expect(result.overallStatus).toBe('issue');
        expect(result.issues.map(i => i.key)).toContain('local_ai_server');
        expect(result.warnings).toEqual([]);
    });

    it('requires Local AI when an active call uses a custom-key local provider', () => {
        const result = derive({
            configuredProviders: [{ name: 'office_local', kind: 'local', enabled: true }],
            providerReady: { office_local: 'ready' },
            activeProviderNames: ['office_local'],
        });

        expect(result.localAIRequired).toBe(true);
        expect(result.overallStatus).toBe('issue');
        expect(result.issues.map(i => i.key)).toContain('local_ai_server');
    });

    it('requires Local AI when the configured active pipeline is the default route', () => {
        const result = derive({
            activePipeline: 'local_hybrid',
            defaultProvider: null,
        });

        expect(result.localAIRequired).toBe(true);
        expect(result.overallStatus).toBe('issue');
        expect(result.issues.map(i => i.key)).toContain('local_ai_server');
    });

    it('requires Local AI when the default context pipeline uses local components', () => {
        const result = derive({
            defaultProvider: 'google_live',
            defaultPipeline: 'local_hybrid',
            activePipeline: null,
        });

        expect(result.localAIRequired).toBe(true);
        expect(result.overallStatus).toBe('issue');
        expect(result.issues.map(i => i.key)).toContain('local_ai_server');
        expect(result.warnings).toEqual([]);
    });

    it('matches base route names to suffixed local pipeline variants', () => {
        const result = derive({
            configuredPipelines: [
                { name: 'local_hybrid_groq', stt: 'local_stt', llm: 'groq_llm', tts: 'local_tts' },
            ],
            defaultProvider: 'google_live',
            defaultPipeline: 'local_hybrid',
            activePipeline: null,
        });

        expect(result.localAIRequired).toBe(true);
        expect(result.overallStatus).toBe('issue');
        expect(result.issues.map(i => i.key)).toContain('local_ai_server');
    });

    it('requires Local AI when an active call uses a local pipeline', () => {
        const result = derive({
            activePipelineNames: ['local_hybrid'],
        });

        expect(result.localAIRequired).toBe(true);
        expect(result.overallStatus).toBe('issue');
        expect(result.issues.map(i => i.key)).toContain('local_ai_server');
    });

    it('requires Local AI when an active call uses the local full agent', () => {
        const result = derive({
            activeProviderNames: ['local'],
        });

        expect(result.localAIRequired).toBe(true);
        expect(result.overallStatus).toBe('issue');
        expect(result.issues.map(i => i.key)).toContain('local_ai_server');
    });

    it('does not get stuck checking when unused Local AI is unknown', () => {
        const result = derive({
            localAIStatus: 'unknown',
        });

        expect(result.localAIRequired).toBe(false);
        expect(result.overallStatus).toBe('healthy');
    });

    it('returns provider issue reasons for enabled providers that are not ready', () => {
        const result = derive({
            providerReady: { google_live: 'not_ready' },
        });

        expect(result.overallStatus).toBe('issue');
        expect(result.issues).toEqual([
            expect.objectContaining({
                key: 'provider:google_live',
                target: 'providers',
            }),
        ]);
    });

    it('ignores disabled providers in status and reasons', () => {
        const result = derive({
            configuredProviders: [
                { name: 'google_live', enabled: true },
                { name: 'openai_realtime', enabled: false },
            ],
            providerReady: { google_live: 'ready', openai_realtime: 'not_ready' },
        });

        expect(result.overallStatus).toBe('healthy');
        expect(result.issues).toEqual([]);
    });

    it('returns ARI and AI Engine issue reasons', () => {
        const result = derive({
            aiEngineStatus: 'error',
            ariConnected: false,
        });

        expect(result.overallStatus).toBe('issue');
        expect(result.issues.map(i => i.key)).toEqual(['ai_engine', 'ari']);
    });

    it('waits for provider readiness when enabled providers are still unknown', () => {
        const result = derive({
            providerReady: {},
        });

        expect(result.overallStatus).toBe('checking');
        expect(result.issues).toEqual([]);
    });
});
