// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router-dom';
import axios from 'axios';
import { SystemTopology } from './SystemTopology';
import type { LiveStatusSnapshot } from '../hooks/useLiveStatus';

vi.mock('axios');

const cloudConfigYaml = `
active_pipeline: local_hybrid
default_provider: google_live
providers:
  google_live:
    type: google_live
    enabled: true
pipelines:
  local_hybrid:
    stt: local_stt
    llm: openai_llm
    tts: local_tts
contexts:
  default:
    provider: google_live
`;

const localPipelineContextConfigYaml = `
default_provider: google_live
providers:
  google_live:
    type: google_live
    enabled: true
pipelines:
  local_hybrid:
    stt: local_stt
    llm: openai_llm
    tts: local_tts
contexts:
  default:
    provider: google_live
    pipeline: local_hybrid
`;

const customLocalProviderConfigYaml = `
default_provider: office_local
providers:
  office_local:
    type: local
    enabled: true
pipelines: {}
contexts:
  default:
    provider: office_local
`;

const localFallbackCloudPipelineConfigYaml = `
default_provider: local
providers:
  local:
    type: local
    enabled: true
    capabilities: [stt, llm, tts]
pipelines:
  cambai_pipeline:
    stt: cambai_stt
    llm: cambai_llm
    tts: cambai_tts
contexts:
  default:
    provider: local
    pipeline: cambai_pipeline
`;

const mockTopologyApis = ({
    providerReady = true,
    configYaml = cloudConfigYaml,
    localAIModels,
    providerHealth,
}: {
    providerReady?: boolean;
    configYaml?: string;
    localAIModels?: unknown;
    providerHealth?: Record<string, { ready: boolean }>;
} = {}) => {
    const providerHealthPayload = providerHealth || {
        google_live: { ready: providerReady },
    };

    vi.mocked(axios.get).mockImplementation((url) => {
        if (url === '/api/config/yaml') {
            return Promise.resolve({ data: { content: configYaml } });
        }
        if (url === '/api/system/sessions') {
            return Promise.resolve({ data: { sessions: [] } });
        }
        if (url === '/api/system/health') {
            return Promise.resolve({
                data: {
                    ai_engine: {
                        status: 'connected',
                        details: {
                            ari_connected: true,
                            providers: providerHealthPayload,
                        },
                    },
                    local_ai_server: {
                        status: 'error',
                        details: {
                            error: 'Local AI server is not running',
                            ...(localAIModels === undefined ? {} : { models: localAIModels }),
                        },
                    },
                },
            });
        }
        return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
};

const liveStatusSnapshot = (overrides: Partial<LiveStatusSnapshot> = {}): LiveStatusSnapshot => ({
    version: 1,
    event_id: 1,
    generated_at: '2026-06-26T12:00:00Z',
    summary: { state: 'ready', component_count: 4 },
    components: {
        ai_engine: {
            state: 'ready',
            freshness: 'fresh',
            summary: 'AI Engine connected',
            source: 'probe',
            updated_at: '2026-06-26T12:00:00Z',
            details: {
                ari_connected: true,
                asterisk_channels: 1,
                providers: { google_live: { ready: true } },
            },
            metrics: {},
            warnings: [],
            errors: [],
        },
        local_ai_server: {
            state: 'unreachable',
            freshness: 'fresh',
            summary: 'Local AI unreachable',
            source: 'probe',
            updated_at: '2026-06-26T12:00:00Z',
            details: {},
            metrics: {},
            warnings: [],
            errors: ['Local AI status WebSocket unreachable'],
        },
        sessions: {
            state: 'ready',
            freshness: 'fresh',
            summary: '1 active calls',
            source: 'probe',
            updated_at: '2026-06-26T12:00:00Z',
            details: {
                reachable: true,
                active_calls: 1,
                sessions: [
                    {
                        call_id: 'call-1',
                        provider: 'google_live',
                        pipeline: undefined,
                        conversation_state: 'connected',
                    },
                ],
            },
            metrics: {},
            warnings: [],
            errors: [],
        },
        asterisk: {
            state: 'ready',
            freshness: 'fresh',
            summary: 'Asterisk ARI reachable',
            source: 'probe',
            updated_at: '2026-06-26T12:00:00Z',
            details: { live: { ari_reachable: true } },
            metrics: {},
            warnings: [],
            errors: [],
        },
    },
    ai_engine: undefined,
    local_ai_server: undefined,
    sessions: undefined,
    asterisk: undefined,
    ...overrides,
});

const renderTopology = (props?: React.ComponentProps<typeof SystemTopology>) => render(
    <MemoryRouter>
        <SystemTopology {...props} />
    </MemoryRouter>
);

const flushAsyncEffects = async () => {
    await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
    });
};

afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
});

describe('SystemTopology dashboard health', () => {
    it('hydrates health and sessions from live status without legacy health/session polling', async () => {
        vi.useFakeTimers();
        vi.mocked(axios.get).mockImplementation((url) => {
            if (url === '/api/config/yaml') {
                return Promise.resolve({ data: { content: cloudConfigYaml } });
            }
            return Promise.reject(new Error(`Unexpected URL: ${url}`));
        });

        renderTopology({ liveStatusSnapshot: liveStatusSnapshot() });
        await flushAsyncEffects();

        const requestedUrls = vi.mocked(axios.get).mock.calls.map(([url]) => url);
        expect(requestedUrls).toContain('/api/config/yaml');
        expect(requestedUrls).not.toContain('/api/system/health');
        expect(requestedUrls).not.toContain('/api/system/sessions');
        expect(screen.getAllByText('1 call').length).toBeGreaterThan(0);
        expect(screen.getByText('All systems healthy')).toBeInTheDocument();
    });

    it('uses legacy health/session polling while waiting for a live status snapshot', async () => {
        vi.useFakeTimers();
        mockTopologyApis();

        renderTopology({ liveStatusEnabled: true, liveStatusSnapshot: null });
        await flushAsyncEffects();

        const requestedUrls = vi.mocked(axios.get).mock.calls.map(([url]) => url);
        expect(requestedUrls).toContain('/api/config/yaml');
        expect(requestedUrls).toContain('/api/system/health');
        expect(requestedUrls).toContain('/api/system/sessions');
    });

    it('uses legacy health/session polling when live status is disabled after a snapshot error', async () => {
        vi.useFakeTimers();
        mockTopologyApis();

        renderTopology({ liveStatusEnabled: false, liveStatusSnapshot: liveStatusSnapshot() });
        await flushAsyncEffects();

        const requestedUrls = vi.mocked(axios.get).mock.calls.map(([url]) => url);
        expect(requestedUrls).toContain('/api/config/yaml');
        expect(requestedUrls).toContain('/api/system/health');
        expect(requestedUrls).toContain('/api/system/sessions');
    });

    it('shows healthy for cloud Google Live when Local AI is optional and stopped', async () => {
        vi.useFakeTimers();
        mockTopologyApis();

        renderTopology();
        await flushAsyncEffects();
        await act(async () => {
            await vi.advanceTimersByTimeAsync(5000);
        });

        expect(screen.getByText('All systems healthy')).toBeInTheDocument();
        expect(screen.queryByText('Issue detected')).not.toBeInTheDocument();
        expect(screen.getByText('Optional Local AI Server is unavailable')).toBeInTheDocument();
        expect(screen.getByText('Optional offline')).toBeInTheDocument();
    });

    it('hides stale local model counts when optional Local AI is unavailable', async () => {
        vi.useFakeTimers();
        mockTopologyApis({
            localAIModels: {
                stt: { backend: 'whisper', loaded: true },
                llm: { loaded: true },
                tts: { backend: 'piper', loaded: true },
            },
        });

        renderTopology();
        await flushAsyncEffects();
        await act(async () => {
            await vi.advanceTimersByTimeAsync(5000);
        });

        expect(screen.getByText('Optional Local AI Server is unavailable')).toBeInTheDocument();
        expect(screen.queryByText(/local models loaded/i)).not.toBeInTheDocument();
    });

    it('treats contexts.default.pipeline local routes as Local AI requirements', async () => {
        vi.useFakeTimers();
        mockTopologyApis({ configYaml: localPipelineContextConfigYaml });

        renderTopology();
        await flushAsyncEffects();

        await act(async () => {
            await vi.advanceTimersByTimeAsync(5000);
        });

        const issueButton = screen.getByRole('button', { name: /issue detected/i });
        fireEvent.click(issueButton);

        expect(screen.getByText('Local AI Server is disconnected')).toBeInTheDocument();
        expect(screen.getByText('The active or default route uses Local AI, but local_ai_server is not connected.')).toBeInTheDocument();
        expect(screen.queryByText('Optional Local AI Server is unavailable')).not.toBeInTheDocument();
    });

    it('treats custom-key local providers as Local AI requirements', async () => {
        vi.useFakeTimers();
        mockTopologyApis({
            configYaml: customLocalProviderConfigYaml,
            providerHealth: { office_local: { ready: true } },
        });

        renderTopology();
        await flushAsyncEffects();

        await act(async () => {
            await vi.advanceTimersByTimeAsync(5000);
        });

        fireEvent.click(screen.getByRole('button', { name: /issue detected/i }));

        expect(screen.getByText('Local AI Server is disconnected')).toBeInTheDocument();
        expect(screen.queryByText('Optional Local AI Server is unavailable')).not.toBeInTheDocument();
    });

    it('keeps Local AI optional when a non-local context pipeline precedes a local fallback provider', async () => {
        vi.useFakeTimers();
        mockTopologyApis({
            configYaml: localFallbackCloudPipelineConfigYaml,
            providerHealth: { local: { ready: true } },
        });

        renderTopology();
        await flushAsyncEffects();

        await act(async () => {
            await vi.advanceTimersByTimeAsync(5000);
        });

        expect(screen.getByText('All systems healthy')).toBeInTheDocument();
        expect(screen.getByText('Optional Local AI Server is unavailable')).toBeInTheDocument();
        expect(screen.queryByText('Issue detected')).not.toBeInTheDocument();
    });

    it('opens warning details when optional Local AI is unavailable', async () => {
        vi.useFakeTimers();
        mockTopologyApis();

        renderTopology();
        await flushAsyncEffects();
        await act(async () => {
            await vi.advanceTimersByTimeAsync(5000);
        });

        fireEvent.click(screen.getByRole('button', { name: 'Optional Local AI Server is unavailable' }));

        expect(screen.getAllByText('Optional Local AI Server is unavailable').length).toBeGreaterThan(1);
        expect(screen.getByText('Calls can continue on the configured cloud provider, but local pipelines and local models are unavailable until local_ai_server reconnects.')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Models' })).toBeInTheDocument();
    });

    it('opens issue details from the summary when a real issue is detected', async () => {
        vi.useFakeTimers();
        mockTopologyApis({ providerReady: false });

        renderTopology();
        await flushAsyncEffects();

        await act(async () => {
            await vi.advanceTimersByTimeAsync(5000);
        });

        const issueButton = screen.getByRole('button', { name: /issue detected/i });
        fireEvent.click(issueButton);

        expect(screen.getByText('Provider google_live is not ready')).toBeInTheDocument();
        expect(screen.getByText('The enabled provider health check is failing.')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Providers' })).toBeInTheDocument();
    });
});
