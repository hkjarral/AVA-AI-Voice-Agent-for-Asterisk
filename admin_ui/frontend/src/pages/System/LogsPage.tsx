import React, { useMemo, useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { RefreshCw, Pause, Play, Terminal, ArrowDown } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';
import { parseAnsi } from '../../utils/ansi';
import { describeApiError } from '../../utils/apiErrors';

type LogLevel = 'error' | 'warning' | 'info' | 'debug';
type LogCategory = 'call' | 'provider' | 'audio' | 'transport' | 'vad' | 'tools' | 'config';
type LogsMode = 'troubleshoot' | 'raw';
type TroubleshootView = 'overview' | 'issues' | 'provider' | 'media' | 'vad' | 'tools' | 'all';

type LogEvent = {
    ts: string | null;
    level: LogLevel;
    msg: string;
    component: string | null;
    call_id: string | null;
    provider: string | null;
    context: string | null;
    pipeline: string | null;
    category: LogCategory;
    milestone: boolean;
    meta?: Record<string, string>;
    raw: string;
};

type CallMeta = {
    call_id: string;
    caller_number: string | null;
    caller_name: string | null;
    start_time: string | null;
    end_time: string | null;
    duration_seconds: number;
    provider_name: string;
    pipeline_name: string | null;
    context_name: string | null;
    outcome: string;
    error_message: string | null;
    barge_in_count: number;
    avg_turn_latency_ms: number;
    total_turns: number;
};

type EventsResponse = {
    events: LogEvent[];
    call?: CallMeta | null;
    window?: { source: string; since: string | null; until: string | null } | null;
    related_ids?: string[];
    related_bridge_ids?: string[];
};

type CallRecordSummary = {
    id: string;
    call_id: string;
    caller_number: string | null;
    caller_name: string | null;
    start_time: string | null;
    end_time: string | null;
    duration_seconds: number;
    provider_name: string;
    pipeline_name: string | null;
    context_name: string | null;
    outcome: string;
    error_message: string | null;
    avg_turn_latency_ms: number;
    total_turns: number;
    barge_in_count: number;
};

type CallListResponse = {
    calls: CallRecordSummary[];
    total: number;
    page: number;
    page_size: number;
    total_pages: number;
};

type FilterOptions = {
    providers: string[];
    pipelines: string[];
    contexts: string[];
    outcomes: string[];
};

// Back-compat: old URLs used `preset=important|audio|provider|transport|vad|tools|config`
const mapLegacyPresetToView = (preset: string | null): TroubleshootView => {
    switch ((preset || '').toLowerCase()) {
        case 'important':
            return 'overview';
        case 'audio':
            return 'media';
        case 'provider':
            return 'provider';
        case 'transport':
            return 'media';
        case 'vad':
            return 'vad';
        case 'tools':
            return 'tools';
        case 'config':
            return 'issues';
        default:
            return 'overview';
    }
};

const LogsPage = () => {
    const [searchParams, setSearchParams] = useSearchParams();
    const [logs, setLogs] = useState('');
    const [events, setEvents] = useState<LogEvent[]>([]);
    const [eventsMeta, setEventsMeta] = useState<EventsResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [autoRefresh, setAutoRefresh] = useState(true);
    const [container, setContainer] = useState(searchParams.get('container') || 'ai_engine');
    const [mode, setMode] = useState<LogsMode>(() => {
        const rawMode = (searchParams.get('mode') || '').toLowerCase();
        // Back-compat: old URLs used mode=events
        if (rawMode === 'events') return 'troubleshoot';
        if (rawMode === 'troubleshoot') return 'troubleshoot';
        // Default is Raw for fast scanning; Troubleshoot is the call-centric flow.
        return 'raw';
    });
    const [view, setView] = useState<TroubleshootView>(() => {
        const v = (searchParams.get('view') || '').trim();
        if (v) return v as any;
        return mapLegacyPresetToView(searchParams.get('preset'));
    });
    const [callId, setCallId] = useState(searchParams.get('call_id') || '');
    const [q, setQ] = useState(searchParams.get('q') || '');
    const [rawLevels, setRawLevels] = useState<LogLevel[]>(() => {
        const v = (searchParams.get('raw_levels') || '').trim();
        if (!v) return ['error', 'warning'];
        return v.split(',').map(s => s.trim().toLowerCase() as LogLevel).filter(Boolean);
    });
    const [hidePayloads, setHidePayloads] = useState(searchParams.get('hide_payloads') !== 'false');
    const [since, setSince] = useState(searchParams.get('since') || '');
    const [until, setUntil] = useState(searchParams.get('until') || '');
    const [includeDebug, setIncludeDebug] = useState(() => {
        const v = searchParams.get('include_debug');
        if (v === null) return true;
        return v === 'true';
    });
    const [hideRepeats, setHideRepeats] = useState(searchParams.get('hide_repeats') !== 'false');
    const [showCallFinder, setShowCallFinder] = useState(!callId);
    const [callFilters, setCallFilters] = useState({
        caller_number: '',
        caller_name: '',
        provider_name: '',
        pipeline_name: '',
        context_name: '',
        outcome: '',
        start_date: '',
        end_date: '',
    });
    const [callFilterOptions, setCallFilterOptions] = useState<FilterOptions | null>(null);
    const [callResults, setCallResults] = useState<CallRecordSummary[]>([]);
    const [callPage, setCallPage] = useState(1);
    const [callTotalPages, setCallTotalPages] = useState(1);
    const [callLoading, setCallLoading] = useState(false);
    const logsEndRef = useRef<HTMLDivElement>(null);
    const scrollRef = useRef<HTMLDivElement>(null);
    const [isPinnedToBottom, setIsPinnedToBottom] = useState(true);

    const recomputePinned = useCallback(() => {
        const el = scrollRef.current;
        if (!el) return;
        const remaining = el.scrollHeight - el.scrollTop - el.clientHeight;
        setIsPinnedToBottom(remaining < 80);
    }, []);

    const updateUrlParams = (next: Record<string, string>) => {
        const merged: Record<string, string> = {};
        searchParams.forEach((v, k) => (merged[k] = v));
        Object.entries(next).forEach(([k, v]) => {
            if (!v) delete merged[k];
            else merged[k] = v;
        });
        setSearchParams(merged);
    };

    const fetchLogs = async () => {
        setLoading(true);
        try {
            const params: Record<string, any> = { tail: 500 };
            // Send as CSV for FastAPI list parsing (avoid axios `levels[]=...` serialization).
            if (rawLevels.length) params.levels = rawLevels.join(',');
            const res = await axios.get(`/api/logs/${container}`, { params });
            setLogs(res.data.logs);
        } catch (err: any) {
            const info = describeApiError(err, `/api/logs/${container}`);
            console.error("Failed to fetch logs", info);
            setLogs(
                `Failed to fetch logs for ${container}.\n` +
                `${info.status ? `HTTP ${info.status}` : info.kind}${info.detail ? ` - ${info.detail}` : ''}\n\n` +
                `Troubleshooting:\n` +
                `- Check: docker compose -p asterisk-ai-voice-agent logs --tail=200 admin_ui\n` +
                `- Check Docker socket access: ls -ln /var/run/docker.sock\n` +
                `- If you changed .env or ran preflight, recreate admin_ui: docker compose -p asterisk-ai-voice-agent up -d --force-recreate admin_ui\n`
            );
        } finally {
            setLoading(false);
        }
    };

    const fetchEvents = async () => {
        setLoading(true);
        try {
            const viewCategories = (() => {
                switch (view) {
                    case 'provider':
                        return ['provider'];
                    case 'media':
                        return ['audio', 'transport'];
                    case 'vad':
                        return ['vad'];
                    case 'tools':
                        return ['tools'];
                    default:
                        return [];
                }
            })();
            const params: Record<string, any> = {
                limit: includeDebug ? 2000 : 500,
                hide_payloads: hidePayloads,
            };
            if (callId.trim()) params.call_id = callId.trim();
            // Focused views should fetch focused categories server-side so mid-call events
            // aren't dropped by window/limit slicing.
            if (viewCategories.length) params.categories = viewCategories.join(',');
            // Reduce payload size unless user explicitly opts into debug.
            if (!includeDebug) params.levels = 'error,warning,info';
            if (since.trim()) params.since = since.trim();
            if (until.trim()) params.until = until.trim();

            const res = await axios.get<EventsResponse>(`/api/logs/${container}/events`, { params });
            setEvents(res.data.events || []);
            setEventsMeta(res.data || null);
        } catch (err: any) {
            const info = describeApiError(err, `/api/logs/${container}/events`);
            console.error("Failed to fetch events", info);
            setEvents([]);
            setEventsMeta(null);
            setLogs(
                `Failed to fetch log events for ${container}.\n` +
                `${info.status ? `HTTP ${info.status}` : info.kind}${info.detail ? ` - ${info.detail}` : ''}\n`
            );
        } finally {
            setLoading(false);
        }
    };

    const fetchCallFilterOptions = useCallback(async () => {
        try {
            const res = await axios.get<FilterOptions>('/api/calls/filters');
            setCallFilterOptions(res.data);
        } catch (err) {
            console.error('Failed to fetch call filter options', err);
        }
    }, []);

    const fetchCalls = useCallback(async () => {
        try {
            setCallLoading(true);
            const params: Record<string, any> = { page: callPage, page_size: 20 };
            Object.entries(callFilters).forEach(([k, v]) => {
                if (v) params[k] = v;
            });
            const res = await axios.get<CallListResponse>('/api/calls', { params });
            setCallResults(res.data.calls || []);
            setCallTotalPages(res.data.total_pages || 1);
        } catch (err) {
            console.error('Failed to fetch calls', err);
            setCallResults([]);
        } finally {
            setCallLoading(false);
        }
    }, [callFilters, callPage]);

    useEffect(() => {
        if (mode === 'troubleshoot') {
            fetchCallFilterOptions();
            if (showCallFinder) fetchCalls();
        }
    }, [mode, fetchCallFilterOptions, fetchCalls, showCallFinder]);

    useEffect(() => {
        // Keep call finder in sync with URL-provided call_id
        setShowCallFinder(!callId);
    }, [callId]);

    useEffect(() => {
        if (mode !== 'raw') return;
        fetchLogs();
        const interval = setInterval(() => {
            if (autoRefresh) fetchLogs();
        }, 3000);
        return () => clearInterval(interval);
    }, [autoRefresh, container, mode, rawLevels.join(',')]);

    useEffect(() => {
        if (mode !== 'troubleshoot') return;
        if (!callId) return;
        fetchEvents();
        const interval = setInterval(() => {
            if (!autoRefresh) return;
            if (!callId) return;
            fetchEvents();
        }, 3000);
        return () => clearInterval(interval);
    }, [autoRefresh, container, mode, callId, hidePayloads, since, until, includeDebug, view]);

    useEffect(() => {
        if (autoRefresh && isPinnedToBottom) {
            logsEndRef.current?.scrollIntoView({ behavior: "instant" as any });
        }
    }, [logs, events, autoRefresh, isPinnedToBottom]);

    useEffect(() => {
        // Keep legacy `preset` in sync for old links, but prefer `view`
        updateUrlParams({ view });
    }, [view]);

    const isIssueSignal = (e: LogEvent) => {
        if (e.level !== 'info') return false;
        const t = (e.msg || '').toLowerCase();
        // Exclude per-frame noise that fires every ~20ms.
        if (t.includes('encode resample')) return false;
        if (t.includes('encode config - reading provider config')) return false;
        if (t.includes('encoded for provider')) return false;
        if (t.includes('continuous input') && (t.includes('forwarding frame') || t.includes('frame sent'))) return false;
        if (t.includes('audiosocket rx') && t.includes('frame received')) return false;
        // Useful signals for narrowing issues
        const keywords = [
            'mismatch',
            'drift',
            'buffer',
            'underflow',
            'overflow',
            'dropped',
            'gap',
            'jitter',
            'loss',
            'rtt',
            'timeout',
            'retry',
            'reconnect',
            'fallback',
            'no active streaming',
            'grace_ms capped',
            'dc offset',
            'low audio energy',
        ];
        return keywords.some(k => t.includes(k));
    };

    const filteredEvents = useMemo(() => {
        if (mode !== 'troubleshoot') return [];
        const base = includeDebug ? events : events.filter(e => e.level !== 'debug');
        const viewFiltered = (() => {
            switch (view) {
                case 'overview':
                    return base.filter(e => e.level === 'error' || e.level === 'warning' || (e.level === 'info' && e.milestone));
                case 'issues':
                    return base.filter(e => e.level === 'error' || e.level === 'warning' || isIssueSignal(e));
                case 'provider':
                    return base.filter(e => e.category === 'provider' || e.level === 'error' || e.level === 'warning');
                case 'media':
                    return base.filter(e => e.category === 'audio' || e.category === 'transport' || e.level === 'error' || e.level === 'warning');
                case 'vad':
                    return base.filter(e => e.category === 'vad' || e.level === 'error' || e.level === 'warning');
                case 'tools':
                    return base.filter(e => e.category === 'tools' || e.level === 'error' || e.level === 'warning');
                case 'all':
                default:
                    return base;
            }
        })();

        if (!q.trim()) return viewFiltered;
        const qn = q.trim().toLowerCase();
        return viewFiltered.filter(e => (e.raw || '').toLowerCase().includes(qn) || (e.msg || '').toLowerCase().includes(qn));
    }, [events, mode, view, includeDebug, q]);

    const displayEvents = useMemo(() => {
        if (!hideRepeats) return (filteredEvents as any);
        const out: Array<LogEvent & { repeat?: number }> = [];
        for (const e of filteredEvents) {
            const prev = out[out.length - 1];
            const same =
                prev &&
                prev.level === e.level &&
                prev.category === e.category &&
                prev.msg === e.msg &&
                prev.call_id === e.call_id &&
                prev.provider === e.provider &&
                prev.milestone === e.milestone;
            if (same) {
                prev.repeat = (prev.repeat || 1) + 1;
            } else {
                out.push({ ...e, repeat: 1 });
            }
        }
        return out;
    }, [filteredEvents, hideRepeats]);

    const formatMeta = (meta?: Record<string, string>) => {
        if (!meta) return '';
        const entries = Object.entries(meta).filter(([_, v]) => v !== undefined && v !== null && String(v).trim() !== '');
        if (!entries.length) return '';
        // Keep compact: show up to 6 fields in the row.
        return entries
            .slice(0, 6)
            .map(([k, v]) => `${k}=${v}`)
            .join(' ');
    };

    const levelBadge = (lvl: LogLevel) => {
        const cls =
            lvl === 'error' ? 'bg-red-600/20 text-red-300 border-red-800' :
            lvl === 'warning' ? 'bg-yellow-600/20 text-yellow-200 border-yellow-800' :
            lvl === 'info' ? 'bg-blue-600/20 text-blue-200 border-blue-800' :
            'bg-gray-600/20 text-gray-200 border-gray-700';
        return <span className={`inline-flex items-center rounded border px-2 py-0.5 text-[10px] ${cls}`}>{lvl.toUpperCase()}</span>;
    };

    const highlightMatch = (line: string, term: string): React.ReactNode => {
        if (!term) return line;
        const escapedTerm = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const regex = new RegExp(`(${escapedTerm})`, 'gi');
        const parts = line.split(regex);
        return parts.map((part, i) =>
            i % 2 === 1
                ? <mark key={i} className="bg-yellow-400/30 text-yellow-200 rounded px-0.5">{part}</mark>
                : part
        );
    };

    const allRawLines = useMemo(() => {
        if (!logs) return [];
        const lines = logs.split('\n');
        if (lines.length > 0 && lines[lines.length - 1] === '') lines.pop();
        return lines;
    }, [logs]);

    const filteredRawLines = useMemo(() => {
        if (!q) return allRawLines;
        return allRawLines.filter(line => line.toLowerCase().includes(q.toLowerCase()));
    }, [allRawLines, q]);

    return (
        <div className="space-y-6 h-[calc(100vh-140px)] flex flex-col">
            <div className="flex justify-between items-center flex-shrink-0">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">System Logs</h1>
                    <p className="text-muted-foreground mt-1">
                        Raw container logs (quick level filter) and a call-centric Troubleshoot view.
                    </p>
                </div>
                <div className="flex space-x-2 items-center">
                    <button
                        onClick={async () => {
                            try {
                                const response = await axios.get('/api/config/export-logs', { responseType: 'blob' });
                                const url = window.URL.createObjectURL(new Blob([response.data]));
                                const link = document.createElement('a');
                                link.href = url;
                                link.setAttribute('download', `debug-logs-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.zip`);
                                document.body.appendChild(link);
                                link.click();
                                link.remove();
                            } catch (err) {
                                console.error('Failed to export logs', err);
                                toast.error('Failed to export logs');
                            }
                        }}
                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-3"
                        title="Export Logs & Config for Debugging"
                    >
                        <span className="mr-2">Export</span>
                        <Terminal className="w-4 h-4" />
                    </button>

                    <select
                        className="h-9 rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                        value={container}
                        onChange={e => {
                            setContainer(e.target.value);
                            updateUrlParams({ container: e.target.value });
                        }}
                    >
                        <option value="ai_engine">AI Engine</option>
                        <option value="local_ai_server">Local AI Server</option>
                        <option value="admin_ui">Admin UI</option>
                    </select>

                    <select
                        className="h-9 rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                        value={mode}
                        onChange={e => {
                            const nextMode = e.target.value as LogsMode;
                            setMode(nextMode);
                            updateUrlParams({ mode: nextMode });
                        }}
                        title="Logs View"
                    >
                        <option value="troubleshoot">Troubleshoot</option>
                        <option value="raw">Raw</option>
                    </select>

                    <button
                        onClick={() => setAutoRefresh(!autoRefresh)}
                        className={`inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 h-9 px-3 shadow-sm ${autoRefresh
                            ? 'bg-primary text-primary-foreground hover:bg-primary/90'
                            : 'border border-input bg-background hover:bg-accent hover:text-accent-foreground'
                            }`}
                        title={autoRefresh ? "Pause Auto-refresh" : "Resume Auto-refresh"}
                    >
                        {autoRefresh ? <Pause className="w-4 h-4 mr-2" /> : <Play className="w-4 h-4 mr-2" />}
                        {autoRefresh ? 'Live' : 'Paused'}
                    </button>

                    <button
                        onClick={() => {
                            if (mode === 'troubleshoot') {
                                if (showCallFinder) fetchCalls();
                                else fetchEvents();
                            } else {
                                fetchLogs();
                            }
                        }}
                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-3"
                        title="Refresh Now"
                    >
                        <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
                    </button>
                </div>
            </div>

            {mode === 'raw' && (
                <div className="flex flex-wrap items-center gap-3 border rounded-lg p-3 bg-background">
                    <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">Levels</span>
                        {(['error', 'warning', 'info', 'debug'] as LogLevel[]).map(lvl => (
                            <label key={lvl} className="flex items-center gap-1 text-xs">
                                <input
                                    type="checkbox"
                                    checked={rawLevels.includes(lvl)}
                                    onChange={e => {
                                        const next = e.target.checked
                                            ? Array.from(new Set([...rawLevels, lvl]))
                                            : rawLevels.filter(x => x !== lvl);
                                        setRawLevels(next);
                                        updateUrlParams({ raw_levels: next.join(',') });
                                    }}
                                />
                                {lvl}
                            </label>
                        ))}
                    </div>
                    <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">Search</span>
                        <input
                            className="h-8 w-[280px] rounded-md border border-input bg-background px-2 py-1 text-xs"
                            placeholder="text match…"
                            value={q}
                            onChange={e => {
                                setQ(e.target.value);
                                updateUrlParams({ q: e.target.value });
                            }}
                        />
                    </div>
                    {logs && (
                        <span className="text-xs text-muted-foreground tabular-nums">
                            {q
                                ? `${filteredRawLines.length} / ${allRawLines.length} lines`
                                : `${allRawLines.length} lines`}
                        </span>
                    )}
                    <button
                        onClick={() => logsEndRef.current?.scrollIntoView({ behavior: "smooth" })}
                        className="ml-auto inline-flex items-center justify-center rounded-md border border-input bg-background hover:bg-accent p-2"
                        title="Scroll to Bottom"
                        aria-label="Scroll to Bottom"
                    >
                        <ArrowDown className="w-4 h-4" />
                    </button>
                </div>
            )}

            {mode === 'troubleshoot' && showCallFinder && (
                <div className="border rounded-lg p-4 bg-background space-y-3">
                    <div className="flex items-center justify-between">
                        <div className="font-semibold">Find a Call</div>
                        <div className="text-xs text-muted-foreground">Uses Call History (fast and reliable)</div>
                    </div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                        <div>
                            <div className="text-xs text-muted-foreground mb-1">Caller Number</div>
                            <input
                                className="h-8 w-full rounded-md border border-input bg-background px-2 py-1 text-xs"
                                placeholder="Phone number"
                                value={callFilters.caller_number}
                                onChange={e => { setCallPage(1); setCallFilters(f => ({ ...f, caller_number: e.target.value })); }}
                            />
                        </div>
                        <div>
                            <div className="text-xs text-muted-foreground mb-1">Caller Name</div>
                            <input
                                className="h-8 w-full rounded-md border border-input bg-background px-2 py-1 text-xs"
                                placeholder="Name"
                                value={callFilters.caller_name}
                                onChange={e => { setCallPage(1); setCallFilters(f => ({ ...f, caller_name: e.target.value })); }}
                            />
                        </div>
                        <div>
                            <div className="text-xs text-muted-foreground mb-1">Provider</div>
                            <select
                                className="h-8 w-full rounded-md border border-input bg-background px-2 py-1 text-xs"
                                value={callFilters.provider_name}
                                onChange={e => { setCallPage(1); setCallFilters(f => ({ ...f, provider_name: e.target.value })); }}
                            >
                                <option value="">All</option>
                                {(callFilterOptions?.providers || []).map(p => <option key={p} value={p}>{p}</option>)}
                            </select>
                        </div>
                        <div>
                            <div className="text-xs text-muted-foreground mb-1">Pipeline</div>
                            <select
                                className="h-8 w-full rounded-md border border-input bg-background px-2 py-1 text-xs"
                                value={callFilters.pipeline_name}
                                onChange={e => { setCallPage(1); setCallFilters(f => ({ ...f, pipeline_name: e.target.value })); }}
                            >
                                <option value="">All</option>
                                {(callFilterOptions?.pipelines || []).map(p => <option key={p} value={p}>{p}</option>)}
                            </select>
                        </div>
                        <div>
                            <div className="text-xs text-muted-foreground mb-1">Context</div>
                            <select
                                className="h-8 w-full rounded-md border border-input bg-background px-2 py-1 text-xs"
                                value={callFilters.context_name}
                                onChange={e => { setCallPage(1); setCallFilters(f => ({ ...f, context_name: e.target.value })); }}
                            >
                                <option value="">All</option>
                                {(callFilterOptions?.contexts || []).map(c => <option key={c} value={c}>{c}</option>)}
                            </select>
                        </div>
                        <div>
                            <div className="text-xs text-muted-foreground mb-1">Outcome</div>
                            <select
                                className="h-8 w-full rounded-md border border-input bg-background px-2 py-1 text-xs"
                                value={callFilters.outcome}
                                onChange={e => { setCallPage(1); setCallFilters(f => ({ ...f, outcome: e.target.value })); }}
                            >
                                <option value="">All</option>
                                {(callFilterOptions?.outcomes || []).map(o => <option key={o} value={o}>{o}</option>)}
                            </select>
                        </div>
                        <div>
                            <div className="text-xs text-muted-foreground mb-1">From Date</div>
                            <input
                                type="date"
                                className="h-8 w-full rounded-md border border-input bg-background px-2 py-1 text-xs"
                                value={callFilters.start_date}
                                onChange={e => { setCallPage(1); setCallFilters(f => ({ ...f, start_date: e.target.value })); }}
                            />
                        </div>
                        <div>
                            <div className="text-xs text-muted-foreground mb-1">To Date</div>
                            <input
                                type="date"
                                className="h-8 w-full rounded-md border border-input bg-background px-2 py-1 text-xs"
                                value={callFilters.end_date}
                                onChange={e => { setCallPage(1); setCallFilters(f => ({ ...f, end_date: e.target.value })); }}
                            />
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        <button
                            onClick={fetchCalls}
                            className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-3"
                        >
                            Search
                        </button>
                        <button
                            onClick={() => { setCallPage(1); setCallFilters({ caller_number: '', caller_name: '', provider_name: '', pipeline_name: '', context_name: '', outcome: '', start_date: '', end_date: '' }); }}
                            className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-3"
                        >
                            Clear
                        </button>
                        <div className="text-xs text-muted-foreground">{callLoading ? 'Loading…' : `${callResults.length} results`}</div>
                    </div>

                    <div className="border rounded-lg overflow-hidden">
                        <div className="grid grid-cols-6 gap-2 px-3 py-2 text-xs bg-muted/40 text-muted-foreground">
                            <div>Caller</div>
                            <div>Time</div>
                            <div>Duration</div>
                            <div>Provider</div>
                            <div>Context</div>
                            <div>Actions</div>
                        </div>
                        {callResults.map(r => (
                            <div key={r.id} className="grid grid-cols-6 gap-2 px-3 py-2 text-xs border-t">
                                <div className="truncate">{r.caller_number || 'Unknown'}{r.caller_name ? ` (${r.caller_name})` : ''}</div>
                                <div className="truncate">{r.start_time ? new Date(r.start_time).toLocaleString() : '-'}</div>
                                <div>{Math.round(r.duration_seconds)}s</div>
                                <div className="truncate">{r.provider_name}</div>
                                <div className="truncate">{r.context_name || '-'}</div>
                                <div>
                                    <button
                                        onClick={() => {
                                            setCallId(r.call_id);
                                            setSince(r.start_time || '');
                                            setUntil(r.end_time || '');
                                            setShowCallFinder(false);
                                            setAutoRefresh(false);
                                            updateUrlParams({ mode: 'troubleshoot', call_id: r.call_id, since: r.start_time || '', until: r.end_time || '' });
                                        }}
                                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-xs font-medium transition-colors border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-8 px-2"
                                    >
                                        Troubleshoot
                                    </button>
                                </div>
                            </div>
                        ))}
                        {!callResults.length && !callLoading && (
                            <div className="px-3 py-4 text-xs text-muted-foreground">No calls match the current filters.</div>
                        )}
                    </div>

                    <div className="flex items-center justify-end gap-2 text-xs">
                        <button
                            disabled={callPage <= 1}
                            onClick={() => setCallPage(p => Math.max(1, p - 1))}
                            className="rounded-md border px-2 py-1 disabled:opacity-50"
                        >
                            Prev
                        </button>
                        <span className="text-muted-foreground">Page {callPage} / {callTotalPages}</span>
                        <button
                            disabled={callPage >= callTotalPages}
                            onClick={() => setCallPage(p => Math.min(callTotalPages, p + 1))}
                            className="rounded-md border px-2 py-1 disabled:opacity-50"
                        >
                            Next
                        </button>
                    </div>
                </div>
            )}

            {mode === 'troubleshoot' && !showCallFinder && (
                <div className="flex flex-wrap items-center gap-2 border rounded-lg p-3 bg-background">
                    <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">View</span>
                        <select
                            className="h-8 rounded-md border border-input bg-background px-2 py-1 text-xs"
                            value={view}
                            onChange={e => {
                                const nextView = e.target.value as TroubleshootView;
                                setView(nextView);
                                updateUrlParams({ view: nextView });
                            }}
                        >
                            <option value="overview">Overview</option>
                            <option value="issues">Issues</option>
                            <option value="provider">Provider</option>
                            <option value="media">Media</option>
                            <option value="vad">Barge-in / VAD</option>
                            <option value="tools">Tools</option>
                            <option value="all">All</option>
                        </select>
                    </div>

                    <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">Call</span>
                        <input
                            className="h-8 w-[280px] rounded-md border border-input bg-background px-2 py-1 text-xs"
                            placeholder="Call ID"
                            value={callId}
                            onChange={e => {
                                setCallId(e.target.value);
                                updateUrlParams({ call_id: e.target.value });
                            }}
                        />
                        <button
                            onClick={() => {
                                setCallId('');
                                setSince('');
                                setUntil('');
                                setShowCallFinder(true);
                                updateUrlParams({ call_id: '', since: '', until: '' });
                            }}
                            className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-xs font-medium transition-colors border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-8 px-2"
                            title="Pick a different call"
                        >
                            Find Call
                        </button>
                    </div>

                    <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">Search</span>
                        <input
                            className="h-8 w-[240px] rounded-md border border-input bg-background px-2 py-1 text-xs"
                            placeholder="text match…"
                            value={q}
                            onChange={e => {
                                setQ(e.target.value);
                                updateUrlParams({ q: e.target.value });
                            }}
                        />
                    </div>

                    {events.length > 0 && (
                        <span className="text-xs text-muted-foreground tabular-nums">
                            {q
                                ? `${filteredEvents.length} / ${events.length} events`
                                : `${events.length} events`}
                        </span>
                    )}

                    <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">Since</span>
                        <input
                            className="h-8 w-[240px] rounded-md border border-input bg-background px-2 py-1 text-xs"
                            placeholder="ISO8601 (optional)"
                            value={since}
                            onChange={e => {
                                setSince(e.target.value);
                                updateUrlParams({ since: e.target.value });
                            }}
                        />
                    </div>

                    <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">Until</span>
                        <input
                            className="h-8 w-[240px] rounded-md border border-input bg-background px-2 py-1 text-xs"
                            placeholder="ISO8601 (optional)"
                            value={until}
                            onChange={e => {
                                setUntil(e.target.value);
                                updateUrlParams({ until: e.target.value });
                            }}
                        />
                    </div>

                    <label className="flex items-center gap-2 text-xs">
                        <input
                            type="checkbox"
                            checked={hidePayloads}
                            onChange={e => {
                                setHidePayloads(e.target.checked);
                                updateUrlParams({ hide_payloads: e.target.checked ? 'true' : 'false' });
                            }}
                        />
                        Hide transcripts / payloads
                    </label>

                    <label className="flex items-center gap-2 text-xs">
                        <input
                            type="checkbox"
                            checked={includeDebug}
                            onChange={e => {
                                setIncludeDebug(e.target.checked);
                                updateUrlParams({ include_debug: e.target.checked ? 'true' : 'false' });
                            }}
                        />
                        Include debug
                    </label>

                    <label className="flex items-center gap-2 text-xs">
                        <input
                            type="checkbox"
                            checked={hideRepeats}
                            onChange={e => {
                                setHideRepeats(e.target.checked);
                                updateUrlParams({ hide_repeats: e.target.checked ? 'true' : 'false' });
                            }}
                        />
                        Hide repeats
                    </label>
                </div>
            )}

            {mode === 'troubleshoot' && !showCallFinder && (eventsMeta?.call || eventsMeta?.window || (eventsMeta?.related_ids && eventsMeta.related_ids.length > 1)) && (
                <div className="border rounded-lg p-3 bg-background text-xs">
                    {eventsMeta?.call && (
                        <div className="flex flex-wrap gap-x-4 gap-y-1">
                            <div><span className="text-muted-foreground">Caller</span> {eventsMeta.call.caller_number || 'unknown'}{eventsMeta.call.caller_name ? ` (${eventsMeta.call.caller_name})` : ''}</div>
                            <div><span className="text-muted-foreground">Provider</span> {eventsMeta.call.provider_name}</div>
                            <div><span className="text-muted-foreground">Pipeline</span> {eventsMeta.call.pipeline_name || 'default'}</div>
                            <div><span className="text-muted-foreground">Context</span> {eventsMeta.call.context_name || 'unknown'}</div>
                            <div><span className="text-muted-foreground">Outcome</span> {eventsMeta.call.outcome}</div>
                            {eventsMeta.call.error_message && <div className="text-red-600"><span className="text-muted-foreground">Error</span> {eventsMeta.call.error_message}</div>}
                        </div>
                    )}
                    {eventsMeta?.window && (
                        <div className="mt-2 text-muted-foreground">
                            Window: {eventsMeta.window.source}{eventsMeta.window.since ? ` since=${eventsMeta.window.since}` : ''}{eventsMeta.window.until ? ` until=${eventsMeta.window.until}` : ''}
                        </div>
                    )}
                    {eventsMeta?.related_ids && eventsMeta.related_ids.length > 1 && (
                        <div className="mt-2">
                            <span className="text-muted-foreground">Related IDs</span>{' '}
                            <span className="font-mono">{eventsMeta.related_ids.join(', ')}</span>
                        </div>
                    )}
                </div>
            )}

            <div
                ref={scrollRef}
                onScroll={recomputePinned}
                className="flex-1 min-h-0 border rounded-lg bg-[#09090b] text-gray-300 font-mono text-xs p-4 overflow-auto shadow-inner relative"
            >
                <div className="absolute top-2 right-2 opacity-50 pointer-events-none">
                    <Terminal className="w-6 h-6" />
                </div>
                {mode === 'troubleshoot' && autoRefresh && !isPinnedToBottom && (
                    <button
                        onClick={() => logsEndRef.current?.scrollIntoView({ behavior: "smooth" })}
                        className="absolute bottom-3 right-3 z-10 inline-flex items-center justify-center rounded-md border border-gray-700 bg-black/60 px-3 py-1 text-[10px] text-gray-200 hover:bg-black/80"
                        title="Jump to latest"
                    >
                        Jump to latest
                    </button>
                )}
                {mode === 'troubleshoot' ? (
                    <div className="space-y-1">
                        {(displayEvents.length ? displayEvents : []).map((e, idx) => (
                            <div key={idx} className="flex gap-2 items-start hover:bg-white/5 px-2 py-1 rounded">
                                <div className="w-[90px] text-gray-500 shrink-0">
                                    {e.ts ? new Date(e.ts).toLocaleTimeString() : '--:--:--'}
                                </div>
                                <div className="shrink-0">{levelBadge(e.level)}</div>
                                <div className="shrink-0">
                                    <span className="inline-flex items-center rounded border border-gray-700 px-2 py-0.5 text-[10px] text-gray-200 bg-gray-600/10">
                                        {e.category}
                                    </span>
                                </div>
                                {e.milestone && (
                                    <div className="shrink-0">
                                        <span className="inline-flex items-center rounded border border-emerald-800 px-2 py-0.5 text-[10px] text-emerald-200 bg-emerald-600/10">
                                            milestone
                                        </span>
                                    </div>
                                )}
                                {(e.repeat || 1) > 1 && (
                                    <div className="shrink-0">
                                        <span className="inline-flex items-center rounded border border-gray-700 px-2 py-0.5 text-[10px] text-gray-300 bg-gray-600/10">
                                            x{e.repeat}
                                        </span>
                                    </div>
                                )}
                                <div className="flex-1 break-words">
                                    <div className="text-gray-200">{highlightMatch(e.msg, q)}</div>
                                    <div className="text-[10px] text-gray-500 mt-0.5">
                                        {e.call_id ? `call_id=${e.call_id} ` : ''}
                                        {e.provider ? `provider=${e.provider} ` : ''}
                                        {e.context ? `context=${e.context} ` : ''}
                                        {formatMeta(e.meta) ? `${formatMeta(e.meta)} ` : ''}
                                        {e.component ? `component=${e.component}` : ''}
                                    </div>
                                </div>
                            </div>
                        ))}
                        {!displayEvents.length && (
                            <div className="text-gray-400">{showCallFinder ? 'Pick a call to troubleshoot.' : 'No events match the current filters.'}</div>
                        )}
                    </div>
                ) : (
                    <>
                        {logs && filteredRawLines.length > 0 ? (
                            <div className="space-y-0 font-mono">
                                {filteredRawLines.map((line, i) => {
                                    let className = 'text-gray-300';
                                    if (line.includes('ERROR') || line.includes('Exception') || line.includes('CRITICAL')) {
                                        className = 'text-red-500 font-bold';
                                    } else if (line.includes('WARN') || line.includes('WARNING')) {
                                        className = 'text-yellow-500';
                                    } else if (line.includes('INFO')) {
                                        className = 'text-blue-300';
                                    } else if (line.includes('DEBUG')) {
                                        className = 'text-gray-500';
                                    }
                                    return (
                                        <div key={i} className={`${className} hover:bg-white/5 px-1 rounded`} style={{ whiteSpace: 'pre-wrap' }}>
                                            {q ? highlightMatch(line, q) : parseAnsi(line)}
                                        </div>
                                    );
                                })}
                            </div>
                        ) : logs ? (
                            rawLevels.length === 1 && rawLevels[0] === 'debug' ? (
                                <div className="text-gray-400">
                                    No debug logs found.<br/><br/>
                                    <span className="text-gray-500">
                                        Debug logging may be disabled. To enable:<br/>
                                        1. Set <span className="text-blue-400">LOG_LEVEL=DEBUG</span> in your .env file<br/>
                                        2. Restart the container: <span className="text-blue-400">docker compose up -d --force-recreate {container}</span>
                                    </span>
                                </div>
                            ) : rawLevels.length > 0 && !rawLevels.includes('info') && !rawLevels.includes('warning') && !rawLevels.includes('error') ? (
                                <div className="text-gray-400">
                                    No logs found for selected level(s): {rawLevels.join(', ')}<br/><br/>
                                    <span className="text-gray-500">
                                        Try selecting additional levels like 'info' or 'warning'.
                                    </span>
                                </div>
                            ) : q ? (
                                <div className="text-gray-400 italic">No lines match the filter.</div>
                            ) : (
                                <div className="text-gray-400">No logs available...</div>
                            )
                        ) : (
                            <div className="text-gray-400">No logs available...</div>
                        )}
                    </>
                )}
                <div ref={logsEndRef} />
            </div>
        </div>
    );
};

export default LogsPage;
