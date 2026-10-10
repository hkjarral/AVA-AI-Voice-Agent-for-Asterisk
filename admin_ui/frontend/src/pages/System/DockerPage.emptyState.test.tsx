// @vitest-environment jsdom
import { act, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import DockerPage from './DockerPage';

vi.mock('axios');
vi.mock('../../hooks/useConfirmDialog', () => ({
    useConfirmDialog: () => ({ confirm: vi.fn().mockResolvedValue(false) }),
}));

describe('DockerPage empty results', () => {
    beforeEach(() => {
        vi.mocked(axios.get).mockReset();
    });
    afterEach(() => {
        vi.restoreAllMocks();
    });

    it('shows the empty state only after container loading succeeds', async () => {
        let finishLoading = () => {};
        const pending = new Promise<void>(resolve => { finishLoading = resolve; });
        vi.mocked(axios.get).mockImplementation(async url => {
            if (url === '/api/system/containers') {
                await pending;
                return { data: [] };
            }
            return { data: null };
        });
        render(<DockerPage />);

        expect(screen.getByText('Loading container status...')).toBeInTheDocument();
        expect(screen.queryByRole('heading', { name: 'No containers found.' })).not.toBeInTheDocument();
        await act(async () => finishLoading());
        expect(await screen.findByRole('heading', { name: 'No containers found.', level: 4 })).toBeInTheDocument();
        expect(screen.queryByText('Loading container status...')).not.toBeInTheDocument();
    });

    it('keeps backend failure guidance separate from an empty result', async () => {
        vi.spyOn(console, 'error').mockImplementation(() => undefined);
        vi.mocked(axios.get).mockImplementation(async url => {
            if (url === '/api/system/containers') throw new Error('Docker is unavailable');
            return { data: null };
        });
        render(<DockerPage />);

        expect(await screen.findByText('Unable to load container status')).toBeInTheDocument();
        expect(screen.queryByRole('heading', { name: 'No containers found.' })).not.toBeInTheDocument();
        expect(screen.getByText('Verify Docker is running on the host and that admin_ui mounts the correct Docker socket.')).toBeInTheDocument();
    });
});
