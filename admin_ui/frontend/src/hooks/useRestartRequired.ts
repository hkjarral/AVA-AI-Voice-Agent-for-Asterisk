import { useCallback, useEffect, useState } from 'react';
import axios from 'axios';

interface ConfigState {
    running_config_hash: string;
    disk_config_hash: string;
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

    const refetch = useCallback(async () => {
        try {
            const res = await axios.get<ConfigState>(CONFIG_STATE_URL);
            setRestartRequired(res.data?.restart_required === true);
        } catch {
            // Never false-alarm: treat any error as "no restart required".
            setRestartRequired(false);
        } finally {
            setLoading(false);
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
