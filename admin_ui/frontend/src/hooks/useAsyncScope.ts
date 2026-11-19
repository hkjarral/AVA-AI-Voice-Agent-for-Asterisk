import { useCallback, useLayoutEffect, useRef } from 'react';

/** Own a page's reads and timers without cancelling already submitted writes. */
export function useAsyncScope() {
    const mounted = useRef(false);
    const reads = useRef(new Map<string, AbortController>());
    const timeouts = useRef(new Set<number>());
    const intervals = useRef(new Set<number>());
    useLayoutEffect(() => {
        mounted.current = true;
        const activeReads = reads.current;
        const activeTimeouts = timeouts.current;
        const activeIntervals = intervals.current;
        return () => {
            mounted.current = false;
            activeReads.forEach(controller => controller.abort());
            activeReads.clear();
            activeTimeouts.forEach(id => window.clearTimeout(id));
            activeTimeouts.clear();
            activeIntervals.forEach(id => window.clearInterval(id));
            activeIntervals.clear();
        };
    }, []);
    const isMounted = useCallback(() => mounted.current, []);
    const cancelRead = useCallback((key: string) => {
        reads.current.get(key)?.abort();
        reads.current.delete(key);
    }, []);
    const beginRead = useCallback((key: string, skipIfPending = false) => {
        if (!mounted.current || (skipIfPending && reads.current.has(key))) return null;
        reads.current.get(key)?.abort();
        const controller = new AbortController();
        reads.current.set(key, controller);
        return {
            signal: controller.signal,
            isCurrent: () => mounted.current && !controller.signal.aborted && reads.current.get(key) === controller,
            finish: () => { if (reads.current.get(key) === controller) reads.current.delete(key); },
        };
    }, []);
    const later = useCallback((callback: () => unknown, delay: number) => {
        if (!mounted.current) return undefined;
        const id = window.setTimeout(() => {
            timeouts.current.delete(id);
            if (mounted.current) void callback();
        }, delay);
        timeouts.current.add(id);
        return id;
    }, []);
    const clearLater = useCallback((id?: number) => {
        if (id === undefined) return;
        window.clearTimeout(id);
        timeouts.current.delete(id);
    }, []);
    const every = useCallback((callback: () => void, delay: number) => {
        if (!mounted.current) return undefined;
        const id = window.setInterval(() => { if (mounted.current) callback(); }, delay);
        intervals.current.add(id);
        return id;
    }, []);
    const clearEvery = useCallback((id?: number) => {
        if (id === undefined) return;
        window.clearInterval(id);
        intervals.current.delete(id);
    }, []);
    return { isMounted, beginRead, cancelRead, later, clearLater, every, clearEvery };
}
