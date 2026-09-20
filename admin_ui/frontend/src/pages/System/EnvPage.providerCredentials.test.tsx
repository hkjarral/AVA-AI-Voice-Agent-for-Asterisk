// @vitest-environment jsdom
import { render, screen } from '@testing-library/react';
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
            if (url === '/api/config/env') return { data: {} };
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
