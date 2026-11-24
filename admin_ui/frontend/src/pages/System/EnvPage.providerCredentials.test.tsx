// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import EnvPage from './EnvPage';

vi.mock('axios');
vi.mock('../../auth/AuthContext', () => ({
    useAuth: () => ({ token: 'test-token', loading: false }),
}));
vi.mock('../../hooks/useConfirmDialog', () => ({
    useConfirmDialog: () => ({ confirm: vi.fn().mockResolvedValue(false) }),
}));

describe('EnvPage provider credential audit', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        vi.mocked(axios.get).mockImplementation(async url => {
            if (url === '/api/config/env') return { data: { AAVA_OUTBOUND_PBX_TYPE: { set: true }, OPENAI_API_KEY: { set: true }, CUSTOM_SUPPLIER_KEY: { set: false }, SMTP_PASSWORD: { set: true }, ASTERISK_ARI_PASSWORD: { set: true } } };
            if (url === '/api/config/env/status') {
                return { data: { apply_plan: [], pending_restart: false } };
            }
            if (url === '/api/config/yaml') {
                return {
                    data: {
                        providers: {
                            google_live: {
                                type: 'google_live',
                                api_key: '${GOOGLE_API_KEY}',
                                use_vertex_ai: true,
                            },
                        },
                    },
                };
            }
            if (url === '/api/config/providers/google_live/credentials') {
                return {
                    data: {
                        type: 'google_live',
                        credentials: {
                            'api-key': {
                                uploaded: false,
                                configured: false,
                                source: 'env_var',
                                env_var: 'GOOGLE_API_KEY',
                            },
                            'vertex-json': {
                                uploaded: false,
                                configured: true,
                                source: 'legacy_shared_file',
                                path: '/app/project/secrets/gcp-service-account.json',
                            },
                        },
                    },
                };
            }
            throw new Error(`Unexpected GET ${url}`);
        });
    });

    it('omits all saved and unset secrets on save and sends only an explicit replacement', async () => {
        vi.mocked(axios.post).mockResolvedValue({ data: {} });
        render(<MemoryRouter><EnvPage /></MemoryRouter>);
        const save = await screen.findByRole('button', { name: 'Save Changes' });
        fireEvent.click(save);
        await waitFor(() => expect(axios.post).toHaveBeenCalled());
        const body = vi.mocked(axios.post).mock.calls[0][1];
        for (const key of ['OPENAI_API_KEY', 'CUSTOM_SUPPLIER_KEY', 'SMTP_PASSWORD', 'ASTERISK_ARI_PASSWORD', 'AAVA_OUTBOUND_PBX_TYPE']) {
            expect(body).not.toHaveProperty(key);
        }
        fireEvent.click(screen.getByRole('button', { name: 'Replace OpenAI API Key' }));
        fireEvent.change(screen.getByLabelText('OpenAI API Key'), { target: { value: 'synthetic-new' } });
        fireEvent.click(save);
        await waitFor(() => expect(axios.post).toHaveBeenLastCalledWith('/api/config/env', expect.objectContaining({ OPENAI_API_KEY: 'synthetic-new' }), expect.anything()));
        await screen.findByRole('button', { name: 'Replace OpenAI API Key' });
        fireEvent.click(save);
        await waitFor(() => expect(vi.mocked(axios.post).mock.calls).toHaveLength(3));
        expect(vi.mocked(axios.post).mock.calls[2][1]).not.toHaveProperty('OPENAI_API_KEY');
    });

    it('offers Replace for server-assigned credential aliases, including known setting names', async () => {
        render(<MemoryRouter><EnvPage /></MemoryRouter>);
        fireEvent.click(await screen.findByRole('button', { name: 'System' }));
        expect(screen.getByRole('button', { name: 'Replace AAVA_OUTBOUND_PBX_TYPE' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Replace CUSTOM_SUPPLIER_KEY' })).toBeInTheDocument();
        window.history.replaceState(null, '', '/env');
    });

    it('tests saved ARI and SMTP credentials without serializing their markers', async () => {
        vi.mocked(axios.post).mockResolvedValue({ data: { success: true } });
        render(<MemoryRouter><EnvPage /></MemoryRouter>);
        fireEvent.click(await screen.findByRole('button', { name: 'Test Connection' }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledWith('/api/system/test-ari', expect.anything(), expect.anything()));
        expect(vi.mocked(axios.post).mock.calls[0][1]).not.toHaveProperty('password');
        fireEvent.change(screen.getByLabelText('Send Test Email To'), { target: { value: 'test@example.invalid' } });
        fireEvent.click(screen.getByRole('button', { name: 'Send Test Email' }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledWith('/api/config/env/smtp/test', expect.anything(), expect.anything()));
        expect(vi.mocked(axios.post).mock.calls[1][1]).not.toHaveProperty('smtp_password');
    });

    it('uses effective backend readiness instead of unresolved YAML references', async () => {
        render(
            <MemoryRouter>
                <EnvPage />
            </MemoryRouter>
        );

        expect(await screen.findByText('Legacy shared file')).toBeInTheDocument();
        expect(screen.getByText('not configured')).toBeInTheDocument();
        expect(screen.queryByText('env var GOOGLE_API_KEY')).not.toBeInTheDocument();
        expect(
            screen.getByText('— /app/project/secrets/gcp-service-account.json')
        ).toBeInTheDocument();
    });
});
