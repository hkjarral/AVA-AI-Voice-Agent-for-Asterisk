// @vitest-environment jsdom

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import yaml from 'js-yaml';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ProvidersPage from './ProvidersPage';

const mocks = vi.hoisted(() => ({
    config: {} as Record<string, unknown>,
    refetch: vi.fn().mockResolvedValue(undefined),
    confirm: vi.fn().mockResolvedValue(true),
    loadConfigYaml: vi.fn(),
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
    useConfirmDialog: () => ({ confirm: mocks.confirm }),
}));
vi.mock('../hooks/useRestartRequired', () => ({
    useRestartRequired: () => ({
        restartRequired: false,
        refetch: mocks.refetch,
    }),
}));
vi.mock('../utils/configCache', () => ({
    getCachedConfig: () => ({ config: mocks.config, yamlError: null }),
    loadConfigYaml: mocks.loadConfigYaml,
}));

describe('ProvidersPage OpenAI Realtime save contract', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mocks.confirm.mockResolvedValue(true);
        mocks.loadConfigYaml.mockImplementation(async () => ({
            config: mocks.config,
            yamlError: null,
        }));
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

    it('does not replace provider B form data when provider A reset completes late', async () => {
        mocks.config = {
            providers: {
                provider_a: {
                    type: 'openai_realtime',
                    capabilities: ['stt', 'llm', 'tts'],
                    model: 'gpt-realtime',
                },
                provider_b: {
                    type: 'deepgram',
                    capabilities: ['stt', 'llm', 'tts'],
                    model: 'nova-3',
                },
            },
        };
        let resolveResetFetch: (value: unknown) => void = () => undefined;
        const resetFetch = new Promise((resolve) => {
            resolveResetFetch = resolve;
        });

        render(
            <MemoryRouter>
                <ProvidersPage />
            </MemoryRouter>,
        );

        const settings = await screen.findAllByTitle('Settings');
        fireEvent.click(settings[0]);
        const providerADialog = await screen.findByRole('dialog', {
            name: 'Edit Provider: provider_a',
        });
        mocks.loadConfigYaml.mockReturnValueOnce(resetFetch);
        fireEvent.click(within(providerADialog).getByRole('button', {
            name: 'Restore audio defaults',
        }));
        await waitFor(() => expect(mocks.loadConfigYaml).toHaveBeenCalledTimes(2));

        fireEvent.click(within(providerADialog).getByRole('button', { name: 'Cancel' }));
        fireEvent.click(screen.getAllByTitle('Settings')[1]);
        const providerBDialog = await screen.findByRole('dialog', {
            name: 'Edit Provider: provider_b',
        });
        expect(within(providerBDialog).getByDisplayValue('provider_b')).toBeInTheDocument();

        await act(async () => {
            resolveResetFetch({
                config: {
                    providers: {
                        provider_a: {
                            type: 'openai_realtime',
                            capabilities: ['stt', 'llm', 'tts'],
                            model: 'reset-a',
                        },
                        provider_b: {
                            type: 'deepgram',
                            capabilities: ['stt', 'llm', 'tts'],
                            model: 'nova-3',
                        },
                    },
                },
                yamlError: null,
            });
        });

        await waitFor(() => {
            expect(within(providerBDialog).getByDisplayValue('provider_b')).toBeInTheDocument();
        });
        expect(within(providerBDialog).queryByDisplayValue('provider_a')).not.toBeInTheDocument();
    });

    it('removes stale Flux-only fields when Deepgram is saved with Nova-3', async () => {
        mocks.config = {
            providers: {
                deepgram: {
                    type: 'deepgram',
                    capabilities: ['stt', 'llm', 'tts'],
                    enabled: true,
                    model: 'nova-3',
                    agent_language: 'es',
                    tts_model: 'aura-2-celeste-es',
                    version: 'v2',
                    eot_threshold: 0.7,
                    eager_eot_threshold: 0.5,
                    keyterms: ['Asterisk'],
                },
            },
            default_provider: 'deepgram',
        };

        render(
            <MemoryRouter>
                <ProvidersPage />
            </MemoryRouter>,
        );

        fireEvent.click(await screen.findByTitle('Settings'));
        const dialog = await screen.findByRole('dialog', {
            name: 'Edit Provider: deepgram',
        });
        fireEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }));

        await waitFor(() => {
            expect(axios.post).toHaveBeenCalledWith(
                '/api/config/yaml',
                expect.objectContaining({ content: expect.any(String) }),
            );
        });
        const saveCall = vi.mocked(axios.post).mock.calls.find(([url]) => url === '/api/config/yaml');
        const body = saveCall?.[1] as { content: string };
        const saved = yaml.load(body.content) as {
            providers: Record<string, Record<string, unknown>>;
        };
        expect(saved.providers.deepgram.model).toBe('nova-3');
        expect(saved.providers.deepgram).not.toHaveProperty('version');
        expect(saved.providers.deepgram).not.toHaveProperty('eot_threshold');
        expect(saved.providers.deepgram).not.toHaveProperty('eager_eot_threshold');
        expect(saved.providers.deepgram).not.toHaveProperty('keyterms');
    });

    it('preserves version and tuning fields for a custom Deepgram model', async () => {
        mocks.config = {
            providers: {
                deepgram: {
                    type: 'deepgram',
                    capabilities: ['stt', 'llm', 'tts'],
                    enabled: true,
                    model: 'customer-private-model',
                    agent_language: 'en',
                    tts_model: 'aura-2-luna-en',
                    version: 'private-v2',
                    eot_threshold: 0.8,
                    eager_eot_threshold: 0.4,
                    keyterms: ['PrivateTerm'],
                },
            },
            default_provider: 'deepgram',
        };

        render(
            <MemoryRouter>
                <ProvidersPage />
            </MemoryRouter>,
        );

        fireEvent.click(await screen.findByTitle('Settings'));
        const dialog = await screen.findByRole('dialog', {
            name: 'Edit Provider: deepgram',
        });
        fireEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }));

        await waitFor(() => {
            expect(axios.post).toHaveBeenCalledWith(
                '/api/config/yaml',
                expect.objectContaining({ content: expect.any(String) }),
            );
        });
        const saveCall = vi.mocked(axios.post).mock.calls.find(([url]) => url === '/api/config/yaml');
        const body = saveCall?.[1] as { content: string };
        const saved = yaml.load(body.content) as {
            providers: Record<string, Record<string, unknown>>;
        };
        expect(saved.providers.deepgram).toEqual(
            expect.objectContaining({
                model: 'customer-private-model',
                version: 'private-v2',
                eot_threshold: 0.8,
                eager_eot_threshold: 0.4,
                keyterms: ['PrivateTerm'],
            }),
        );
    });

    it('serializes the OpenAI Realtime template with the GA output contract', async () => {
        mocks.config = { providers: {} };

        render(
            <MemoryRouter>
                <ProvidersPage />
            </MemoryRouter>,
        );

        fireEvent.click(await screen.findByRole('button', { name: 'Add Provider Templates' }));
        const dialog = await screen.findByRole('dialog', { name: 'Add Provider Templates' });
        fireEvent.click(within(dialog).getByRole('checkbox', { name: /OpenAI Realtime/i }));
        fireEvent.click(within(dialog).getByRole('button', { name: 'Add Selected' }));

        await waitFor(() => {
            expect(axios.post).toHaveBeenCalledWith(
                '/api/config/yaml',
                expect.objectContaining({ content: expect.any(String) }),
            );
        });
        const saveCall = vi.mocked(axios.post).mock.calls.find(([url]) => url === '/api/config/yaml');
        const body = saveCall?.[1] as { content: string };
        const saved = yaml.load(body.content) as {
            providers: Record<string, Record<string, unknown>>;
        };
        expect(saved.providers.openai_realtime).toMatchObject({
            api_version: 'ga',
            output_encoding: 'linear16',
            output_sample_rate_hz: 24000,
        });
    });
});
