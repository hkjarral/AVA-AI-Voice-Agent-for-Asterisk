import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    CalendarClock,
    Plus,
    Play,
    Pause,
    Square,
    Copy,
    Pencil,
    Upload,
    RefreshCw,
    Trash2,
    FileDown,
    AlertTriangle,
    PhoneCall
} from 'lucide-react';

type CampaignStatus = 'draft' | 'running' | 'paused' | 'stopped';

interface OutboundCampaign {
    id: string;
    name: string;
    status: CampaignStatus;
    timezone: string;
    daily_window_start_local: string;
    daily_window_end_local: string;
    max_concurrent: number;
    min_interval_seconds_between_calls: number;
    default_context: string;
    voicemail_drop_mode: string;
    voicemail_drop_text?: string | null;
    voicemail_drop_media_uri?: string | null;
    created_at_utc?: string;
    updated_at_utc?: string;
}

interface CampaignStats {
    lead_states?: Record<string, number>;
    attempt_outcomes?: Record<string, number>;
}

interface LeadRow {
    id: string;
    phone_number: string;
    state: string;
    attempt_count: number;
    last_outcome?: string | null;
    last_attempt_at_utc?: string | null;
    custom_vars?: Record<string, any>;
    created_at_utc?: string;
}

interface AttemptRow {
    id: string;
    phone_number?: string | null;
    started_at_utc: string;
    ended_at_utc?: string | null;
    outcome?: string | null;
    amd_status?: string | null;
    amd_cause?: string | null;
    call_history_call_id?: string | null;
    error_message?: string | null;
}

type Notice = { type: 'success' | 'error' | 'info'; message: string };

const formatUtc = (iso?: string | null) => {
    if (!iso) return '-';
    try {
        return new Date(iso).toLocaleString();
    } catch {
        return iso;
    }
};

const StatusBadge = ({ status, label }: { status: CampaignStatus; label?: string }) => {
    const cls =
        status === 'running'
            ? 'bg-green-500/10 text-green-500 border-green-500/20'
            : status === 'paused'
                ? 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20'
                : status === 'stopped'
                    ? 'bg-red-500/10 text-red-500 border-red-500/20'
                    : 'bg-muted text-muted-foreground border-border';
    return (
        <span className={`inline-flex items-center px-2 py-0.5 rounded border text-xs font-medium ${cls}`}>
            {label || status}
        </span>
    );
};

const timeStringInZone = (timeZone: string): string | null => {
    try {
        const parts = new Intl.DateTimeFormat('en-US', {
            timeZone,
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
        }).formatToParts(new Date());
        const hour = parts.find(p => p.type === 'hour')?.value;
        const minute = parts.find(p => p.type === 'minute')?.value;
        if (!hour || !minute) return null;
        return `${hour}:${minute}`;
    } catch {
        return null;
    }
};

const withinDailyWindow = (nowHHMM: string, startHHMM: string, endHHMM: string): boolean => {
    if (!nowHHMM || !startHHMM || !endHHMM) return true;
    const crossesMidnight = endHHMM < startHHMM;
    if (!crossesMidnight) return nowHHMM >= startHHMM && nowHHMM <= endHHMM;
    return nowHHMM >= startHHMM || nowHHMM <= endHHMM;
};

const CallSchedulingPage = () => {
    const [tab, setTab] = useState<'campaigns' | 'leads' | 'attempts'>('campaigns');
    const [campaigns, setCampaigns] = useState<OutboundCampaign[]>([]);
    const [selectedCampaignId, setSelectedCampaignId] = useState<string | null>(null);
    const selectedCampaign = useMemo(
        () => campaigns.find(c => c.id === selectedCampaignId) || null,
        [campaigns, selectedCampaignId]
    );
    const selectedHasVoicemail = Boolean((selectedCampaign?.voicemail_drop_media_uri || '').trim());

    const windowInfo = useMemo(() => {
        if (!selectedCampaign) return null;
        const nowLocal = timeStringInZone(selectedCampaign.timezone || 'UTC');
        if (!nowLocal) return null;
        const within = withinDailyWindow(
            nowLocal,
            selectedCampaign.daily_window_start_local,
            selectedCampaign.daily_window_end_local
        );
        return { nowLocal, within };
    }, [selectedCampaign]);

    const [stats, setStats] = useState<CampaignStats | null>(null);
    const [leads, setLeads] = useState<LeadRow[]>([]);
    const [attempts, setAttempts] = useState<AttemptRow[]>([]);

    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<Notice | null>(null);

    const [showCreate, setShowCreate] = useState(false);
    const [createForm, setCreateForm] = useState({
        name: '',
        timezone: 'UTC',
        daily_window_start_local: '09:00',
        daily_window_end_local: '17:00',
        max_concurrent: 1,
        min_interval_seconds_between_calls: 5,
        default_context: 'default'
    });

    const [showEdit, setShowEdit] = useState(false);
    const [editForm, setEditForm] = useState({
        name: '',
        timezone: 'UTC',
        daily_window_start_local: '09:00',
        daily_window_end_local: '17:00',
        max_concurrent: 1,
        min_interval_seconds_between_calls: 5,
        default_context: 'default'
    });

    const [showSetupGuide, setShowSetupGuide] = useState(false);

    const crossesMidnight = useMemo(() => {
        const s = createForm.daily_window_start_local;
        const e = createForm.daily_window_end_local;
        return Boolean(s && e && e < s);
    }, [createForm.daily_window_start_local, createForm.daily_window_end_local]);

    const editCrossesMidnight = useMemo(() => {
        const s = editForm.daily_window_start_local;
        const e = editForm.daily_window_end_local;
        return Boolean(s && e && e < s);
    }, [editForm.daily_window_start_local, editForm.daily_window_end_local]);

    const refreshCampaigns = async () => {
        const res = await axios.get('/api/outbound/campaigns');
        setCampaigns(res.data || []);
        if (!selectedCampaignId && res.data?.length) {
            setSelectedCampaignId(res.data[0].id);
        }
    };

    const refreshCampaignDetails = async (campaignId: string) => {
        const [statsRes, leadsRes, attemptsRes] = await Promise.all([
            axios.get(`/api/outbound/campaigns/${campaignId}/stats`),
            axios.get(`/api/outbound/campaigns/${campaignId}/leads`, { params: { page: 1, page_size: 50 } }),
            axios.get(`/api/outbound/campaigns/${campaignId}/attempts`, { params: { page: 1, page_size: 50 } })
        ]);
        setStats(statsRes.data || {});
        setLeads(leadsRes.data?.leads || []);
        setAttempts(attemptsRes.data?.attempts || []);
    };

    useEffect(() => {
        let mounted = true;
        (async () => {
            try {
                setLoading(true);
                await refreshCampaigns();
                setError(null);
            } catch (e: any) {
                if (mounted) setError(e?.response?.data?.detail || e?.message || 'Failed to load campaigns');
            } finally {
                if (mounted) setLoading(false);
            }
        })();
        return () => {
            mounted = false;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        if (!selectedCampaignId) return;
        let stop = false;
        (async () => {
            try {
                await refreshCampaignDetails(selectedCampaignId);
            } catch {
                // ignore
            }
        })();
        const interval = setInterval(async () => {
            if (stop) return;
            try {
                await refreshCampaigns();
                await refreshCampaignDetails(selectedCampaignId);
            } catch {
                // ignore
            }
        }, 5000);
        return () => {
            stop = true;
            clearInterval(interval);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selectedCampaignId]);

    const createCampaign = async () => {
        try {
            const res = await axios.post('/api/outbound/campaigns', createForm);
            await refreshCampaigns();
            setSelectedCampaignId(res.data.id);
            setShowCreate(false);
            setNotice({ type: 'success', message: 'Campaign created. Upload voicemail and import leads before starting.' });
            setCreateForm({
                name: '',
                timezone: 'UTC',
                daily_window_start_local: '09:00',
                daily_window_end_local: '17:00',
                max_concurrent: 1,
                min_interval_seconds_between_calls: 5,
                default_context: 'default'
            });
        } catch (e: any) {
            setNotice({ type: 'error', message: e?.response?.data?.detail || e?.message || 'Failed to create campaign' });
        }
    };

    const setStatus = async (campaignId: string, status: CampaignStatus, cancel_pending: boolean = false) => {
        try {
            await axios.post(`/api/outbound/campaigns/${campaignId}/status`, { status, cancel_pending });
            await refreshCampaigns();
            await refreshCampaignDetails(campaignId);
            setNotice({ type: 'success', message: `Campaign status set to ${status}` });
        } catch (e: any) {
            setNotice({ type: 'error', message: e?.response?.data?.detail || e?.message || 'Failed to update campaign status' });
        }
    };

    const cloneCampaign = async (campaignId: string) => {
        try {
            const res = await axios.post(`/api/outbound/campaigns/${campaignId}/clone`);
            await refreshCampaigns();
            setSelectedCampaignId(res.data.id);
            setNotice({ type: 'success', message: 'Campaign cloned' });
        } catch (e: any) {
            setNotice({ type: 'error', message: e?.response?.data?.detail || e?.message || 'Failed to clone campaign' });
        }
    };

    const openEdit = () => {
        if (!selectedCampaign) return;
        setEditForm({
            name: selectedCampaign.name || '',
            timezone: selectedCampaign.timezone || 'UTC',
            daily_window_start_local: selectedCampaign.daily_window_start_local || '09:00',
            daily_window_end_local: selectedCampaign.daily_window_end_local || '17:00',
            max_concurrent: selectedCampaign.max_concurrent || 1,
            min_interval_seconds_between_calls: selectedCampaign.min_interval_seconds_between_calls || 5,
            default_context: selectedCampaign.default_context || 'default'
        });
        setShowEdit(true);
    };

    const saveEdit = async () => {
        if (!selectedCampaign) return;
        try {
            await axios.patch(`/api/outbound/campaigns/${selectedCampaign.id}`, editForm);
            setShowEdit(false);
            await refreshCampaigns();
            await refreshCampaignDetails(selectedCampaign.id);
            setNotice({ type: 'success', message: 'Campaign updated' });
        } catch (e: any) {
            setNotice({ type: 'error', message: e?.response?.data?.detail || e?.message || 'Failed to update campaign' });
        }
    };

    const cancelLead = async (leadId: string) => {
        try {
            await axios.post(`/api/outbound/leads/${leadId}/cancel`);
            if (selectedCampaignId) await refreshCampaignDetails(selectedCampaignId);
        } catch (e: any) {
            setNotice({ type: 'error', message: e?.response?.data?.detail || e?.message || 'Failed to cancel lead' });
        }
    };

    const downloadText = (filename: string, text: string) => {
        const blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
    };

    const importLeads = async (campaignId: string, file: File) => {
        const formData = new FormData();
        formData.append('file', file);
        try {
            const res = await axios.post(`/api/outbound/campaigns/${campaignId}/leads/import?skip_existing=true`, formData, {
                headers: { 'Content-Type': 'multipart/form-data' }
            });
            const data = res.data as LeadImportResponse;
            if (data?.error_csv) {
                downloadText(`outbound_import_errors_${new Date().toISOString().slice(0, 19)}.csv`, data.error_csv);
            }
            await refreshCampaignDetails(campaignId);
            setNotice({
                type: data.rejected > 0 ? 'info' : 'success',
                message: `Imported leads: accepted=${data.accepted}, rejected=${data.rejected}, duplicates=${data.duplicates}`
            });
        } catch (e: any) {
            setNotice({ type: 'error', message: e?.response?.data?.detail || e?.message || 'Failed to import leads' });
        }
    };

    const uploadVoicemail = async (campaignId: string, file: File) => {
        const formData = new FormData();
        formData.append('file', file);
        try {
            await axios.post(`/api/outbound/campaigns/${campaignId}/voicemail/upload`, formData, {
                headers: { 'Content-Type': 'multipart/form-data' }
            });
            await refreshCampaigns();
            await refreshCampaignDetails(campaignId);
            setNotice({ type: 'success', message: 'Voicemail uploaded and linked to campaign' });
        } catch (e: any) {
            setNotice({ type: 'error', message: e?.response?.data?.detail || e?.message || 'Failed to upload voicemail' });
        }
    };

    const previewVoicemail = async (campaignId: string) => {
        try {
            const res = await axios.get(`/api/outbound/campaigns/${campaignId}/voicemail/preview.wav`, { responseType: 'blob' });
            const url = URL.createObjectURL(res.data);
            const audio = new Audio(url);
            audio.onended = () => URL.revokeObjectURL(url);
            await audio.play();
        } catch (e: any) {
            setNotice({ type: 'error', message: e?.response?.data?.detail || e?.message || 'Failed to preview voicemail' });
        }
    };

    if (loading) {
        return (
            <div className="p-6">
                <div className="flex items-center gap-2 text-muted-foreground">
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    Loading outbound campaigns…
                </div>
            </div>
        );
    }

    return (
        <div className="p-6 space-y-6">
            <div className="flex items-start justify-between gap-4">
                <div>
                    <h1 className="text-3xl font-bold flex items-center gap-2">
                        <CalendarClock className="w-7 h-7" />
                        Call Scheduling
                    </h1>
                    <p className="text-sm text-muted-foreground">
                        Schedule outbound campaigns (MVP: single queue, skip-existing CSV import, voicemail drop).
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <button
                        className="inline-flex items-center gap-2 px-3 py-2 rounded-md bg-accent hover:bg-accent/80 text-sm"
                        onClick={async () => {
                            await refreshCampaigns();
                            if (selectedCampaignId) await refreshCampaignDetails(selectedCampaignId);
                        }}
                    >
                        <RefreshCw className="w-4 h-4" />
                        Refresh
                    </button>
                    <button
                        className="inline-flex items-center gap-2 px-3 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 text-sm"
                        onClick={() => setShowCreate(true)}
                    >
                        <Plus className="w-4 h-4" />
                        New Campaign
                    </button>
                </div>
            </div>

            {notice && (
                <div
                    className={`p-4 rounded-md border text-sm ${notice.type === 'error'
                        ? 'border-red-500/30 bg-red-500/10 text-red-500'
                        : notice.type === 'success'
                            ? 'border-green-500/30 bg-green-500/10 text-green-500'
                            : 'border-border bg-card/50 text-muted-foreground'
                        }`}
                >
                    <div className="flex items-start justify-between gap-3">
                        <div>{notice.message}</div>
                        <button
                            className="text-xs px-2 py-1 rounded hover:bg-accent/60"
                            onClick={() => setNotice(null)}
                            title="Dismiss"
                        >
                            ×
                        </button>
                    </div>
                </div>
            )}

            {error && (
                <div className="p-4 rounded-md border border-red-500/30 bg-red-500/10 text-red-500 text-sm">
                    {error}
                </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
                <div className="lg:col-span-1 border border-border rounded-lg bg-card/50 overflow-hidden">
                    <div className="p-3 border-b border-border flex items-center justify-between">
                        <div className="text-sm font-medium">Campaigns</div>
                        <div className="text-xs text-muted-foreground">{campaigns.length}</div>
                    </div>
                    <div className="max-h-[520px] overflow-y-auto">
                        {campaigns.length === 0 && (
                            <div className="p-4 text-sm text-muted-foreground">No campaigns yet.</div>
                        )}
                        {campaigns.map(c => (
                            <button
                                key={c.id}
                                onClick={() => setSelectedCampaignId(c.id)}
                                className={`w-full text-left px-3 py-3 border-b border-border/50 hover:bg-accent/40 transition ${selectedCampaignId === c.id ? 'bg-accent/60' : ''
                                    }`}
                            >
                                <div className="flex items-center justify-between gap-2">
                                    <div className="font-medium text-sm truncate">{c.name}</div>
                                    <StatusBadge status={c.status} />
                                </div>
                                <div className="text-xs text-muted-foreground mt-1 truncate">
                                    TZ: {c.timezone} · Window: {c.daily_window_start_local}–{c.daily_window_end_local}
                                </div>
                            </button>
                        ))}
                    </div>
                </div>

                <div className="lg:col-span-3 space-y-4">
                    {!selectedCampaign && (
                        <div className="p-6 border border-border rounded-lg bg-card/50 text-muted-foreground">
                            Select a campaign to view leads and attempts.
                        </div>
                    )}

                    {selectedCampaign && (
                        <>
                            <div className="border border-border rounded-lg bg-card/50 p-4 flex flex-col gap-3">
                                <div className="flex items-start justify-between gap-4">
	                                    <div>
	                                        <div className="flex items-center gap-2">
	                                            <div className="text-xl font-semibold">{selectedCampaign.name}</div>
	                                            <StatusBadge
	                                                status={selectedCampaign.status}
	                                                label={
	                                                    selectedCampaign.status === 'running' && windowInfo && !windowInfo.within
	                                                        ? 'running (outside window)'
	                                                        : undefined
	                                                }
	                                            />
	                                        </div>
	                                        <div className="text-sm text-muted-foreground">
	                                            Default context: <span className="font-mono">{selectedCampaign.default_context}</span> ·
	                                            Max concurrent: {selectedCampaign.max_concurrent} ·
	                                            Min interval: {selectedCampaign.min_interval_seconds_between_calls}s
	                                        </div>
	                                        <div className="text-xs text-muted-foreground mt-1">
	                                            Voicemail media: <span className="font-mono">{selectedCampaign.voicemail_drop_media_uri || '(not set)'}</span>
	                                            {selectedCampaign.status === 'running' && windowInfo && (
	                                                <span className="ml-3">
	                                                    Now ({selectedCampaign.timezone}): <span className="font-mono">{windowInfo.nowLocal}</span>
	                                                </span>
	                                            )}
	                                        </div>
	                                        {!selectedHasVoicemail && (
	                                            <div className="mt-2 text-xs text-yellow-500 flex items-center gap-2">
	                                                <AlertTriangle className="w-3 h-3" />
	                                                Upload voicemail media before starting (required for MVP).
	                                            </div>
	                                        )}
	                                    </div>
	                                    <div className="flex items-center gap-2">
	                                        <button
	                                            className="inline-flex items-center gap-2 px-3 py-2 rounded-md bg-accent hover:bg-accent/80 text-sm disabled:opacity-50 disabled:cursor-not-allowed"
	                                            onClick={openEdit}
	                                            disabled={selectedCampaign.status === 'running'}
	                                            title={selectedCampaign.status === 'running' ? 'Pause the campaign to edit' : 'Edit campaign'}
	                                        >
	                                            <Pencil className="w-4 h-4" />
	                                            Edit
	                                        </button>
	                                        <button
	                                            className="inline-flex items-center gap-2 px-3 py-2 rounded-md bg-accent hover:bg-accent/80 text-sm"
	                                            onClick={() => cloneCampaign(selectedCampaign.id)}
	                                        >
                                            <Copy className="w-4 h-4" />
                                            Clone
                                        </button>
                                        <label className="inline-flex items-center gap-2 px-3 py-2 rounded-md bg-accent hover:bg-accent/80 text-sm cursor-pointer">
                                            <Upload className="w-4 h-4" />
                                            Upload VM (.ulaw)
                                            <input
                                                type="file"
                                                accept=".ulaw"
                                                className="hidden"
                                                onChange={e => {
                                                    const f = e.target.files?.[0];
                                                    if (f) uploadVoicemail(selectedCampaign.id, f);
                                                    e.currentTarget.value = '';
                                                }}
                                            />
                                        </label>
	                                        <button
	                                            className="inline-flex items-center gap-2 px-3 py-2 rounded-md bg-accent hover:bg-accent/80 text-sm"
	                                            onClick={() => previewVoicemail(selectedCampaign.id)}
	                                            disabled={!selectedHasVoicemail}
	                                            title={!selectedHasVoicemail ? 'Upload voicemail media first' : 'Preview voicemail'}
	                                        >
	                                            <PhoneCall className="w-4 h-4" />
	                                            Preview VM
	                                        </button>
	                                        {selectedCampaign.status !== 'running' ? (
	                                            <button
	                                                className="inline-flex items-center gap-2 px-3 py-2 rounded-md bg-green-600 text-white hover:bg-green-600/90 text-sm disabled:opacity-50 disabled:cursor-not-allowed"
	                                                onClick={() => setStatus(selectedCampaign.id, 'running')}
	                                                disabled={!selectedHasVoicemail}
	                                                title={!selectedHasVoicemail ? 'Upload voicemail media first' : 'Start campaign'}
	                                            >
	                                                <Play className="w-4 h-4" />
	                                                Start
	                                            </button>
                                        ) : (
                                            <button
                                                className="inline-flex items-center gap-2 px-3 py-2 rounded-md bg-yellow-600 text-white hover:bg-yellow-600/90 text-sm"
                                                onClick={() => setStatus(selectedCampaign.id, 'paused')}
                                            >
                                                <Pause className="w-4 h-4" />
                                                Pause
                                            </button>
                                        )}
                                        <button
                                            className="inline-flex items-center gap-2 px-3 py-2 rounded-md bg-red-600 text-white hover:bg-red-600/90 text-sm"
                                            onClick={() => {
                                                const cancel = confirm('Stop campaign and cancel remaining pending leads?\n\nOK = stop + cancel pending\nCancel = stop only');
                                                setStatus(selectedCampaign.id, 'stopped', cancel);
                                            }}
                                        >
                                            <Square className="w-4 h-4" />
                                            Stop
                                        </button>
                                    </div>
                                </div>

                                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                                    <div className="p-3 rounded-md border border-border">
                                        <div className="text-xs text-muted-foreground">QUEUE</div>
                                        <div className="text-sm mt-1">
                                            Pending: {stats?.lead_states?.pending || 0} · Leased: {stats?.lead_states?.leased || 0}
                                        </div>
                                    </div>
                                    <div className="p-3 rounded-md border border-border">
                                        <div className="text-xs text-muted-foreground">IN PROGRESS</div>
                                        <div className="text-sm mt-1">
                                            Dialing: {stats?.lead_states?.dialing || 0} · In call: {stats?.lead_states?.in_progress || 0}
                                        </div>
                                    </div>
                                    <div className="p-3 rounded-md border border-border">
                                        <div className="text-xs text-muted-foreground">OUTCOMES</div>
                                        <div className="text-sm mt-1">
                                            VM: {stats?.attempt_outcomes?.voicemail_dropped || 0} · Human: {stats?.attempt_outcomes?.answered_human || 0} · Errors:{' '}
                                            {stats?.attempt_outcomes?.error || 0}
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <div className="border border-border rounded-lg bg-card/50 overflow-hidden">
                                <div className="flex items-center justify-between px-3 py-2 border-b border-border">
                                    <div className="flex gap-2">
                                        <button
                                            className={`px-3 py-1.5 rounded-md text-sm ${tab === 'campaigns' ? 'bg-accent' : 'hover:bg-accent/60'
                                                }`}
                                            onClick={() => setTab('campaigns')}
                                        >
                                            Campaigns
                                        </button>
                                        <button
                                            className={`px-3 py-1.5 rounded-md text-sm ${tab === 'leads' ? 'bg-accent' : 'hover:bg-accent/60'}`}
                                            onClick={() => setTab('leads')}
                                        >
                                            Leads
                                        </button>
                                        <button
                                            className={`px-3 py-1.5 rounded-md text-sm ${tab === 'attempts' ? 'bg-accent' : 'hover:bg-accent/60'}`}
                                            onClick={() => setTab('attempts')}
                                        >
                                            Attempts
                                        </button>
                                    </div>
                                    {tab === 'leads' && (
                                        <div className="flex items-center gap-2">
                                            <label className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 text-sm cursor-pointer">
                                                <Upload className="w-4 h-4" />
                                                Import CSV (skip existing)
                                                <input
                                                    type="file"
                                                    accept=".csv,text/csv"
                                                    className="hidden"
                                                    onChange={e => {
                                                        const f = e.target.files?.[0];
                                                        if (f) importLeads(selectedCampaign.id, f);
                                                        e.currentTarget.value = '';
                                                    }}
                                                />
                                            </label>
                                        </div>
                                    )}
                                </div>

                                {tab === 'campaigns' && (
                                    <div className="p-4 space-y-3">
                                        <div className="text-sm text-muted-foreground">
                                            Configure the campaign and press Start. Routing assumption: your FreePBX outbound routes handle dialing from extension 6789.
                                        </div>
                                        <button
                                            className="inline-flex items-center gap-2 px-3 py-2 rounded-md bg-accent hover:bg-accent/80 text-sm"
                                            onClick={() => setShowSetupGuide(v => !v)}
                                        >
                                            {showSetupGuide ? 'Hide Setup Guide' : 'Show Setup Guide'}
                                        </button>
                                        {showSetupGuide && (
                                            <div className="border border-border rounded-md bg-background p-3 text-sm">
                                                <div className="text-xs text-muted-foreground mb-2">
                                                    Add this to <span className="font-mono">/etc/asterisk/extensions_custom.conf</span> and reload the dialplan.
                                                </div>
                                                <pre className="text-xs overflow-x-auto p-3 rounded bg-muted/50 border border-border">
{`[aava-outbound-amd]
exten => s,1,NoOp(AAVA Outbound AMD hop)
 same => n,NoOp(Attempt=\${AAVA_ATTEMPT_ID} Campaign=\${AAVA_CAMPAIGN_ID} Lead=\${AAVA_LEAD_ID})
 same => n,AMD(\${AAVA_AMD_OPTS})
 same => n,NoOp(AMDSTATUS=\${AMDSTATUS} AMDCAUSE=\${AMDCAUSE})
 same => n,GotoIf($["\${AMDSTATUS}" = "HUMAN"]?human)
 same => n,GotoIf($["\${AMDSTATUS}" = "NOTSURE"]?machine)
 same => n(machine),WaitForSilence(1500,3)
 same => n,Stasis(asterisk-ai-voice-agent,outbound_amd,\${AAVA_ATTEMPT_ID},MACHINE,\${AMDCAUSE})
 same => n,Hangup()
 same => n(human),Stasis(asterisk-ai-voice-agent,outbound_amd,\${AAVA_ATTEMPT_ID},HUMAN,\${AMDCAUSE})
 same => n,Hangup()`}
                                                </pre>
                                                <div className="flex items-center gap-2 mt-3">
                                                    <button
                                                        className="inline-flex items-center gap-2 px-3 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 text-sm"
                                                        onClick={() => {
                                                            navigator.clipboard.writeText(
`[aava-outbound-amd]
exten => s,1,NoOp(AAVA Outbound AMD hop)
 same => n,NoOp(Attempt=\${AAVA_ATTEMPT_ID} Campaign=\${AAVA_CAMPAIGN_ID} Lead=\${AAVA_LEAD_ID})
 same => n,AMD(\${AAVA_AMD_OPTS})
 same => n,NoOp(AMDSTATUS=\${AMDSTATUS} AMDCAUSE=\${AMDCAUSE})
 same => n,GotoIf($[\"\\${AMDSTATUS}\" = \"HUMAN\"]?human)
 same => n,GotoIf($[\"\\${AMDSTATUS}\" = \"NOTSURE\"]?machine)
 same => n(machine),WaitForSilence(1500,3)
 same => n,Stasis(asterisk-ai-voice-agent,outbound_amd,\\${AAVA_ATTEMPT_ID},MACHINE,\\${AMDCAUSE})
 same => n,Hangup()
 same => n(human),Stasis(asterisk-ai-voice-agent,outbound_amd,\\${AAVA_ATTEMPT_ID},HUMAN,\\${AMDCAUSE})
 same => n,Hangup()`
                                                            );
                                                            setNotice({ type: 'success', message: 'Dialplan snippet copied to clipboard' });
                                                        }}
                                                    >
                                                        <FileDown className="w-4 h-4" />
                                                        Copy Snippet
                                                    </button>
                                                    <div className="text-xs text-muted-foreground">
                                                        Reload: <span className="font-mono">asterisk -rx "dialplan reload"</span> · Verify:{' '}
                                                        <span className="font-mono">asterisk -rx "dialplan show aava-outbound-amd"</span>
                                                    </div>
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                )}

                                {tab === 'leads' && (
                                    <div className="p-4">
                                        <div className="text-xs text-muted-foreground mb-2">
                                            Showing most recent 50 leads. Use CSV import to add more (default: skip duplicates).
                                        </div>
                                        <div className="overflow-x-auto">
                                            <table className="w-full text-sm">
                                                <thead>
                                                    <tr className="text-left text-xs text-muted-foreground border-b border-border">
                                                        <th className="py-2 pr-3">Phone</th>
                                                        <th className="py-2 pr-3">State</th>
                                                        <th className="py-2 pr-3">Attempts</th>
                                                        <th className="py-2 pr-3">Last outcome</th>
                                                        <th className="py-2 pr-3">Created</th>
                                                        <th className="py-2 pr-3"></th>
                                                    </tr>
                                                </thead>
                                                <tbody>
                                                    {leads.map(l => (
                                                        <tr key={l.id} className="border-b border-border/50">
                                                            <td className="py-2 pr-3 font-mono">{l.phone_number}</td>
                                                            <td className="py-2 pr-3">{l.state}</td>
                                                            <td className="py-2 pr-3">{l.attempt_count}</td>
                                                            <td className="py-2 pr-3">{l.last_outcome || '-'}</td>
                                                            <td className="py-2 pr-3">{formatUtc(l.created_at_utc)}</td>
                                                            <td className="py-2 pr-0 text-right">
                                                                <button
                                                                    className="inline-flex items-center gap-2 px-2 py-1 rounded-md hover:bg-accent text-xs"
                                                                    onClick={() => cancelLead(l.id)}
                                                                    title="Cancel lead"
                                                                >
                                                                    <Trash2 className="w-3 h-3" />
                                                                    Cancel
                                                                </button>
                                                            </td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    </div>
                                )}

                                {tab === 'attempts' && (
                                    <div className="p-4">
                                        <div className="text-xs text-muted-foreground mb-2">
                                            Showing most recent 50 attempts.
                                        </div>
                                        <div className="overflow-x-auto">
                                            <table className="w-full text-sm">
                                                <thead>
                                                    <tr className="text-left text-xs text-muted-foreground border-b border-border">
                                                        <th className="py-2 pr-3">Started</th>
                                                        <th className="py-2 pr-3">Phone</th>
                                                        <th className="py-2 pr-3">Outcome</th>
                                                        <th className="py-2 pr-3">AMD</th>
                                                        <th className="py-2 pr-3">Call history</th>
                                                        <th className="py-2 pr-3">Error</th>
                                                    </tr>
                                                </thead>
                                                <tbody>
                                                    {attempts.map(a => (
                                                        <tr key={a.id} className="border-b border-border/50">
                                                            <td className="py-2 pr-3">{formatUtc(a.started_at_utc)}</td>
                                                            <td className="py-2 pr-3 font-mono">{a.phone_number || '-'}</td>
                                                            <td className="py-2 pr-3">{a.outcome || '-'}</td>
                                                            <td className="py-2 pr-3">
                                                                {a.amd_status ? (
                                                                    <span className="font-mono">
                                                                        {a.amd_status}
                                                                        {a.amd_cause ? `/${a.amd_cause}` : ''}
                                                                    </span>
                                                                ) : (
                                                                    '-'
                                                                )}
                                                            </td>
                                                            <td className="py-2 pr-3">
                                                                {a.call_history_call_id ? (
                                                                    <button
                                                                        className="inline-flex items-center gap-2 px-2 py-1 rounded-md bg-accent hover:bg-accent/80 text-xs"
                                                                        onClick={() => {
                                                                            navigator.clipboard.writeText(a.call_history_call_id || '');
                                                                            alert('Call History record id copied to clipboard');
                                                                        }}
                                                                    >
                                                                        <FileDown className="w-3 h-3" />
                                                                        Copy ID
                                                                    </button>
                                                                ) : (
                                                                    '-'
                                                                )}
                                                            </td>
                                                            <td className="py-2 pr-3 text-xs text-muted-foreground">
                                                                {a.error_message ? (
                                                                    <span className="inline-flex items-center gap-1 text-red-500">
                                                                        <AlertTriangle className="w-3 h-3" />
                                                                        {a.error_message}
                                                                    </span>
                                                                ) : (
                                                                    '-'
                                                                )}
                                                            </td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    </div>
                                )}
                            </div>
                        </>
                    )}
                </div>
            </div>

            {showCreate && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-50">
                    <div className="w-full max-w-2xl border border-border rounded-lg bg-background p-5">
                        <div className="flex items-center justify-between mb-4">
                            <div className="text-lg font-semibold">Create Campaign</div>
                            <button className="text-sm text-muted-foreground hover:text-foreground" onClick={() => setShowCreate(false)}>
                                Close
                            </button>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <label className="text-xs text-muted-foreground">Name</label>
                                <input
                                    className="w-full px-3 py-2 rounded-md bg-background border border-border"
                                    value={createForm.name}
                                    onChange={e => setCreateForm({ ...createForm, name: e.target.value })}
                                    placeholder="Sales follow-up"
                                />
                            </div>
                            <div className="space-y-2">
                                <label className="text-xs text-muted-foreground">Timezone</label>
                                <input
                                    className="w-full px-3 py-2 rounded-md bg-background border border-border font-mono"
                                    value={createForm.timezone}
                                    onChange={e => setCreateForm({ ...createForm, timezone: e.target.value })}
                                    placeholder="UTC"
                                />
                            </div>
                            <div className="space-y-2">
                                <label className="text-xs text-muted-foreground">Daily Window Start (local)</label>
                                <input
                                    type="time"
                                    className="w-full px-3 py-2 rounded-md bg-background border border-border font-mono"
                                    value={createForm.daily_window_start_local}
                                    onChange={e => setCreateForm({ ...createForm, daily_window_start_local: e.target.value })}
                                />
                            </div>
                            <div className="space-y-2">
                                <label className="text-xs text-muted-foreground">Daily Window End (local)</label>
                                <input
                                    type="time"
                                    className="w-full px-3 py-2 rounded-md bg-background border border-border font-mono"
                                    value={createForm.daily_window_end_local}
                                    onChange={e => setCreateForm({ ...createForm, daily_window_end_local: e.target.value })}
                                />
                                {crossesMidnight && (
                                    <div className="text-xs text-yellow-500 flex items-center gap-2">
                                        <AlertTriangle className="w-3 h-3" />
                                        Crosses midnight (window runs across two days)
                                    </div>
                                )}
                            </div>
                            <div className="space-y-2">
                                <label className="text-xs text-muted-foreground">Max Concurrent</label>
                                <input
                                    type="number"
                                    min={1}
                                    max={5}
                                    className="w-full px-3 py-2 rounded-md bg-background border border-border"
                                    value={createForm.max_concurrent}
                                    onChange={e => setCreateForm({ ...createForm, max_concurrent: Number(e.target.value) })}
                                />
                            </div>
                            <div className="space-y-2">
                                <label className="text-xs text-muted-foreground">Min Interval Between Calls (sec)</label>
                                <input
                                    type="number"
                                    min={0}
                                    max={3600}
                                    className="w-full px-3 py-2 rounded-md bg-background border border-border"
                                    value={createForm.min_interval_seconds_between_calls}
                                    onChange={e => setCreateForm({ ...createForm, min_interval_seconds_between_calls: Number(e.target.value) })}
                                />
                            </div>
                            <div className="space-y-2 md:col-span-2">
                                <label className="text-xs text-muted-foreground">Default Context</label>
                                <input
                                    className="w-full px-3 py-2 rounded-md bg-background border border-border font-mono"
                                    value={createForm.default_context}
                                    onChange={e => setCreateForm({ ...createForm, default_context: e.target.value })}
                                    placeholder="default"
                                />
                            </div>
                        </div>
                        <div className="flex items-center justify-end gap-2 mt-5">
                            <button className="px-3 py-2 rounded-md bg-accent hover:bg-accent/80 text-sm" onClick={() => setShowCreate(false)}>
                                Cancel
                            </button>
                            <button
                                className="px-3 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 text-sm"
                                onClick={createCampaign}
                                disabled={!createForm.name.trim()}
                            >
                                Create
                            </button>
                        </div>
                        <div className="text-xs text-muted-foreground mt-3">
                            Voicemail drop media is required before a campaign can start. Upload a <span className="font-mono">.ulaw</span> file after creation.
                        </div>
                    </div>
                </div>
            )}

            {showEdit && selectedCampaign && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-50">
                    <div className="w-full max-w-2xl border border-border rounded-lg bg-background p-5">
                        <div className="flex items-center justify-between mb-4">
                            <div className="text-lg font-semibold">Edit Campaign</div>
                            <button className="text-sm text-muted-foreground hover:text-foreground" onClick={() => setShowEdit(false)}>
                                Close
                            </button>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <label className="text-xs text-muted-foreground">Name</label>
                                <input
                                    className="w-full px-3 py-2 rounded-md bg-background border border-border"
                                    value={editForm.name}
                                    onChange={e => setEditForm({ ...editForm, name: e.target.value })}
                                />
                            </div>
                            <div className="space-y-2">
                                <label className="text-xs text-muted-foreground">Timezone</label>
                                <input
                                    className="w-full px-3 py-2 rounded-md bg-background border border-border font-mono"
                                    value={editForm.timezone}
                                    onChange={e => setEditForm({ ...editForm, timezone: e.target.value })}
                                />
                            </div>
                            <div className="space-y-2">
                                <label className="text-xs text-muted-foreground">Daily Window Start (local)</label>
                                <input
                                    type="time"
                                    className="w-full px-3 py-2 rounded-md bg-background border border-border font-mono"
                                    value={editForm.daily_window_start_local}
                                    onChange={e => setEditForm({ ...editForm, daily_window_start_local: e.target.value })}
                                />
                            </div>
                            <div className="space-y-2">
                                <label className="text-xs text-muted-foreground">Daily Window End (local)</label>
                                <input
                                    type="time"
                                    className="w-full px-3 py-2 rounded-md bg-background border border-border font-mono"
                                    value={editForm.daily_window_end_local}
                                    onChange={e => setEditForm({ ...editForm, daily_window_end_local: e.target.value })}
                                />
                                {editCrossesMidnight && (
                                    <div className="text-xs text-yellow-500 flex items-center gap-2">
                                        <AlertTriangle className="w-3 h-3" />
                                        Crosses midnight (window runs across two days)
                                    </div>
                                )}
                            </div>
                            <div className="space-y-2">
                                <label className="text-xs text-muted-foreground">Max Concurrent</label>
                                <input
                                    type="number"
                                    min={1}
                                    max={5}
                                    className="w-full px-3 py-2 rounded-md bg-background border border-border"
                                    value={editForm.max_concurrent}
                                    onChange={e => setEditForm({ ...editForm, max_concurrent: Number(e.target.value) })}
                                />
                            </div>
                            <div className="space-y-2">
                                <label className="text-xs text-muted-foreground">Min Interval Between Calls (sec)</label>
                                <input
                                    type="number"
                                    min={0}
                                    max={3600}
                                    className="w-full px-3 py-2 rounded-md bg-background border border-border"
                                    value={editForm.min_interval_seconds_between_calls}
                                    onChange={e => setEditForm({ ...editForm, min_interval_seconds_between_calls: Number(e.target.value) })}
                                />
                            </div>
                            <div className="space-y-2 md:col-span-2">
                                <label className="text-xs text-muted-foreground">Default Context</label>
                                <input
                                    className="w-full px-3 py-2 rounded-md bg-background border border-border font-mono"
                                    value={editForm.default_context}
                                    onChange={e => setEditForm({ ...editForm, default_context: e.target.value })}
                                />
                            </div>
                        </div>
                        <div className="flex items-center justify-end gap-2 mt-5">
                            <button className="px-3 py-2 rounded-md bg-accent hover:bg-accent/80 text-sm" onClick={() => setShowEdit(false)}>
                                Cancel
                            </button>
                            <button
                                className="px-3 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 text-sm"
                                onClick={saveEdit}
                                disabled={!editForm.name.trim()}
                            >
                                Save
                            </button>
                        </div>
                        <div className="text-xs text-muted-foreground mt-3">
                            Edits are disabled while a campaign is running. Pause first, then edit and resume.
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

interface LeadImportResponse {
    accepted: number;
    rejected: number;
    duplicates: number;
    errors: Array<{ row_number: number; phone_number: string; error_reason: string }>;
    error_csv: string;
    error_csv_truncated: boolean;
}

export default CallSchedulingPage;
