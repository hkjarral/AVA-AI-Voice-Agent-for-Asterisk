// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import yaml from 'js-yaml';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ProvidersPage from './ProvidersPage';

const mocks = vi.hoisted(() => ({
    config: {} as Record<string, unknown>,
    refetch: vi.fn().mockResolvedValue(undefined),
    toastError: vi.fn(),
}));

vi.mock('axios');
vi.mock('sonner', () => ({
    toast: {
        error: mocks.toastError,
        success: vi.fn(),
        warning: vi.fn(),
        info: vi.fn(),
    },
}));
vi.mock('../hooks/useConfirmDialog', () => ({
    useConfirmDialog: () => ({ confirm: vi.fn() }),
}));
vi.mock('../hooks/useRestartRequired', () => ({
    useRestartRequired: () => ({
        restartRequired: false,
        refetch: mocks.refetch,
    }),
}));
vi.mock('../utils/configCache', () => ({
    getCachedConfig: () => ({ config: mocks.config, yamlError: null }),
    loadConfigYaml: vi.fn(async () => ({ config: mocks.config, yamlError: null })),
}));

describe('ProvidersPage OpenAI Realtime save contract', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        vi.mocked(axios.get).mockResolvedValue({ data: {} });
        vi.mocked(axios.post).mockResolvedValue({ data: {}, status: 200 });
    });

    it.each([
        {
            label: 'explicit GA',
            apiVersion: 'ga',
            expectedEncoding: 'linear16',
            expectedRate: 24000,
        },
        {
            label: 'the omitted API version that defaults to GA',
            apiVersion: undefined,
            expectedEncoding: 'linear16',
            expectedRate: 24000,
        },
        {
            label: 'Beta',
            apiVersion: 'beta',
            expectedEncoding: 'mulaw',
            expectedRate: 8000,
        },
    ])('serializes the correct audio pair for $label', async ({
        apiVersion,
        expectedEncoding,
        expectedRate,
    }) => {
        mocks.config = {
            providers: {
                openai_realtime: {
                    type: 'openai_realtime',
                    capabilities: ['stt', 'llm', 'tts'],
                    enabled: true,
                    ...(apiVersion ? { api_version: apiVersion } : {}),
                    output_encoding: 'mulaw',
                    output_sample_rate_hz: 8000,
                    model: 'gpt-realtime',
                    voice: 'alloy',
                },
            },
            default_provider: 'openai_realtime',
        };

        render(
            <MemoryRouter>
                <ProvidersPage />
            </MemoryRouter>,
        );

        fireEvent.click(await screen.findByTitle('Settings'));
        const dialog = await screen.findByRole('dialog', {
            name: 'Edit Provider: openai_realtime',
        });
        fireEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }));

        await waitFor(() => {
            expect(axios.post).toHaveBeenCalledWith(
                '/api/config/yaml',
                expect.objectContaining({ content: expect.any(String) }),
            );
        });
        const saveCall = vi.mocked(axios.post).mock.calls.find(([url]) => url === '/api/config/yaml');
        expect(saveCall).toBeDefined();
        const body = saveCall?.[1] as { content: string };
        const saved = yaml.load(body.content) as {
            providers: Record<string, Record<string, unknown>>;
        };
        expect(saved.providers.openai_realtime.output_encoding).toBe(expectedEncoding);
        expect(saved.providers.openai_realtime.output_sample_rate_hz).toBe(expectedRate);
        if (apiVersion === undefined) {
            expect(saved.providers.openai_realtime).not.toHaveProperty('api_version');
        } else {
            expect(saved.providers.openai_realtime.api_version).toBe(apiVersion);
        }
    });
});
