// @vitest-environment jsdom
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import ModelsPage from './ModelsPage';
import type { LiveStatusSnapshot } from '../../hooks/useLiveStatus';

vi.mock('axios');

const liveStatusState = vi.hoisted(() => ({
    current: {
        snapshot: null as LiveStatusSnapshot | null,
        loading: false,
        connected: true,
        mode: 'stream' as const,
        error: null,
    },
}));

vi.mock('../../hooks/useLiveStatus', () => ({
    useLiveStatus: () => liveStatusState.current,
}));

vi.mock('../../hooks/useConfirmDialog', () => ({
    useConfirmDialog: () => ({ confirm: vi.fn().mockResolvedValue(false) }),
}));

const localAIComponent = {
    state: 'degraded',
    freshness: 'fresh',
    summary: 'Local AI degraded, 0/3 models loaded',
    source: 'push',
    updated_at: '2026-06-27T05:31:21Z',
    details: {
        config: {
            runtime_mode: 'minimal',
            degraded: true,
        },
        gpu: {
            runtime_detected: false,
            runtime_usable: false,
            error: 'NVIDIA runtime not available in container.',
        },
        models: {
            stt: {
                backend: 'vosk',
                path: '/app/models/stt/vosk-model-en-us-0.22',
                loaded: false,
                display: 'vosk-model-en-us-0.22',
            },
            llm: {
                path: '/app/models/llm/phi-3-mini-4k-instruct.Q4_K_M.gguf',
                loaded: false,
                display: 'phi-3-mini-4k-instruct.Q4_K_M.gguf',
                config: { context: 768, max_tokens: 64 },
                prompt_fit: {},
                auto_context: {},
                tool_capability: { level: 'none' },
            },
            tts: {
                backend: 'piper',
                path: '/app/models/tts/en_US-lessac-medium.onnx',
                loaded: false,
                display: 'en_US-lessac-medium.onnx',
            },
        },
    },
    metrics: { models_loaded: 0, models_total: 3 },
    warnings: [
        'stt: STT model not found at /app/models/stt/vosk-model-en-us-0.22',
        'tts: TTS model not found at /app/models/tts/en_US-lessac-medium.onnx',
    ],
    errors: [],
};

const liveStatusSnapshot = (): LiveStatusSnapshot => ({
    version: 1,
    event_id: 7,
    generated_at: '2026-06-27T05:31:22Z',
    summary: { state: 'degraded', component_count: 1 },
    components: {
        local_ai_server: localAIComponent,
    },
    local_ai_server: localAIComponent,
});

const installedModelsPayload = {
    stt: {
        vosk: [{ name: 'vosk-model-en-us-0.22', path: '/app/models/stt/vosk-model-en-us-0.22', size_mb: 0 }],
    },
    tts: {
        piper: [{ name: 'en_US-lessac-medium.onnx', path: '/app/models/tts/en_US-lessac-medium.onnx', size_mb: 0 }],
    },
    llm: [
        { name: 'phi-3-mini', path: '/app/models/llm/phi-3-mini-4k-instruct.Q4_K_M.gguf', size_mb: 0 },
    ],
};

const mockModelsPageApis = () => {
    vi.mocked(axios.get).mockImplementation((url) => {
        if (url === '/api/wizard/local/available-models') {
            return Promise.resolve({
                data: {
                    catalog: { stt: [], tts: [], llm: [] },
                    language_names: {},
                    region_names: {},
                },
            });
        }
        if (url === '/api/system/live-status') {
            return Promise.resolve({ data: liveStatusState.current.snapshot || liveStatusSnapshot() });
        }
        if (url === '/api/system/health') {
            return Promise.resolve({
                data: {
                    local_ai_server: {
                        status: 'error',
                        details: { error: 'old health path timed out' },
                    },
                },
            });
        }
        if (url === '/api/local-ai/models') {
            return Promise.resolve({ data: installedModelsPayload });
        }
        if (url === '/api/local-ai/capabilities') {
            return Promise.resolve({ data: { stt: {}, tts: {} } });
        }
        if (url === '/api/config/env') {
            return Promise.resolve({ data: {} });
        }
        if (url === '/api/custom-models') {
            return Promise.resolve({ data: { enabled: false, models: [] } });
        }
        return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
};

describe('ModelsPage Local AI status', () => {
    afterEach(() => {
        liveStatusState.current.snapshot = null;
        liveStatusState.current.loading = false;
        vi.clearAllMocks();
    });

    it('shows degraded pushed Local AI status as reachable instead of unreachable', async () => {
        liveStatusState.current.snapshot = liveStatusSnapshot();
        mockModelsPageApis();

        render(
            <MemoryRouter>
                <ModelsPage />
            </MemoryRouter>
        );

        await waitFor(() => expect(screen.getByText('Degraded')).toBeInTheDocument());

        expect(screen.getByText(/Local AI Server is reachable but degraded/i)).toBeInTheDocument();
        expect(screen.queryByText(/Local AI Server is not reachable/i)).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Start Local AI Server/i })).not.toBeInTheDocument();
        expect(screen.getAllByText('vosk-model-en-us-0.22').length).toBeGreaterThan(0);
        expect(screen.getAllByText('en_US-lessac-medium.onnx').length).toBeGreaterThan(0);
    });

    it('falls back to legacy health status when pushed Local AI state is unknown', async () => {
        liveStatusState.current.snapshot = {
            ...liveStatusSnapshot(),
            components: {
                local_ai_server: {
                    ...localAIComponent,
                    state: 'unknown',
                    summary: 'Local AI status pending',
                },
            },
        };
        mockModelsPageApis();

        render(
            <MemoryRouter>
                <ModelsPage />
            </MemoryRouter>
        );

        await waitFor(() => {
            expect(vi.mocked(axios.get)).toHaveBeenCalledWith('/api/system/health');
        });
        await waitFor(() => expect(screen.getByText('Error')).toBeInTheDocument());
        expect(screen.getByText(/Local AI Server is not reachable/i)).toBeInTheDocument();
    });
});
