// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import GoogleLiveProviderForm from './GoogleLiveProviderForm';

vi.mock('axios');
vi.mock('../../../hooks/useConfirmDialog', () => ({
    useConfirmDialog: () => ({ confirm: vi.fn().mockResolvedValue(false) }),
}));

const credentialsUrl = '/api/config/providers/google_live/credentials';

describe('GoogleLiveProviderForm Vertex credentials', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it('patches the per-instance credentials path into the parent form after upload', async () => {
        vi.mocked(axios.get).mockImplementation(async url => {
            if (url === '/api/config/vertex-ai/regions') return { data: { regions: [] } };
            if (url === credentialsUrl) {
                return {
                    data: {
                        credentials: { 'vertex-json': { uploaded: false, configured: false } },
                    },
                };
            }
            throw new Error(`Unexpected GET ${url}`);
        });
        vi.mocked(axios.post).mockResolvedValue({
            data: {
                path: '/app/project/secrets/providers/google_live/vertex-service-account.json',
                project_id: 'vertex-project',
            },
        });
        const onChange = vi.fn();
        const { container } = render(
            <GoogleLiveProviderForm
                providerKey="google_live"
                config={{ use_vertex_ai: true, llm_model: 'gemini-live-2.5-flash-native-audio' }}
                onChange={onChange}
            />
        );

        await screen.findByText('Upload Service Account JSON');
        const input = container.querySelector('#vertex-json-upload') as HTMLInputElement;
        fireEvent.change(input, {
            target: {
                files: [new File(['{}'], 'service-account.json', { type: 'application/json' })],
            },
        });

        await waitFor(() =>
            expect(onChange).toHaveBeenCalledWith({
                credentials_path:
                    '/app/project/secrets/providers/google_live/vertex-service-account.json',
                vertex_project: 'vertex-project',
            })
        );
    });

    it('shows a legacy shared file as a fallback without offering to delete it', async () => {
        vi.mocked(axios.get).mockImplementation(async url => {
            if (url === '/api/config/vertex-ai/regions') return { data: { regions: [] } };
            if (url === credentialsUrl) {
                return {
                    data: {
                        credentials: {
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

        render(
            <GoogleLiveProviderForm
                providerKey="google_live"
                config={{ use_vertex_ai: true, llm_model: 'gemini-live-2.5-flash-native-audio' }}
                onChange={vi.fn()}
            />
        );

        expect(
            await screen.findByText(/Using the legacy shared service-account file/)
        ).toBeInTheDocument();
        expect(screen.getByText('Upload Per-Instance Override')).toBeInTheDocument();
        expect(screen.queryByTitle('Delete credentials')).not.toBeInTheDocument();
    });
});
