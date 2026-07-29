// @vitest-environment jsdom
import { renderHook, waitFor, act } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { useRestartRequired } from './useRestartRequired';

vi.mock('axios');

const mockedGet = vi.mocked(axios.get);

const configState = (restart_required: boolean) => ({
    data: {
        running_config_hash: 'a',
        disk_config_hash: restart_required ? 'b' : 'a',
        restart_required,
        disk_config_valid: true,
        engine_reachable: true,
    },
});

const hotReloadState = {
    data: {
        running_config_hash: 'a',
        disk_config_hash: 'b',
        apply_required: true,
        restart_required: false,
        recommended_apply_method: 'hot_reload' as const,
        disk_config_valid: true,
        engine_reachable: true,
    },
};

describe('useRestartRequired', () => {
    afterEach(() => {
        vi.useRealTimers();
        vi.restoreAllMocks();
    });

    it('fetches config-state on mount and reflects restart_required', async () => {
        mockedGet.mockResolvedValue(configState(true));

        const { result, unmount } = renderHook(() => useRestartRequired());

        await waitFor(() => expect(result.current.restartRequired).toBe(true));
        expect(mockedGet).toHaveBeenCalledWith('/api/system/config-state');

        unmount();
    });

    it('reports false when restart_required is false', async () => {
        mockedGet.mockResolvedValue(configState(false));

        const { result, unmount } = renderHook(() => useRestartRequired());

        await waitFor(() => expect(result.current.loading).toBe(false));
        expect(result.current.restartRequired).toBe(false);

        unmount();
    });

    it('distinguishes a hot-reload apply from a restart', async () => {
        mockedGet.mockResolvedValue(hotReloadState);

        const { result, unmount } = renderHook(() => useRestartRequired());

        await waitFor(() => expect(result.current.loading).toBe(false));
        expect(result.current.applyRequired).toBe(true);
        expect(result.current.restartRequired).toBe(false);
        expect(result.current.recommendedApplyMethod).toBe('hot_reload');
        expect(result.current.stateStale).toBe(false);

        unmount();
    });

    it('refetch re-fetches the config-state', async () => {
        mockedGet.mockResolvedValueOnce(configState(false));

        const { result, unmount } = renderHook(() => useRestartRequired());

        await waitFor(() => expect(result.current.restartRequired).toBe(false));

        mockedGet.mockResolvedValueOnce(configState(true));
        await act(async () => {
            await result.current.refetch();
        });

        expect(result.current.restartRequired).toBe(true);

        unmount();
    });

    it('keeps conservative defaults but reports stale state on an initial error', async () => {
        mockedGet.mockRejectedValue(new Error('network down'));

        const { result, unmount } = renderHook(() => useRestartRequired());

        await waitFor(() => expect(result.current.loading).toBe(false));
        expect(result.current.restartRequired).toBe(false);
        expect(result.current.applyRequired).toBe(false);
        expect(result.current.stateStale).toBe(true);

        unmount();
    });

    it('preserves the last-known apply action when a later poll fails', async () => {
        mockedGet.mockResolvedValueOnce(hotReloadState);

        const { result, unmount } = renderHook(() => useRestartRequired());

        await waitFor(() => expect(result.current.loading).toBe(false));
        mockedGet.mockRejectedValueOnce(new Error('temporary outage'));
        await act(async () => {
            await result.current.refetch();
        });

        expect(result.current.applyRequired).toBe(true);
        expect(result.current.restartRequired).toBe(false);
        expect(result.current.recommendedApplyMethod).toBe('hot_reload');
        expect(result.current.stateStale).toBe(true);

        unmount();
    });
});
