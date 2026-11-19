// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import yaml from 'js-yaml';
import StreamingPage from './StreamingPage';

type TestConfig = {
    audio_transport: string;
    downstream_mode: string;
    streaming: Record<string, unknown>;
    providers: Record<string, unknown>;
    tools: Record<string, unknown>;
};
const state = vi.hoisted(() => ({
    disk: {} as TestConfig,
    restart: false,
    post: vi.fn(),
    refetch: vi.fn(),
}));
vi.mock('axios', () => ({ default: { post: state.post } }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() } }));
vi.mock('../../hooks/useRestartRequired', () => ({
    useRestartRequired: () => ({ restartRequired: state.restart, refetch: state.refetch }),
}));
vi.mock('../../utils/configCache', () => ({
    getCachedConfig: () => ({ config: structuredClone(state.disk), yamlError: null }),
    loadConfigYaml: vi.fn(async () => ({ config: structuredClone(state.disk), yamlError: null })),
}));

const delay = () => screen.getByLabelText('Greeting Media-Ready Delay (ms)');
const timeout = () => screen.getByLabelText('Greeting Media-Ready Timeout (ms)');
const save = () => screen.getByRole('button', { name: 'Save Changes' });
async function open() {
    const result = render(<StreamingPage />);
    // Settle the initial cache load before editing.
    await waitFor(() => expect(delay()).toBeInTheDocument());
    await Promise.resolve();
    return result;
}

beforeEach(() => {
    vi.clearAllMocks();
    state.restart = false;
    state.disk = {
        audio_transport: 'audiosocket', downstream_mode: 'stream',
        streaming: { greeting_min_start_ms: 0, greeting_rtp_wait_ms: 333, custom_yaml_setting: 'preserve' },
        providers: { named: { type: 'local', enabled: true, custom_field: 7 } },
        tools: { untouched: { enabled: true } },
    };
    state.post.mockImplementation(async (url: string, payload?: { content: string }) => {
        if (url === '/api/config/yaml') {
            state.disk = yaml.load(payload!.content) as TestConfig;
            state.restart = true;
            return { data: { recommended_apply_method: 'restart' } };
        }
        return { data: { status: 'success' } };
    });
});
afterEach(cleanup);

describe('initial greeting media readiness', () => {
    it('displays backend defaults without inserting absent YAML keys on unrelated save', async () => {
        await open();
        expect(delay()).toHaveValue(0);
        expect(timeout()).toHaveValue(1000);
        fireEvent.change(screen.getByLabelText('Chunk Size (ms)'), { target: { value: '40' } });
        fireEvent.click(save());
        await waitFor(() => expect(state.post).toHaveBeenCalledOnce());
        expect(state.disk.streaming).not.toHaveProperty('greeting_media_ready_delay_ms');
        expect(state.disk.streaming).not.toHaveProperty('greeting_media_ready_timeout_ms');
        expect(state.disk.streaming.greeting_min_start_ms).toBe(0);
        expect(state.disk.streaming.greeting_rtp_wait_ms).toBe(333);
        expect(state.disk.streaming.custom_yaml_setting).toBe('preserve');
    });

    it.each([[0, 0], [40, 1000], [2000, 5000]])('saves and reopens delay %i and timeout %i without changing other settings', async (d, t) => {
        state.disk.streaming.greeting_media_ready_delay_ms = 10;
        state.disk.streaming.greeting_media_ready_timeout_ms = 20;
        const oldProviders = structuredClone(state.disk.providers);
        const view = await open();
        fireEvent.change(delay(), { target: { value: String(d) } });
        fireEvent.change(timeout(), { target: { value: String(t) } });
        fireEvent.click(save());
        await waitFor(() => expect(state.post).toHaveBeenCalledOnce());
        expect(state.disk.streaming.greeting_media_ready_delay_ms).toBe(d);
        expect(state.disk.streaming.greeting_media_ready_timeout_ms).toBe(t);
        expect(state.disk.providers).toEqual(oldProviders);
        expect(state.post.mock.calls[0][0]).toBe('/api/config/yaml'); // save never restarts
        view.unmount();
        await open();
        expect(delay()).toHaveValue(d);
        expect(timeout()).toHaveValue(t);
        expect(screen.getByText(/require an AI Engine restart/)).toBeInTheDocument();
        fireEvent.change(screen.getByLabelText('Chunk Size (ms)'), { target: { value: '30' } });
        fireEvent.click(save());
        await waitFor(() => expect(state.post).toHaveBeenCalledTimes(2));
        expect(state.disk.streaming.greeting_media_ready_delay_ms).toBe(d);
        expect(state.disk.streaming.greeting_media_ready_timeout_ms).toBe(t);
    });

    it.each(['', '-1', '2001', '1.5'])('rejects invalid delay %j without truncation or a save', async raw => {
        await open();
        fireEvent.change(delay(), { target: { value: raw } });
        expect(delay()).toHaveAttribute('aria-invalid', 'true');
        expect(screen.getByText('Enter a whole number from 0 to 2000 ms.')).toBeInTheDocument();
        expect(save()).toBeDisabled();
        fireEvent.click(save());
        expect(state.post).not.toHaveBeenCalled();
    });

    it('rejects a timeout beyond the backend limit and permits recovery', async () => {
        await open();
        fireEvent.change(timeout(), { target: { value: '5001' } });
        expect(save()).toBeDisabled();
        fireEvent.change(timeout(), { target: { value: '0' } });
        expect(save()).toBeEnabled();
        expect(timeout()).toHaveValue(0);
    });

    it('preserves YAML-created zero values', async () => {
        state.disk.streaming.greeting_media_ready_delay_ms = 0;
        state.disk.streaming.greeting_media_ready_timeout_ms = 0;
        await open();
        expect(delay()).toHaveValue(0);
        expect(timeout()).toHaveValue(0);
    });

    it('does not force a restart when active calls block application', async () => {
        state.restart = true;
        state.post.mockResolvedValue({ data: { status: 'warning', message: '1 active call' } });
        const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
        await open();
        fireEvent.click(screen.getByRole('button', { name: 'Restart AI Engine' }));
        await waitFor(() => expect(state.post).toHaveBeenCalledOnce());
        expect(state.post).toHaveBeenCalledWith('/api/system/containers/ai_engine/restart?force=false');
        expect(confirm).not.toHaveBeenCalled();
        confirm.mockRestore();
    });
});
