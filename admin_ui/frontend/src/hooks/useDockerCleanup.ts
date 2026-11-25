import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';

export interface CleanupJob {
    job_id: string;
    status: string;
    elapsed_seconds: number;
    timeout_seconds: number;
    error: string | null;
    steps: { resource: string; status: string; reclaimed: string | null; error: string | null }[];
}

export function useDockerCleanup(onTerminal: () => void) {
    const [job, setJob] = useState<CleanupJob | null>(null);
    const [checking, setChecking] = useState(true);
    const [starting, setStarting] = useState(false);
    const [pollError, setPollError] = useState<string | null>(null);
    const [submissionError, setSubmissionError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const previousRunning = useRef<string | null>(null);
    const controller = useRef<AbortController | null>(null);
    const generation = useRef(0);
    const startingRef = useRef(false);
    const terminalRef = useRef<string | null>(null);
    const terminalCallback = useRef(onTerminal);
    terminalCallback.current = onTerminal;

    const accept = useCallback((next: CleanupJob | null) => {
        let refreshUsage = false;
        const previous = previousRunning.current;
        if (previous && previous !== next?.job_id && terminalRef.current !== previous) {
            terminalRef.current = previous;
            refreshUsage = true;
        }
        previousRunning.current = next?.status === 'running' ? next.job_id : null;
        setJob(next);
        setChecking(false);
        setPollError(null);
        if (next && next.status !== 'running' && terminalRef.current !== next.job_id) {
            terminalRef.current = next.job_id;
            refreshUsage = true;
        }
        if (refreshUsage) terminalCallback.current();
    }, []);

    useEffect(() => {
        const abort = new AbortController();
        controller.current = abort;
        let timer: ReturnType<typeof setTimeout>;
        const poll = async () => {
            const version = generation.current;
            try {
                if (startingRef.current) return;
                const response = await axios.get('/api/system/docker/prune', { signal: abort.signal, timeout: 10000 });
                if (!abort.signal.aborted && version === generation.current) accept(response.data.job);
            } catch {
                if (!abort.signal.aborted && version === generation.current) {
                    setChecking(false);
                    setPollError('Cleanup status unavailable. Reconnecting...');
                }
            } finally {
                if (!abort.signal.aborted) timer = setTimeout(poll, 2000);
            }
        };
        void poll();
        return () => {
            abort.abort();
            clearTimeout(timer);
        };
    }, [accept]);

    const start = async (type: 'build_cache' | 'images') => {
        if (startingRef.current) return;
        const abort = controller.current;
        startingRef.current = true;
        setStarting(true);
        setNotice(null);
        setSubmissionError(null);
        generation.current += 1;
        try {
            const response = await axios.post('/api/system/docker/prune', {
                prune_build_cache: type === 'build_cache',
                prune_images: type === 'images',
                prune_containers: false,
                prune_volumes: false,
            }, { signal: abort?.signal, timeout: 10000 });
            if (!abort?.signal.aborted) {
                const active = response.data.job as CleanupJob;
                accept(active);
                if (!active.steps.some(step => step.resource === type)) {
                    const label = type === 'images' ? 'Unused images' : 'Build cache';
                    setNotice(`${label} cleanup was not queued. Another cleanup is running. Retry when it finishes.`);
                }
            }
        } catch (err) {
            if (!abort?.signal.aborted) {
                const detail = axios.isAxiosError(err) ? err.response?.data?.detail : null;
                setSubmissionError(typeof detail === 'string' ? detail : 'Cleanup request could not be confirmed. Check status before retrying.');
                terminalCallback.current();
            }
        } finally {
            startingRef.current = false;
            if (!abort?.signal.aborted) setStarting(false);
        }
    };

    return { job, error: [submissionError, pollError].filter(Boolean).join(' ') || null, notice, busy: checking || starting || !!pollError || job?.status === 'running', start };
}
