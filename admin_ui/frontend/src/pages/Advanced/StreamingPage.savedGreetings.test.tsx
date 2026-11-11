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
    get: vi.fn(),
    refetch: vi.fn(),
}));
vi.mock('axios', () => ({ default: { post: state.post, get: state.get } }));
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
    state.get.mockResolvedValue({ data: [{ provider: "named", is_active: 1 }] });
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

const minStart = () => screen.getByLabelText('Greeting Min Start (ms)');
const rtpWait = () => screen.getByLabelText('Greeting RTP Wait (ms)');

describe('saved greeting controls', () => {
    it.each([
        ['audiosocket', [{ provider: 'named' }], true, 'AudioSocket'],
        ['websocket', [{ provider: 'named' }], true, 'WebSocket'],
        ['externalmedia', [{ provider: 'named' }], false, 'Configured provider'],
        ['externalmedia', [{ provider: 'named', extra_json: '{"pipeline":"hybrid"}' }], true, 'pipeline greetings'],
        ['externalmedia', [{ provider: 'hybrid' }], true, 'pipeline greetings'],
        ['externalmedia', [{ provider: 'named' }, { provider: 'hybrid' }], false, 'Partial support'],
        ['externalmedia', [{}], false, 'Configured provider'],
        ['externalmedia', [null], true, 'could not be resolved'],
        ['externalmedia', [], true, 'No supported provider'],
        ['externalmedia', [{ provider: 'missing' }], true, 'could not be resolved'],
        ['externalmedia', [{ provider: 'named', is_active: 0 }], true, 'No supported provider'],
        ['externalmedia', [{ provider: 'named', extra_json: '{bad' }], true, 'could not be resolved'],
    ])('gates %s routes %j', async (transport, agents, readonly, help) => {
        state.disk.audio_transport = transport as string;
        Object.assign(state.disk, { pipelines: { hybrid: {} }, default_provider: 'named' });
        state.get.mockResolvedValue({ data: agents });
        await open();
        await waitFor(() => expect(state.get).toHaveBeenCalledWith('/api/agents'));
        await waitFor(() => expect(document.getElementById('greeting-rtp-support')).toHaveTextContent(help as string));
        expect(minStart()).toHaveAttribute('readonly');
        expect(rtpWait().hasAttribute('readonly')).toBe(readonly);
        expect(minStart()).toHaveValue(0);
        expect(rtpWait()).toHaveValue(333);
    });

    it.each([{}, { greeting_min_start_ms: 0 }, { greeting_rtp_wait_ms: 0 },
        { greeting_min_start_ms: 0, greeting_rtp_wait_ms: 0 },
        { greeting_min_start_ms: 83, greeting_rtp_wait_ms: 777 },
    ])('preserves saved values and absent keys through save and reopen: %j', async settings => {
        state.disk.streaming = { ...settings };
        let view = await open();
        expect(minStart()).toHaveValue(settings.greeting_min_start_ms ?? null);
        expect(rtpWait()).toHaveValue(settings.greeting_rtp_wait_ms ?? null);
        if (!('greeting_min_start_ms' in settings)) expect(document.getElementById('greeting-min-support')).toHaveTextContent('Unset — backend default: 0 ms');
        if (!('greeting_rtp_wait_ms' in settings)) expect(document.getElementById('greeting-rtp-support')).toHaveTextContent('Unset — backend default: 1000 ms');
        // Even a synthetic change event on a read-only control cannot rewrite state.
        fireEvent.change(minStart(), { target: { value: '999' } });
        fireEvent.change(rtpWait(), { target: { value: '999' } });
        fireEvent.change(screen.getByLabelText('Chunk Size (ms)'), { target: { value: '40' } });
        fireEvent.click(save());
        await waitFor(() => expect(state.post).toHaveBeenCalledOnce());
        expect(state.disk.streaming).toEqual({ ...settings, chunk_size_ms: 40 });
        view.unmount();
        view = await open();
        expect(minStart()).toHaveValue(settings.greeting_min_start_ms ?? null);
        expect(rtpWait()).toHaveValue(settings.greeting_rtp_wait_ms ?? null);
        fireEvent.click(save());
        await waitFor(() => expect(state.post).toHaveBeenCalledTimes(2));
        expect(state.disk.streaming).toEqual({ ...settings, chunk_size_ms: 40 });
        expect(state.post.mock.calls.every(([url]) => url === '/api/config/yaml')).toBe(true);
        view.unmount();
    });

    it.each([0, 789, ''])('allows supported edits and clearing without changing Min Start: %s', async value => {
        state.disk.audio_transport = 'externalmedia';
        await open();
        await waitFor(() => expect(rtpWait()).not.toHaveAttribute('readonly'));
        fireEvent.change(rtpWait(), { target: { value: String(value) } });
        fireEvent.click(save());
        await waitFor(() => expect(state.post).toHaveBeenCalledOnce());
        expect(state.disk.streaming.greeting_min_start_ms).toBe(0);
        if (value === '') expect(state.disk.streaming).not.toHaveProperty('greeting_rtp_wait_ms');
        else expect(state.disk.streaming.greeting_rtp_wait_ms).toBe(value);
    });

    it('keeps unavailable/loading routes read-only and exposes keyboard help', async () => {
        state.disk.audio_transport = 'externalmedia';
        state.get.mockRejectedValue(new Error('unavailable'));
        await open();
        expect(rtpWait()).toHaveAttribute('readonly');
        expect(rtpWait()).toHaveAccessibleDescription(/loading or unavailable/);
        expect(minStart()).toHaveAccessibleDescription(/not currently applied/);
        minStart().focus();
        expect(minStart()).toHaveFocus();
        fireEvent.focus(screen.getByRole('button', { name: 'Help for Greeting Min Start (ms)' }));
        expect(await screen.findByRole('tooltip')).toHaveTextContent('does not currently apply');
    });
});
