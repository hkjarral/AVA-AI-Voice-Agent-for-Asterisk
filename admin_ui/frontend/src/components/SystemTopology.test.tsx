// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
// eslint-disable-next-line @typescript-eslint/no-unused-vars -- Keeps this test portable in the server-side disposable runner.
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router-dom';
import axios from 'axios';
import { SystemTopology } from './SystemTopology';

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
    capabilities: [stt, llm, tts]
pipelines: {}
contexts:
  default:
    provider: office_local
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

const renderTopology = () => render(
    <MemoryRouter>
        <SystemTopology />
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
