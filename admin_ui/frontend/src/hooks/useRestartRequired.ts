import { useCallback, useEffect, useRef, useState } from 'react';
import axios, { type AxiosRequestConfig } from 'axios';

export interface ConfigState {
    running_config_hash: string | null;
    disk_config_hash: string | null;
    restart_required: boolean;
    apply_required?: boolean;
    recommended_apply_method?: 'none' | 'hot_reload' | 'restart';
    disk_config_valid: boolean;
    engine_reachable: boolean;
}

const CONFIG_STATE_URL = '/api/system/config-state';
const POLL_INTERVAL_MS = 15000;

export function useRestartRequired(): {
    restartRequired: boolean;
    applyRequired: boolean;
    recommendedApplyMethod: 'none' | 'hot_reload' | 'restart';
    stateStale: boolean;
    refetch: () => Promise<void>;
    refreshAndRead: (options?: AxiosRequestConfig) => Promise<ConfigState | null>;
    loading: boolean;
} {
    const [restartRequired, setRestartRequired] = useState(false);
    const [applyRequired, setApplyRequired] = useState(false);
    const [recommendedApplyMethod, setRecommendedApplyMethod] = useState<'none' | 'hot_reload' | 'restart'>('none');
    const [stateStale, setStateStale] = useState(false);
    const [loading, setLoading] = useState(true);
    // Latest-response-wins: a slower older request (e.g. the 15s poll) must not
    // overwrite a newer one (e.g. a post-save/restart refetch) with stale data.
    const requestSeq = useRef(0);
    const invalidateRequests = useCallback(() => { requestSeq.current++; }, []);

    const refreshAndRead = useCallback(async (options?: AxiosRequestConfig): Promise<ConfigState | null> => {
        const seq = ++requestSeq.current;
        try {
            const res = options
                ? await axios.get<ConfigState>(CONFIG_STATE_URL, options)
                : await axios.get<ConfigState>(CONFIG_STATE_URL);
            if (options?.signal?.aborted || seq !== requestSeq.current) return null;
            if (res.data?.engine_reachable !== true || res.data.disk_config_valid !== true) {
                throw new Error('Engine configuration state unavailable');
            }
            if (seq === requestSeq.current) {
                const needsRestart = res.data?.restart_required === true;
                const needsApply = res.data?.apply_required === true;
                setRestartRequired(needsRestart);
                setApplyRequired(needsApply);
                setRecommendedApplyMethod(
                    needsRestart
                        ? 'restart'
                        : (res.data?.recommended_apply_method || (needsApply ? 'hot_reload' : 'none'))
                );
                setStateStale(false);
                return res.data;
            }
        } catch {
            // Keep the last known action visible during transient failures.
            // On an initial failure the conservative defaults remain false.
            if (seq === requestSeq.current) {
                setStateStale(true);
            }
        } finally {
            if (seq === requestSeq.current) {
                setLoading(false);
            }
        }
        return null;
    }, []);
    const refetch = useCallback(async () => { await refreshAndRead(); }, [refreshAndRead]);

    useEffect(() => {
        let cancelled = false;
        const tick = async () => {
            await refetch();
        };
        tick();
        const interval = setInterval(() => {
            if (!cancelled) tick();
        }, POLL_INTERVAL_MS);
        return () => {
            cancelled = true;
            invalidateRequests();
            clearInterval(interval);
        };
    }, [refetch, invalidateRequests]);

    return { restartRequired, applyRequired, recommendedApplyMethod, stateStale, refetch, refreshAndRead, loading };
}
