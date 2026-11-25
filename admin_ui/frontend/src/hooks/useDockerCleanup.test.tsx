// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import axios from 'axios';
import { useDockerCleanup } from './useDockerCleanup';
vi.mock('axios');
const running = { job_id: 'one', status: 'running', elapsed_seconds: 4, timeout_seconds: 900, error: null, steps: [] };

beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

it('reattaches after reload and refreshes disk usage once on a terminal outcome', async () => {
    vi.mocked(axios.get).mockResolvedValueOnce({ data: { job: running } })
        .mockResolvedValue({ data: { job: { ...running, status: 'timed_out' } } });
    const terminal = vi.fn();
    const hook = renderHook(() => useDockerCleanup(terminal));
    await act(async () => {});
    expect(hook.result.current.busy).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(hook.result.current.busy).toBe(false);
    expect(terminal).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(terminal).toHaveBeenCalledTimes(1);
    expect(axios.post).not.toHaveBeenCalled();
});

it('coalesces clicks and preserves image-only resource selection', async () => {
    vi.mocked(axios.get).mockResolvedValue({ data: { job: null } });
    let finish!: (response: unknown) => void;
    vi.mocked(axios.post).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const hook = renderHook(() => useDockerCleanup(vi.fn()));
    await act(async () => {});
    let pending!: Promise<void>;
    act(() => { pending = hook.result.current.start('images'); });
    await act(async () => { await hook.result.current.start('images'); });
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(vi.mocked(axios.post).mock.calls[0][1]).toEqual({
        prune_build_cache: false, prune_images: true, prune_containers: false, prune_volumes: false,
    });
    await act(async () => { finish({ data: { job: running } }); await pending; });
    expect(hook.result.current.job?.job_id).toBe('one');
});

it('settles network errors and recovers through polling', async () => {
    vi.mocked(axios.get).mockResolvedValue({ data: { job: null } });
    vi.mocked(axios.post).mockRejectedValue(new Error('network'));
    const terminal = vi.fn();
    const hook = renderHook(() => useDockerCleanup(terminal));
    await act(async () => {});
    await act(async () => { await hook.result.current.start('build_cache'); });
    expect(hook.result.current.error).toContain('could not be confirmed');
    expect(terminal).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(hook.result.current.busy).toBe(false);
    expect(hook.result.current.error).toBeNull();
});

it('aborts polling and clears its timer on unmount', async () => {
    vi.mocked(axios.get).mockResolvedValue({ data: { job: running } });
    const hook = renderHook(() => useDockerCleanup(vi.fn()));
    await act(async () => {});
    const signal = vi.mocked(axios.get).mock.calls[0][1]?.signal;
    hook.unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(axios.get).toHaveBeenCalledTimes(1);
});

it('refreshes usage when another client replaces a running job between polls', async () => {
    const second = { ...running, job_id: 'two' };
    vi.mocked(axios.get).mockResolvedValueOnce({ data: { job: running } })
        .mockResolvedValueOnce({ data: { job: second } })
        .mockResolvedValue({ data: { job: { ...second, status: 'completed' } } });
    const terminal = vi.fn();
    renderHook(() => useDockerCleanup(terminal));
    await act(async () => {});
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(terminal).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
    expect(terminal).toHaveBeenCalledTimes(2);
});

it('explains when an existing cleanup does not include the requested resource', async () => {
    vi.mocked(axios.get).mockResolvedValue({ data: { job: null } });
    vi.mocked(axios.post).mockResolvedValue({ data: { job: {
        ...running, steps: [{ resource: 'build_cache', status: 'running', reclaimed: null, error: null }],
    } } });
    const hook = renderHook(() => useDockerCleanup(vi.fn()));
    await act(async () => {});
    await act(async () => { await hook.result.current.start('images'); });
    expect(hook.result.current.notice).toContain('Unused images cleanup was not queued');
    expect(hook.result.current.job?.job_id).toBe('one');
    expect(hook.result.current.busy).toBe(true);
});
