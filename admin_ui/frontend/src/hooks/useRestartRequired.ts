import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';

interface ConfigState {
    running_config_hash: string | null;
    disk_config_hash: string | null;
    restart_required: boolean;
    disk_config_valid: boolean;
    engine_reachable: boolean;
}

const CONFIG_STATE_URL = '/api/system/config-state';
const POLL_INTERVAL_MS = 15000;

export function useRestartRequired(): {
    restartRequired: boolean;
    refetch: () => Promise<void>;
    loading: boolean;
} {
    const [restartRequired, setRestartRequired] = useState(false);
    const [loading, setLoading] = useState(true);
    // Latest-response-wins: a slower older request (e.g. the 15s poll) must not
    // overwrite a newer one (e.g. a post-save/restart refetch) with stale data.
    const requestSeq = useRef(0);

    const refetch = useCallback(async () => {
        const seq = ++requestSeq.current;
        try {
            const res = await axios.get<ConfigState>(CONFIG_STATE_URL);
            if (seq === requestSeq.current) {
                setRestartRequired(res.data?.restart_required === true);
            }
        } catch {
            // Never false-alarm: treat any error as "no restart required".
            if (seq === requestSeq.current) {
                setRestartRequired(false);
            }
        } finally {
            if (seq === requestSeq.current) {
                setLoading(false);
            }
        }
    }, []);

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
            clearInterval(interval);
        };
    }, [refetch]);

    return { restartRequired, refetch, loading };
}
