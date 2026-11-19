// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import axios from 'axios';
import ToolsPage from './ToolsPage';

vi.mock('axios');
vi.mock('../auth/AuthContext', () => ({ useAuth: () => ({ token: 'synthetic-token' }) }));
vi.mock('../hooks/useConfirmDialog', () => ({ useConfirmDialog: () => ({ confirm: vi.fn() }) }));
vi.mock('../utils/configCache', () => ({
    getCachedConfig: () => null,
    loadConfigYaml: async () => ({ config: { tools: { hangup_call: { enabled: true, farewell_message: 'Goodbye.' } } }, yamlError: null }),
}));
vi.mock('@monaco-editor/react', () => ({ default: () => null }));
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

it('keeps the verified pending Apply action through a subsequent unreachable poll with the real status hook', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    const active = {
        running_config_hash: 'synthetic-a', disk_config_hash: 'synthetic-a',
        restart_required: false, apply_required: false, recommended_apply_method: 'none',
        disk_config_valid: true, engine_reachable: true,
    };
    let reads = 0;
    vi.mocked(axios.get).mockImplementation(async url => {
        if (url !== '/api/system/config-state') return { data: { tools: [], providers: [] } };
        reads++;
        if (reads === 1) return { data: active };
        if (reads === 2) return { data: { ...active, disk_config_hash: 'synthetic-b', apply_required: true, recommended_apply_method: 'hot_reload' } };
        return { data: { ...active, running_config_hash: null, disk_config_hash: null, engine_reachable: false } };
    });
    vi.mocked(axios.post).mockImplementation(async url => {
        if (url === '/api/config/yaml') return { data: { recommended_apply_method: 'hot_reload' } };
        throw new Error('Synthetic reload response lost');
    });
    render(<ToolsPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Configure Hangup' }));
    fireEvent.change(screen.getByLabelText('Default Farewell Message'), { target: { value: 'See you soon.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save & Apply' }));
    await screen.findByText('Current saved configuration still needs Apply.');
    expect(reads).toBe(2);
    expect(screen.getByRole('button', { name: 'Apply Changes' })).toBeEnabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
    expect(reads).toBe(3);
    expect(screen.getByRole('button', { name: 'Apply Changes' })).toBeEnabled();
    expect(screen.getByText(/Status refresh failed; showing the last known action/)).toBeVisible();
    expect(axios.post).toHaveBeenCalledTimes(2);
});
