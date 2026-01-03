import { useState, useEffect, useCallback } from 'react';
import {
    Phone, Filter, Download, Trash2, 
    ChevronLeft, ChevronRight, RefreshCw, X, MessageSquare,
    Wrench, AlertCircle, CheckCircle, ArrowRightLeft, PhoneOff,
    BarChart3, Users, Timer, Activity, TrendingUp, Zap, PieChart
} from 'lucide-react';
import axios from 'axios';
import { useLocation } from 'react-router-dom';

interface CallRecordSummary {
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
}

interface CallRecordDetail extends CallRecordSummary {
    pipeline_components: Record<string, string>;
    conversation_history: Array<{ role: string; content: string; timestamp?: string }>;
    transfer_destination: string | null;
    tool_calls: Array<{ name: string; params: any; result: string; message?: string; timestamp: string; duration_ms: number }>;
    max_turn_latency_ms: number;
    caller_audio_format: string;
    codec_alignment_ok: boolean;
}

interface CallStats {
    total_calls: number;
    avg_duration_seconds: number;
    max_duration_seconds: number;
    total_duration_seconds: number;
    avg_latency_ms: number;
    total_turns: number;
    total_barge_ins: number;
    outcomes: Record<string, number>;
    providers: Record<string, number>;
    pipelines: Record<string, number>;
    contexts: Record<string, number>;
    calls_per_day: Array<{ date: string; count: number }>;
    top_callers: Array<{ number: string; count: number }>;
    calls_with_tools: number;
    top_tools: Record<string, number>;
    active_calls: number;
}

interface FilterOptions {
    providers: string[];
    pipelines: string[];
    contexts: string[];
    outcomes: string[];
}

const formatDuration = (seconds: number): string => {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const mins = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    if (mins < 60) return `${mins}m ${secs}s`;
    const hrs = Math.floor(mins / 60);
    const remainMins = mins % 60;
    return `${hrs}h ${remainMins}m`;
};

const formatDate = (dateStr: string | null): string => {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    return date.toLocaleString();
};

const OutcomeIcon = ({ outcome }: { outcome: string }) => {
    switch (outcome) {
        case 'completed':
            return <CheckCircle className="w-4 h-4 text-green-500" />;
        case 'transferred':
            return <ArrowRightLeft className="w-4 h-4 text-blue-500" />;
        case 'error':
            return <AlertCircle className="w-4 h-4 text-red-500" />;
        case 'abandoned':
            return <PhoneOff className="w-4 h-4 text-yellow-500" />;
        default:
            return <Phone className="w-4 h-4 text-muted-foreground" />;
    }
};

const CallHistoryPage = () => {
    const location = useLocation();
    const [calls, setCalls] = useState<CallRecordSummary[]>([]);
    const [stats, setStats] = useState<CallStats | null>(null);
    const [filterOptions, setFilterOptions] = useState<FilterOptions | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [selectedCallSummary, setSelectedCallSummary] = useState<CallRecordSummary | null>(null);
    const [selectedCall, setSelectedCall] = useState<CallRecordDetail | null>(null);
    const [selectedCallLoading, setSelectedCallLoading] = useState(false);
    const [showStats, setShowStats] = useState(true);
    
    // Pagination
    const [page, setPage] = useState(1);
    const [pageSize] = useState(20);
    const [total, setTotal] = useState(0);
    const [totalPages, setTotalPages] = useState(1);
    
    // Filters
    const [filters, setFilters] = useState({
        caller_number: '',
        caller_name: '',
        provider_name: '',
        pipeline_name: '',
        context_name: '',
        outcome: '',
        start_date: '',
        end_date: '',
    });
    const [showFilters, setShowFilters] = useState(false);

    const fetchCalls = useCallback(async () => {
        try {
            setLoading(true);
            setError(null);
            
            const params: Record<string, any> = {
                page,
                page_size: pageSize,
            };
            
            // Add filters
            Object.entries(filters).forEach(([key, value]) => {
                if (value) params[key] = value;
            });
            
            const res = await axios.get('/api/calls', { params });
            setCalls(res.data.calls);
            setTotal(res.data.total);
            setTotalPages(res.data.total_pages);
        } catch (err: any) {
            console.error('Failed to fetch calls:', err);
            setError(err?.response?.data?.detail || 'Failed to load call history');
        } finally {
            setLoading(false);
        }
    }, [page, pageSize, filters]);

    const fetchStats = useCallback(async () => {
        try {
            const params: Record<string, any> = {};
            if (filters.start_date) params.start_date = filters.start_date;
            if (filters.end_date) params.end_date = filters.end_date;
            
            const res = await axios.get('/api/calls/stats', { params });
            setStats(res.data);
        } catch (err) {
            console.error('Failed to fetch stats:', err);
        }
    }, [filters.start_date, filters.end_date]);

    const fetchFilterOptions = useCallback(async () => {
        try {
            const res = await axios.get('/api/calls/filters');
            setFilterOptions(res.data);
        } catch (err) {
            console.error('Failed to fetch filter options:', err);
        }
    }, []);

    useEffect(() => {
        fetchCalls();
        fetchStats();
        fetchFilterOptions();
    }, [fetchCalls, fetchStats, fetchFilterOptions]);

    // Deep-link support: /history?id=<call_record_id>
    useEffect(() => {
        const id = new URLSearchParams(location.search).get('id');
        if (!id) return;
        if (selectedCall?.id === id || selectedCallSummary?.id === id) return;

        setSelectedCallSummary(null);
        setSelectedCall(null);
        setSelectedCallLoading(true);
        axios
            .get(`/api/calls/${id}`)
            .then(res => {
                const detail: CallRecordDetail = res.data;
                setSelectedCall(detail);
                setSelectedCallSummary({
                    id: detail.id,
                    call_id: detail.call_id,
                    caller_number: detail.caller_number,
                    caller_name: detail.caller_name,
                    start_time: detail.start_time,
                    end_time: detail.end_time,
                    duration_seconds: detail.duration_seconds,
                    provider_name: detail.provider_name,
                    pipeline_name: detail.pipeline_name,
                    context_name: detail.context_name,
                    outcome: detail.outcome,
                    error_message: detail.error_message,
                    avg_turn_latency_ms: detail.avg_turn_latency_ms,
                    total_turns: detail.total_turns,
                    barge_in_count: detail.barge_in_count,
                });
            })
            .catch(err => {
                console.error('Failed to open deep-linked call record:', err);
                setError(err?.response?.data?.detail || 'Failed to open call history record');
            })
            .finally(() => setSelectedCallLoading(false));
    }, [location.search, selectedCall?.id, selectedCallSummary?.id]);

    const handleExport = async (format: 'csv' | 'json') => {
        try {
            const params: Record<string, any> = {};
            Object.entries(filters).forEach(([key, value]) => {
                if (value) params[key] = value;
            });
            
            const res = await axios.get(`/api/calls/export/${format}`, { 
                params,
                responseType: 'blob'
            });
            
            const url = window.URL.createObjectURL(res.data);
            const a = document.createElement('a');
            a.href = url;
            a.download = `call_history.${format}`;
            a.click();
            window.URL.revokeObjectURL(url);
        } catch (err) {
            console.error('Failed to export:', err);
        }
    };

    const handleDelete = async (id: string) => {
        if (!confirm('Are you sure you want to delete this call record?')) return;
        try {
            await axios.delete(`/api/calls/${id}`);
            fetchCalls();
            fetchStats();
            if (selectedCall?.id === id || selectedCallSummary?.id === id) {
                setSelectedCall(null);
                setSelectedCallSummary(null);
            }
        } catch (err) {
            console.error('Failed to delete:', err);
        }
    };

    const openCallDetails = async (call: CallRecordSummary) => {
        setSelectedCallSummary(call);
        setSelectedCall(null);
        setSelectedCallLoading(true);
        try {
            const res = await axios.get(`/api/calls/${call.id}`);
            setSelectedCall(res.data);
        } catch (err) {
            console.error('Failed to fetch call details:', err);
        } finally {
            setSelectedCallLoading(false);
        }
    };

    const openTroubleshoot = (call: CallRecordSummary | CallRecordDetail) => {
        const callId = call.call_id;
        const start = (call as any).start_time;
        const end = (call as any).end_time;
        const params = new URLSearchParams();
        params.set('container', 'ai_engine');
        params.set('mode', 'troubleshoot');
        params.set('preset', 'important');
        params.set('call_id', callId);
        if (start) params.set('since', start);
        if (end) params.set('until', end);
        window.location.href = `/logs?${params.toString()}`;
    };

    const clearFilters = () => {
        setFilters({
            caller_number: '',
            caller_name: '',
            provider_name: '',
            pipeline_name: '',
            context_name: '',
            outcome: '',
            start_date: '',
            end_date: '',
        });
        setPage(1);
    };

    const hasActiveFilters = Object.values(filters).some(v => v !== '');
    const modalCall = selectedCall ?? selectedCallSummary;

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <h1 className="text-3xl font-bold">Call History</h1>
                <div className="flex items-center gap-2">
                    <button
                        onClick={() => setShowStats(!showStats)}
                        className={`p-2 rounded-lg border transition-colors ${showStats ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'}`}
                        title="Toggle Stats"
                    >
                        <BarChart3 className="w-5 h-5" />
                    </button>
                    <button
                        onClick={() => { fetchCalls(); fetchStats(); }}
                        className="p-2 rounded-lg border hover:bg-muted"
                        title="Refresh"
                    >
                        <RefreshCw className="w-5 h-5" />
                    </button>
                    <div className="relative">
                        <button
                            onClick={() => setShowFilters(!showFilters)}
                            className={`p-2 rounded-lg border transition-colors ${showFilters || hasActiveFilters ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'}`}
                            title="Filters"
                        >
                            <Filter className="w-5 h-5" />
                        </button>
                        {hasActiveFilters && (
                            <span className="absolute -top-1 -right-1 w-3 h-3 bg-red-500 rounded-full" />
                        )}
                    </div>
                    <div className="border-l h-6 mx-2" />
                    <button
                        onClick={() => handleExport('csv')}
                        className="flex items-center gap-2 px-3 py-2 rounded-lg border hover:bg-muted text-sm"
                    >
                        <Download className="w-4 h-4" />
                        CSV
                    </button>
                    <button
                        onClick={() => handleExport('json')}
                        className="flex items-center gap-2 px-3 py-2 rounded-lg border hover:bg-muted text-sm"
                    >
                        <Download className="w-4 h-4" />
                        JSON
                    </button>
                </div>
            </div>

            {/* Quick Troubleshoot */}
            {modalCall && (
                <div className="flex items-center gap-2">
                    <button
                        onClick={() => openTroubleshoot(modalCall)}
                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-3"
                        title="Open Logs filtered to this call"
                    >
                        Troubleshoot
                    </button>
                </div>
            )}

            {/* Stats Dashboard */}
            {showStats && stats && (
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                    <div className="bg-card border rounded-lg p-4">
                        <div className="flex items-center gap-2 text-muted-foreground text-sm">
                            <Phone className="w-4 h-4" />
                            Total Calls
                        </div>
                        <div className="text-2xl font-bold mt-1">{stats.total_calls}</div>
                    </div>
                    <div className="bg-card border rounded-lg p-4">
                        <div className="flex items-center gap-2 text-muted-foreground text-sm">
                            <PieChart className="w-4 h-4" />
                            Success / Failed
                        </div>
                        <div className="text-2xl font-bold mt-1">
                            {stats.outcomes?.completed || 0} / {stats.outcomes?.error || 0}
                        </div>
                        <div className="text-xs text-muted-foreground">
                            {stats.total_calls > 0 
                                ? Math.round(((stats.outcomes?.completed || 0) / stats.total_calls) * 100) 
                                : 0}% success rate
                        </div>
                    </div>
                    <div className="bg-card border rounded-lg p-4">
                        <div className="flex items-center gap-2 text-muted-foreground text-sm">
                            <Activity className="w-4 h-4" />
                            Active Calls
                        </div>
                        <div className="text-2xl font-bold mt-1">{stats.active_calls || 0}</div>
                    </div>
                    <div className="bg-card border rounded-lg p-4">
                        <div className="flex items-center gap-2 text-muted-foreground text-sm">
                            <Timer className="w-4 h-4" />
                            Avg Duration
                        </div>
                        <div className="text-2xl font-bold mt-1">{formatDuration(stats.avg_duration_seconds)}</div>
                    </div>
                    <div className="bg-card border rounded-lg p-4">
                        <div className="flex items-center gap-2 text-muted-foreground text-sm">
                            <TrendingUp className="w-4 h-4" />
                            Top Provider
                        </div>
                        <div className="text-lg font-bold mt-1 truncate">
                            {Object.entries(stats.providers || {}).sort((a, b) => b[1] - a[1])[0]?.[0] || '-'}
                        </div>
                    </div>
                    <div className="bg-card border rounded-lg p-4">
                        <div className="flex items-center gap-2 text-muted-foreground text-sm">
                            <Wrench className="w-4 h-4" />
                            Top Tool
                        </div>
                        <div className="text-lg font-bold mt-1 truncate">
                            {Object.entries(stats.top_tools || {}).sort((a, b) => b[1] - a[1])[0]?.[0] || '-'}
                        </div>
                        <div className="text-xs text-muted-foreground">{stats.calls_with_tools} calls used tools</div>
                    </div>
                </div>
            )}

            {/* Filters Panel */}
            {showFilters && (
                <div className="bg-card border rounded-lg p-4">
                    <div className="flex items-center justify-between mb-4">
                        <h3 className="font-semibold">Filters</h3>
                        {hasActiveFilters && (
                            <button onClick={clearFilters} className="text-sm text-primary hover:underline">
                                Clear all
                            </button>
                        )}
                    </div>
                    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-4">
                        <div>
                            <label className="text-sm text-muted-foreground">Caller Number</label>
                            <input
                                type="text"
                                value={filters.caller_number}
                                onChange={(e) => setFilters({ ...filters, caller_number: e.target.value })}
                                placeholder="Phone number"
                                className="w-full mt-1 px-3 py-2 bg-background border rounded-lg text-sm"
                            />
                        </div>
                        <div>
                            <label className="text-sm text-muted-foreground">Caller Name</label>
                            <input
                                type="text"
                                value={filters.caller_name}
                                onChange={(e) => setFilters({ ...filters, caller_name: e.target.value })}
                                placeholder="Name"
                                className="w-full mt-1 px-3 py-2 bg-background border rounded-lg text-sm"
                            />
                        </div>
                        <div>
                            <label className="text-sm text-muted-foreground">Provider</label>
                            <select
                                value={filters.provider_name}
                                onChange={(e) => setFilters({ ...filters, provider_name: e.target.value })}
                                className="w-full mt-1 px-3 py-2 bg-background border rounded-lg text-sm"
                            >
                                <option value="">All</option>
                                {filterOptions?.providers.map(p => (
                                    <option key={p} value={p}>{p}</option>
                                ))}
                            </select>
                        </div>
                        <div>
                            <label className="text-sm text-muted-foreground">Pipeline</label>
                            <select
                                value={filters.pipeline_name}
                                onChange={(e) => setFilters({ ...filters, pipeline_name: e.target.value })}
                                className="w-full mt-1 px-3 py-2 bg-background border rounded-lg text-sm"
                            >
                                <option value="">All</option>
                                {filterOptions?.pipelines.map(p => (
                                    <option key={p} value={p}>{p}</option>
                                ))}
                            </select>
                        </div>
                        <div>
                            <label className="text-sm text-muted-foreground">Context</label>
                            <select
                                value={filters.context_name}
                                onChange={(e) => setFilters({ ...filters, context_name: e.target.value })}
                                className="w-full mt-1 px-3 py-2 bg-background border rounded-lg text-sm"
                            >
                                <option value="">All</option>
                                {filterOptions?.contexts.map(c => (
                                    <option key={c} value={c}>{c}</option>
                                ))}
                            </select>
                        </div>
                        <div>
                            <label className="text-sm text-muted-foreground">Outcome</label>
                            <select
                                value={filters.outcome}
                                onChange={(e) => setFilters({ ...filters, outcome: e.target.value })}
                                className="w-full mt-1 px-3 py-2 bg-background border rounded-lg text-sm"
                            >
                                <option value="">All</option>
                                {filterOptions?.outcomes.map(o => (
                                    <option key={o} value={o}>{o}</option>
                                ))}
                            </select>
                        </div>
                        <div>
                            <label className="text-sm text-muted-foreground">From Date</label>
                            <input
                                type="date"
                                value={filters.start_date}
                                onChange={(e) => setFilters({ ...filters, start_date: e.target.value })}
                                className="w-full mt-1 px-3 py-2 bg-background border rounded-lg text-sm"
                            />
                        </div>
                        <div>
                            <label className="text-sm text-muted-foreground">To Date</label>
                            <input
                                type="date"
                                value={filters.end_date}
                                onChange={(e) => setFilters({ ...filters, end_date: e.target.value })}
                                className="w-full mt-1 px-3 py-2 bg-background border rounded-lg text-sm"
                            />
                        </div>
                    </div>
                </div>
            )}

            {/* Error State */}
            {error && (
                <div className="bg-destructive/10 border border-destructive/20 rounded-lg p-4 text-destructive">
                    {error}
                </div>
            )}

            {/* Loading State */}
            {loading && (
                <div className="flex items-center justify-center py-12">
                    <RefreshCw className="w-8 h-8 animate-spin text-muted-foreground" />
                </div>
            )}

            {/* Empty State */}
            {!loading && !error && calls.length === 0 && (
                <div className="bg-card border rounded-lg p-12 text-center">
                    <div className="mx-auto w-16 h-16 bg-muted rounded-full flex items-center justify-center mb-4">
                        <Phone className="w-8 h-8 text-muted-foreground" />
                    </div>
                    <h2 className="text-xl font-semibold mb-2">No Calls Found</h2>
                    <p className="text-muted-foreground">
                        {hasActiveFilters 
                            ? 'No calls match your filters. Try adjusting your search criteria.'
                            : 'Call history will appear here once calls are made.'}
                    </p>
                </div>
            )}

            {/* Call List */}
            {!loading && !error && calls.length > 0 && (
                <>
                    <div className="bg-card border rounded-lg overflow-x-auto">
                        <table className="w-full min-w-[1000px]">
                            <thead className="bg-muted/50">
                                <tr>
                                    <th className="text-left px-4 py-3 text-sm font-medium">Caller</th>
                                    <th className="text-left px-4 py-3 text-sm font-medium">Time</th>
                                    <th className="text-left px-4 py-3 text-sm font-medium">Duration</th>
                                    <th className="text-left px-4 py-3 text-sm font-medium">Provider / Pipeline</th>
                                    <th className="text-left px-4 py-3 text-sm font-medium">Context</th>
                                    <th className="text-left px-4 py-3 text-sm font-medium">Outcome</th>
                                    <th className="text-left px-4 py-3 text-sm font-medium">Turns</th>
                                    <th className="text-left px-4 py-3 text-sm font-medium">Latency</th>
                                    <th className="text-left px-4 py-3 text-sm font-medium">Barge-ins</th>
                                    <th className="text-center px-4 py-3 text-sm font-medium w-20">Actions</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-border">
                                {calls.map((call) => (
	                                    <tr 
	                                        key={call.id} 
	                                        className="hover:bg-muted/30 cursor-pointer"
	                                        onClick={() => openCallDetails(call)}
	                                    >
                                        <td className="px-4 py-3">
                                            <div className="font-medium">{call.caller_number || 'Unknown'}</div>
                                            {call.caller_name && (
                                                <div className="text-sm text-muted-foreground">{call.caller_name}</div>
                                            )}
                                        </td>
                                        <td className="px-4 py-3 text-sm">{formatDate(call.start_time)}</td>
                                        <td className="px-4 py-3 text-sm">{formatDuration(call.duration_seconds)}</td>
                                        <td className="px-4 py-3 text-sm">{call.pipeline_name || call.provider_name}</td>
                                        <td className="px-4 py-3 text-sm">{call.context_name || '-'}</td>
                                        <td className="px-4 py-3">
                                            <div className="flex items-center gap-2">
                                                <OutcomeIcon outcome={call.outcome} />
                                                <span className="text-sm capitalize">{call.outcome}</span>
                                            </div>
                                        </td>
                                        <td className="px-4 py-3 text-sm">{call.total_turns}</td>
                                        <td className="px-4 py-3 text-sm">{(call.avg_turn_latency_ms / 1000).toFixed(1)}s</td>
                                        <td className="px-4 py-3 text-sm">{call.barge_in_count}</td>
                                        <td className="px-4 py-3 text-center w-20">
                                            <button
                                                onClick={(e) => { e.stopPropagation(); handleDelete(call.id); }}
                                                className="p-2 hover:bg-destructive/10 rounded text-destructive"
                                                title="Delete"
                                            >
                                                <Trash2 className="w-4 h-4" />
                                            </button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>

                    {/* Pagination */}
                    <div className="flex items-center justify-between">
                        <div className="text-sm text-muted-foreground">
                            Showing {((page - 1) * pageSize) + 1} to {Math.min(page * pageSize, total)} of {total} calls
                        </div>
                        <div className="flex items-center gap-2">
                            <button
                                onClick={() => setPage(p => Math.max(1, p - 1))}
                                disabled={page === 1}
                                className="p-2 rounded-lg border hover:bg-muted disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                                <ChevronLeft className="w-5 h-5" />
                            </button>
                            <span className="text-sm">
                                Page {page} of {totalPages}
                            </span>
                            <button
                                onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                                disabled={page === totalPages}
                                className="p-2 rounded-lg border hover:bg-muted disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                                <ChevronRight className="w-5 h-5" />
                            </button>
                        </div>
                    </div>
                </>
            )}

            {/* Call Detail Modal */}
            {modalCall && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
                    <div className="bg-card border rounded-lg w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col">
                        {/* Modal Header */}
                        <div className="flex items-center justify-between p-4 border-b">
                            <div>
                                <h2 className="text-xl font-bold">Call Details</h2>
                                <p className="text-sm text-muted-foreground">{modalCall.call_id}</p>
                            </div>
                            <div className="flex items-center gap-2">
                                <button
                                    onClick={() => openTroubleshoot(modalCall)}
                                    className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-3"
                                    title="Open Logs filtered to this call"
                                >
                                    Troubleshoot
                                </button>
                                <button
                                    onClick={() => handleDelete(modalCall.id)}
                                    className="p-2 hover:bg-destructive/10 rounded-lg text-destructive"
                                    title="Delete this call"
                                >
                                    <Trash2 className="w-5 h-5" />
                                </button>
                                <button
                                    onClick={() => { setSelectedCall(null); setSelectedCallSummary(null); }}
                                    className="p-2 hover:bg-muted rounded-lg"
                                >
                                    <X className="w-5 h-5" />
                                </button>
                            </div>
                        </div>

                        {/* Modal Content */}
                        <div className="flex-1 overflow-y-auto p-4 space-y-6">
                            {selectedCallLoading && (
                                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                    <RefreshCw className="w-4 h-4 animate-spin" />
                                    Loading full call details…
                                </div>
                            )}
                            {/* Overview */}
                            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                                <div>
                                    <div className="text-sm text-muted-foreground">Caller</div>
                                    <div className="font-medium">{modalCall.caller_number || 'Unknown'}</div>
                                    {modalCall.caller_name && (
                                        <div className="text-sm">{modalCall.caller_name}</div>
                                    )}
                                </div>
                                <div>
                                    <div className="text-sm text-muted-foreground">Duration</div>
                                    <div className="font-medium">{formatDuration(modalCall.duration_seconds)}</div>
                                </div>
                                <div>
                                    <div className="text-sm text-muted-foreground">Outcome</div>
                                    <div className="flex items-center gap-2">
                                        <OutcomeIcon outcome={modalCall.outcome} />
                                        <span className="font-medium capitalize">{modalCall.outcome}</span>
                                    </div>
                                </div>
                                <div>
                                    <div className="text-sm text-muted-foreground">Turns</div>
                                    <div className="font-medium">{modalCall.total_turns}</div>
                                </div>
                                <div>
                                    <div className="text-sm text-muted-foreground">Avg Latency</div>
                                    <div className="font-medium">{(modalCall.avg_turn_latency_ms / 1000).toFixed(2)}s</div>
                                </div>
                                <div>
                                    <div className="text-sm text-muted-foreground">Barge-ins</div>
                                    <div className="font-medium">{modalCall.barge_in_count}</div>
                                </div>
                            </div>

                            {/* Tool Calls Summary */}
                            <div>
                                <h3 className="font-semibold mb-2">Tool Executions ({selectedCall?.tool_calls.length || 0})</h3>
                                {!selectedCall ? (
                                    <p className="text-muted-foreground text-sm">Load the call to view tool details</p>
                                ) : selectedCall.tool_calls.length === 0 ? (
                                    <p className="text-muted-foreground text-sm">No tools were called during this call</p>
                                ) : (
                                    <div className="flex flex-wrap gap-2">
                                        {selectedCall.tool_calls.map((tool, i) => (
                                            <div key={i} className="bg-muted/30 rounded-lg px-3 py-2 text-sm flex items-center gap-2">
                                                <Wrench className="w-4 h-4" />
                                                <span className="font-medium">{tool.name}</span>
                                                <span className={`text-xs ${tool.result === 'success' ? 'text-green-500' : 'text-red-500'}`}>
                                                    {tool.result}
                                                </span>
                                                <span className="text-xs text-muted-foreground">{Math.round(tool.duration_ms)}ms</span>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>

                            {/* Configuration */}
                            <div>
                                <h3 className="font-semibold mb-2">Configuration</h3>
                                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                                    <div>
                                        <span className="text-muted-foreground">Provider:</span>{' '}
                                        <span className="font-medium">{modalCall.provider_name}</span>
                                    </div>
                                    <div>
                                        <span className="text-muted-foreground">Pipeline:</span>{' '}
                                        <span className="font-medium">{modalCall.pipeline_name || '-'}</span>
                                    </div>
                                    <div>
                                        <span className="text-muted-foreground">Context:</span>{' '}
                                        <span className="font-medium">{modalCall.context_name || '-'}</span>
                                    </div>
                                    <div>
                                        <span className="text-muted-foreground">Audio:</span>{' '}
                                        <span className="font-medium">{selectedCall?.caller_audio_format || '-'}</span>
                                    </div>
                                </div>
                            </div>

                            {/* Transcript */}
                            <div>
                                <h3 className="font-semibold mb-2">Conversation ({selectedCall?.conversation_history.length || 0} messages)</h3>
                                {!selectedCall ? (
                                    <div className="bg-muted/30 rounded-lg p-4 text-sm text-muted-foreground">
                                        Load the call to view the transcript
                                    </div>
                                ) : (
                                    <div className="bg-muted/30 rounded-lg p-4 max-h-64 overflow-y-auto space-y-3">
                                        {selectedCall.conversation_history.length === 0 ? (
                                            <p className="text-muted-foreground text-sm">No conversation recorded</p>
                                        ) : (
                                            selectedCall.conversation_history.map((msg, i) => (
                                                <div key={i} className={`flex ${msg.role === 'assistant' ? 'justify-start' : 'justify-end'}`}>
                                                    <div className={`max-w-[80%] rounded-lg px-3 py-2 ${
                                                        msg.role === 'assistant' 
                                                            ? 'bg-primary/10 text-foreground' 
                                                            : 'bg-muted text-foreground'
                                                    }`}>
                                                        <div className="text-xs text-muted-foreground mb-1 capitalize">{msg.role}</div>
                                                        <div className="text-sm">{msg.content}</div>
                                                        {msg.timestamp && (
                                                            <div className="text-xs text-muted-foreground mt-1">
                                                                {new Date(msg.timestamp).toLocaleTimeString()}
                                                            </div>
                                                        )}
                                                    </div>
                                                </div>
                                            ))
                                        )}
                                    </div>
                                )}
                            </div>

                            {/* Tool Call Details */}
                            {selectedCall && selectedCall.tool_calls.length > 0 && (
                                <div>
                                    <h3 className="font-semibold mb-2">Tool Call Details</h3>
                                    <div className="space-y-2">
                                        {selectedCall.tool_calls.map((tool, i) => (
                                            <div key={i} className="bg-muted/30 rounded-lg p-3 text-sm">
                                                <div className="flex items-center justify-between">
                                                    <div className="flex items-center gap-2">
                                                        <Wrench className="w-4 h-4" />
                                                        <span className="font-medium">{tool.name}</span>
                                                    </div>
                                                    <div className="flex items-center gap-2 text-muted-foreground">
                                                        <span>{Math.round(tool.duration_ms)}ms</span>
                                                        <span className={tool.result === 'success' ? 'text-green-500' : 'text-red-500'}>
                                                            {tool.result}
                                                        </span>
                                                    </div>
                                                </div>
                                                {tool.params && Object.keys(tool.params).length > 0 && (
                                                    <pre className="mt-2 text-xs bg-background/50 rounded p-2 overflow-x-auto">
                                                        {JSON.stringify(tool.params, null, 2)}
                                                    </pre>
                                                )}
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Error Message */}
                            {modalCall.error_message && (
                                <div>
                                    <h3 className="font-semibold mb-2 text-destructive">Error</h3>
                                    <div className="bg-destructive/10 border border-destructive/20 rounded-lg p-3 text-sm">
                                        {modalCall.error_message}
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default CallHistoryPage;
