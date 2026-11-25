// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import axios from 'axios';
import DockerPage from './DockerPage';

vi.mock('axios');
vi.mock('../../hooks/useConfirmDialog', () => ({
    useConfirmDialog: () => ({ confirm: vi.fn() }),
}));

const usage = Object.fromEntries(['images', 'containers', 'volumes', 'build_cache'].map(resource => [resource, {
    total: 6, active: 6, size: '1GB', reclaimable: '0B',
}]));

beforeEach(() => { vi.resetAllMocks(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('preserves the stale-usage warning until a current refresh succeeds', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    let diskCalls = 0;
    let finish!: (value: { data: typeof usage }) => void;
    vi.mocked(axios.get).mockImplementation(async url => {
        if (url === '/api/system/docker/disk-usage') {
            diskCalls += 1;
            if (diskCalls === 1) return { data: usage };
            if (diskCalls === 2) throw new Error('fixture unavailable');
            return new Promise(resolve => { finish = resolve; });
        }
        return { data: url === '/api/system/docker/prune' ? { job: null } : [] };
    });
    render(<DockerPage />);
    await screen.findAllByText('1GB');
    const refresh = screen.getAllByRole('button', { name: 'Refresh' })[1];
    fireEvent.click(refresh);
    const warning = await screen.findByText('Disk usage refresh failed. Displayed values may be outdated.');
    await waitFor(() => expect(refresh).toBeEnabled());
    fireEvent.click(refresh);
    await waitFor(() => expect(diskCalls).toBe(3));
    expect(warning).toBeVisible();
    await act(async () => { finish({ data: usage }); });
    await waitFor(() => expect(warning).not.toBeInTheDocument());
});

it('keeps cleanup actions available if initial usage cannot be measured', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(axios.get).mockImplementation(async url => {
        if (url === '/api/system/docker/disk-usage') throw new Error('fixture unavailable');
        return { data: url === '/api/system/docker/prune' ? { job: null } : [] };
    });
    render(<DockerPage />);
    await screen.findByText('Unable to load Docker disk usage');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Clean Unused Images' })).toBeEnabled());
    expect(screen.getByRole('button', { name: 'Clean Build Cache' })).toBeEnabled();
});
