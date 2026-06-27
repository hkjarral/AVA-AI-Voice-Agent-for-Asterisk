import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../auth/AuthContext';

export interface LiveStatusComponent {
    state: 'ready' | 'degraded' | 'error' | 'unreachable' | 'unknown' | string;
    freshness: 'fresh' | 'stale' | 'expired' | string;
    summary: string;
    source: string;
    updated_at: string;
    age_seconds?: number;
    details: Record<string, any>;
    metrics: Record<string, any>;
    warnings: string[];
    errors: string[];
}

export interface LiveStatusSnapshot {
    version: number;
    event_id: number;
    generated_at: string;
    summary: {
        state: 'ready' | 'degraded' | 'error' | 'unknown' | string;
        component_count: number;
    };
    components: Record<string, LiveStatusComponent>;
    ai_engine?: LiveStatusComponent;
    local_ai_server?: LiveStatusComponent;
    sessions?: LiveStatusComponent;
    directories?: LiveStatusComponent;
    platform?: LiveStatusComponent;
    asterisk?: LiveStatusComponent;
    metrics?: LiveStatusComponent;
}

export interface LiveStatusState {
    snapshot: LiveStatusSnapshot | null;
    loading: boolean;
    connected: boolean;
    mode: 'idle' | 'stream' | 'poll';
    error: string | null;
}

interface SseMessage {
    id?: string;
    event: string;
    data: string;
}

const SNAPSHOT_URL = '/api/system/live-status';
const STREAM_URL = '/api/system/live-status/stream';

const authHeaders = (token: string | null): HeadersInit =>
    token ? { Authorization: `Bearer ${token}` } : {};

export const parseSseMessages = (buffer: string): { messages: SseMessage[]; rest: string } => {
    const frames = buffer.split(/\r?\n\r?\n/);
    const rest = frames.pop() ?? '';
    const messages = frames
        .map(frame => {
            const message: SseMessage = { event: 'message', data: '' };
            const dataLines: string[] = [];
            for (const rawLine of frame.split(/\r?\n/)) {
                if (!rawLine || rawLine.startsWith(':')) continue;
                const colon = rawLine.indexOf(':');
                const field = colon >= 0 ? rawLine.slice(0, colon) : rawLine;
                const rawValue = colon >= 0 ? rawLine.slice(colon + 1) : '';
                const value = rawValue.startsWith(' ') ? rawValue.slice(1) : rawValue;
                if (field === 'id') message.id = value;
                if (field === 'event') message.event = value || 'message';
                if (field === 'data') dataLines.push(value);
            }
            message.data = dataLines.join('\n');
            return message.data ? message : null;
        })
        .filter((message): message is SseMessage => message != null);
    return { messages, rest };
};

export const useLiveStatus = (pollIntervalMs = 5000): LiveStatusState => {
    const { token } = useAuth();
    const [snapshot, setSnapshot] = useState<LiveStatusSnapshot | null>(null);
    const [loading, setLoading] = useState(true);
    const [connected, setConnected] = useState(false);
    const [mode, setMode] = useState<LiveStatusState['mode']>('idle');
    const [error, setError] = useState<string | null>(null);

    const fetchSnapshot = useCallback(async (signal: AbortSignal): Promise<LiveStatusSnapshot> => {
        const response = await fetch(SNAPSHOT_URL, {
            headers: authHeaders(token),
            signal,
        });
        if (!response.ok) {
            throw new Error(`live-status snapshot failed: HTTP ${response.status}`);
        }
        return response.json();
    }, [token]);

    useEffect(() => {
        const controller = new AbortController();
        let pollTimer: ReturnType<typeof setTimeout> | null = null;
        let stopped = false;
        let streamReader: ReadableStreamDefaultReader<Uint8Array> | null = null;

        const applySnapshot = (next: LiveStatusSnapshot) => {
            setSnapshot(next);
            setError(null);
            setLoading(false);
        };

        const clearPollTimer = () => {
            if (pollTimer) {
                clearTimeout(pollTimer);
                pollTimer = null;
            }
        };

        const schedulePoll = () => {
            clearPollTimer();
            if (stopped) return;
            pollTimer = setTimeout(pollOnce, pollIntervalMs);
        };

        const openStream = async () => {
            const stream = await fetch(STREAM_URL, {
                headers: authHeaders(token),
                signal: controller.signal,
            });
            if (!stream.ok || !stream.body) {
                throw new Error(`live-status stream failed: HTTP ${stream.status}`);
            }

            setMode('stream');
            setConnected(true);
            const reader = stream.body.getReader();
            streamReader = reader;
            const decoder = new TextDecoder();
            let buffer = '';

            while (!stopped) {
                const { value, done } = await reader.read();
                if (done) {
                    throw new Error('live-status stream closed');
                }
                buffer += decoder.decode(value, { stream: true });
                const parsed = parseSseMessages(buffer);
                buffer = parsed.rest;
                for (const message of parsed.messages) {
                    if (message.event !== 'snapshot') continue;
                    applySnapshot(JSON.parse(message.data));
                }
            }
        };

        const pollOnce = async () => {
            if (stopped) return;
            setMode('poll');
            setConnected(false);
            try {
                applySnapshot(await fetchSnapshot(controller.signal));
                await openStream();
            } catch (err: any) {
                if (controller.signal.aborted || stopped) return;
                if (!controller.signal.aborted) {
                    setError(err?.message || 'live-status snapshot failed');
                    setLoading(false);
                }
                schedulePoll();
            }
        };

        const start = async () => {
            try {
                applySnapshot(await fetchSnapshot(controller.signal));
                await openStream();
            } catch (err: any) {
                if (controller.signal.aborted || stopped) return;
                setError(err?.message || 'live-status stream failed');
                setConnected(false);
                setMode('poll');
                schedulePoll();
            }
        };

        start();

        return () => {
            stopped = true;
            controller.abort();
            streamReader?.cancel().catch(() => {});
            clearPollTimer();
        };
    }, [fetchSnapshot, pollIntervalMs, token]);

    return { snapshot, loading, connected, mode, error };
};
