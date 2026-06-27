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

    it('treats request errors as no restart required', async () => {
        mockedGet.mockRejectedValue(new Error('network down'));

        const { result, unmount } = renderHook(() => useRestartRequired());

        await waitFor(() => expect(result.current.loading).toBe(false));
        expect(result.current.restartRequired).toBe(false);

        unmount();
    });
});
