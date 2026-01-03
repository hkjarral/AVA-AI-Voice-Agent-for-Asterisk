import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    AlertTriangle,
    Ban,
    CalendarClock,
    Clock,
    Copy,
    ExternalLink,
    FileDown,
    Pause,
    Pencil,
    PhoneCall,
    Play,
    Plus,
    RefreshCw,
    RotateCcw,
    Search,
    Square,
    Trash2,
    Upload
} from 'lucide-react';
import { Modal } from '../components/ui/Modal';

type CampaignStatus = 'draft' | 'running' | 'paused' | 'stopped' | 'archived' | 'completed';

type Notice = { type: 'success' | 'error' | 'info'; message: string };

type OutboundMeta = {
    server_timezone: string;
    iana_timezones: string[];
    server_now_iso?: string;
    default_amd_options?: Record<string, any>;
};

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
    voicemail_drop_enabled?: number | boolean;
    voicemail_drop_media_uri?: string | null;
    consent_enabled?: number | boolean;
    consent_media_uri?: string | null;
    consent_timeout_seconds?: number | null;
    amd_options?: Record<string, any>;
}

interface CampaignStats {
    lead_states?: Record<string, number>;
    attempt_outcomes?: Record<string, number>;
}

interface LeadRow {
    id: string;
    name?: string | null;
    phone_number: string;
    state: string;
    attempt_count: number;
    context_override?: string | null;
    last_outcome?: string | null;
    last_attempt_at_utc?: string | null;

    last_started_at_utc?: string | null;
    last_duration_seconds?: number | null;
    last_outcome_attempt?: string | null;
    last_amd_status?: string | null;
    last_amd_cause?: string | null;
    last_consent_dtmf?: string | null;
    last_context?: string | null;
    last_provider?: string | null;
    last_call_history_call_id?: string | null;
    last_error_message?: string | null;
}

const DEFAULT_AMD_OPTIONS = {
    initial_silence_ms: 2000,
    greeting_ms: 2000,
    after_greeting_silence_ms: 1000,
    total_analysis_time_ms: 5000
};

const buildDialplanSnippet = (opts: {
    stasisAppName: string;
    voicemailEnabled: boolean;
    consentEnabled: boolean;
}): string => {
    const stasis = opts.stasisAppName || 'asterisk-ai-voice-agent';
    const attemptVar = '${AAVA_ATTEMPT_ID}';
    const amdCauseVar = '${AMDCAUSE}';

    const lines: string[] = [
        '[aava-outbound-amd]',
        'exten => s,1,NoOp(AAVA Outbound AMD hop)',
        ' same => n,NoOp(Attempt=${AAVA_ATTEMPT_ID} Campaign=${AAVA_CAMPAIGN_ID} Lead=${AAVA_LEAD_ID})',
        ' same => n,ExecIf($["${AAVA_AMD_OPTS}" = ""]?Set(AAVA_AMD_OPTS=2000,2000,1000,5000))',
        ' same => n,AMD(${AAVA_AMD_OPTS})',
        ' same => n,NoOp(AMDSTATUS=${AMDSTATUS} AMDCAUSE=${AMDCAUSE})',
        ' ; Guardrails to reduce false MACHINE on silent humans',
        ' same => n,GotoIf($["${AMDCAUSE:0:7}" = "TOOLONG"]?human)',
        ' same => n,GotoIf($["${AMDCAUSE:0:14}" = "INITIALSILENCE"]?human)',
        ' same => n,GotoIf($["${AMDSTATUS}" = "HUMAN"]?human)',
        ' same => n,GotoIf($["${AMDSTATUS}" = "NOTSURE"]?machine)',
        ` ; Campaign: consent_enabled=${opts.consentEnabled ? 'true' : 'false'} voicemail_drop_enabled=${opts.voicemailEnabled ? 'true' : 'false'}`,
    ];

    if (!opts.voicemailEnabled) {
        lines.push(' ; NOTE: voicemail drop is disabled for this campaign; MACHINE/NOTSURE will record machine_detected (no voicemail playback).');
    }
    lines.push(
        ' same => n(machine),GotoIf($["${AAVA_VM_ENABLED}" = "1"]?vm:machine_done)',
        ' same => n(vm),WaitForSilence(1500,3,10)',
        ` same => n(machine_done),Stasis(${stasis},outbound_amd,${attemptVar},MACHINE,${amdCauseVar},,)`,
        ' same => n,Hangup()',
    );

    if (!opts.consentEnabled) {
        lines.push(' ; NOTE: consent gate is disabled for this campaign; HUMAN will connect to AI immediately.');
    }
    lines.push(
        ' ; HUMAN path: optional consent gate (DTMF 1 accept / 2 deny)',
        ' same => n(human),GotoIf($["${AAVA_CONSENT_ENABLED}" = "1"]?consent:human_done)',
        ' same => n(consent),Set(TIMEOUT(response)=${IF($["${AAVA_CONSENT_TIMEOUT}"=""]?5:${AAVA_CONSENT_TIMEOUT})})',
        ' same => n,NoOp(AAVA CONSENT enabled=${AAVA_CONSENT_ENABLED} timeout=${AAVA_CONSENT_TIMEOUT} playback=${AAVA_CONSENT_PLAYBACK})',
        ' ; IMPORTANT: Use Read() with a prompt so DTMF is captured while the consent message plays.',
        ' ; If we Playback() then Read(), DTMF pressed during Playback is consumed and Read() times out.',
        ' same => n,Read(AAVA_CONSENT_DTMF,${AAVA_CONSENT_PLAYBACK},1)',
        ' same => n,NoOp(AAVA CONSENT dtmf=${AAVA_CONSENT_DTMF})',
        ' same => n,GotoIf($["${AAVA_CONSENT_DTMF}" = "1"]?human_ok)',
        ' same => n,GotoIf($["${AAVA_CONSENT_DTMF}" = "2"]?human_denied)',
        ` same => n(human_timeout),Stasis(${stasis},outbound_amd,${attemptVar},HUMAN,${amdCauseVar},,timeout)`,
        ' same => n,Hangup()',
        ` same => n(human_denied),Stasis(${stasis},outbound_amd,${attemptVar},HUMAN,${amdCauseVar},2,denied)`,
        ' same => n,Hangup()',
        ` same => n(human_ok),Stasis(${stasis},outbound_amd,${attemptVar},HUMAN,${amdCauseVar},1,accepted)`,
        ' same => n,Hangup()',
        ` same => n(human_done),Stasis(${stasis},outbound_amd,${attemptVar},HUMAN,${amdCauseVar},,skipped)`,
        ' same => n,Hangup()',
    );

    return lines.join('\n');
};

type DialplanLineGroup = 'consent' | 'voicemail' | 'meta';

const classifyDialplanLine = (line: string): DialplanLineGroup | null => {
    const l = line || '';
    if (l.includes('HARD-DISABLE TEMPLATE') || l.includes('Campaign:') || l.includes('; NOTE:')) return 'meta';
    if (
        l.includes('AAVA_CONSENT') ||
        l.includes('(consent)') ||
        l.includes('CONSENT') ||
        l.includes('(human_timeout)') ||
        l.includes('(human_denied)') ||
        l.includes('(human_ok)') ||
        l.includes('(human_done)')
    )
        return 'consent';
    if (l.includes('AAVA_VM') || l.includes('(vm)') || l.includes('WaitForSilence')) return 'voicemail';
    return null;
};

const _isCommented = (line: string): boolean => /^\s*;/.test(line || '');

const _commentOutLine = (line: string): string => {
    if (!line) return ';';
    if (_isCommented(line)) return line;
    if (/^\s*\[/.test(line)) return line;
    return `; ${line}`;
};

const copyTextToClipboard = async (text: string): Promise<boolean> => {
    try {
        if (window.isSecureContext && navigator?.clipboard?.writeText) {
            await navigator.clipboard.writeText(text);
            return true;
        }
    } catch {
        // ignore and try fallback
    }
    try {
        const el = document.createElement('textarea');
        el.value = text;
        el.setAttribute('readonly', 'true');
        el.style.position = 'fixed';
        el.style.left = '-9999px';
        el.style.top = '0';
        document.body.appendChild(el);
        el.focus();
        el.select();
        el.setSelectionRange(0, el.value.length);
        const ok = document.execCommand('copy');
        document.body.removeChild(el);
        return ok;
    } catch {
        return false;
    }
};

const withinDailyWindow = (nowHHMM: string, startHHMM: string, endHHMM: string): boolean => {
    if (!nowHHMM || !startHHMM || !endHHMM) return true;
    const crossesMidnight = endHHMM < startHHMM;
    if (!crossesMidnight) return nowHHMM >= startHHMM && nowHHMM <= endHHMM;
    return nowHHMM >= startHHMM || nowHHMM <= endHHMM;
};

const timeStringInZone = (timeZone: string, now: Date): string | null => {
    try {
        const parts = new Intl.DateTimeFormat('en-US', {
            timeZone,
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
        }).formatToParts(now);
        const hour = parts.find(p => p.type === 'hour')?.value;
        const minute = parts.find(p => p.type === 'minute')?.value;
        if (!hour || !minute) return null;
        return `${hour}:${minute}`;
    } catch {
        return null;
    }
};

const CallSchedulingPage = () => {
    const [meta, setMeta] = useState<OutboundMeta | null>(null);
    const [serverOffsetMs, setServerOffsetMs] = useState(0);
    const [clockTick, setClockTick] = useState(0);

    const [campaigns, setCampaigns] = useState<OutboundCampaign[]>([]);
    const [selectedCampaignId, setSelectedCampaignId] = useState<string | null>(null);
    const [stats, setStats] = useState<CampaignStats | null>(null);

    const [leads, setLeads] = useState<LeadRow[]>([]);
    const [leadPage, setLeadPage] = useState(1);
    const [leadTotalPages, setLeadTotalPages] = useState(1);
    const [leadStateFilter, setLeadStateFilter] = useState('');
    const [leadQuery, setLeadQuery] = useState('');

    const [showArchived, setShowArchived] = useState(false);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<Notice | null>(null);

    const [showCampaignModal, setShowCampaignModal] = useState(false);
    const [campaignModalMode, setCampaignModalMode] = useState<'create' | 'edit'>('create');
    const [campaignModalStep, setCampaignModalStep] = useState<'settings' | 'leads' | 'recordings' | 'setup' | 'advanced'>('settings');
    const [dialplanNeedsReview, setDialplanNeedsReview] = useState(false);

    const [pendingImportFile, setPendingImportFile] = useState<File | null>(null);
    const [pendingVoicemailFile, setPendingVoicemailFile] = useState<File | null>(null);
    const [pendingConsentFile, setPendingConsentFile] = useState<File | null>(null);

    const [recycleLeadRow, setRecycleLeadRow] = useState<LeadRow | null>(null);
    const [recycleMode, setRecycleMode] = useState<'redial' | 'reset'>('redial');

    const [callHistoryModalId, setCallHistoryModalId] = useState<string | null>(null);
    const [callHistoryLoading, setCallHistoryLoading] = useState(false);
    const [callHistoryError, setCallHistoryError] = useState<string | null>(null);
    const [callHistoryRecord, setCallHistoryRecord] = useState<any | null>(null);

    const selectedCampaign = useMemo(
        () => campaigns.find(c => c.id === selectedCampaignId) || null,
        [campaigns, selectedCampaignId]
    );

    const ianaTimezones = useMemo(() => meta?.iana_timezones || [], [meta]);
    const defaultAmdOptions = useMemo(() => {
        const raw = meta?.default_amd_options;
        if (raw && typeof raw === 'object') return { ...DEFAULT_AMD_OPTIONS, ...raw };
        return { ...DEFAULT_AMD_OPTIONS };
    }, [meta?.default_amd_options]);
    const isTimezoneValid = (tz: string) => {
        const t = (tz || '').trim();
        if (!t) return false;
        if (t.toUpperCase() === 'UTC') return true;
        return ianaTimezones.includes(t);
    };

    const serverNow = useMemo(() => new Date(Date.now() + serverOffsetMs), [serverOffsetMs, clockTick]);
    const serverTz = (meta?.server_timezone || 'UTC').trim() || 'UTC';
    const selectedTz = (selectedCampaign?.timezone || 'UTC').trim() || 'UTC';
    const formatClock = (d: Date, tz: string) => {
        try {
            return new Intl.DateTimeFormat('en-US', {
                timeZone: tz,
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            }).format(d);
        } catch {
            return d.toLocaleTimeString();
        }
    };

    const windowInfo = useMemo(() => {
        if (!selectedCampaign) return null;
        const nowLocal = timeStringInZone(selectedCampaign.timezone || 'UTC', serverNow);
        if (!nowLocal) return null;
        const within = withinDailyWindow(nowLocal, selectedCampaign.daily_window_start_local, selectedCampaign.daily_window_end_local);
        return { nowLocal, within };
    }, [selectedCampaign, serverNow]);

    const selectedVoicemailEnabled = Boolean((selectedCampaign as any)?.voicemail_drop_enabled ?? true);
    const selectedConsentEnabled = Boolean((selectedCampaign as any)?.consent_enabled ?? false);

    const statusInfo = useMemo(() => {
        if (!selectedCampaign) return null;
        const within = windowInfo?.within ?? true;
        const missing: string[] = [];
        if (selectedVoicemailEnabled && !(selectedCampaign.voicemail_drop_media_uri || '').trim()) missing.push('Voicemail recording missing');
        if (selectedConsentEnabled && !(((selectedCampaign as any).consent_media_uri || '') as string).trim()) missing.push('Consent recording missing');
        const pending = stats?.lead_states?.pending || 0;
        const canceled = stats?.lead_states?.canceled || 0;
        if (pending <= 0) missing.push(`No pending leads${canceled ? ` (canceled: ${canceled})` : ''}`);
        return { within, missing };
    }, [selectedCampaign, windowInfo?.within, selectedVoicemailEnabled, selectedConsentEnabled]);

    const [createForm, setCreateForm] = useState({
        name: '',
        timezone: 'UTC',
        daily_window_start_local: '09:00',
        daily_window_end_local: '17:00',
        max_concurrent: 1,
        min_interval_seconds_between_calls: 5,
        default_context: 'default',
        voicemail_drop_enabled: true,
        consent_enabled: false,
        consent_timeout_seconds: 5,
        amd_options: { ...DEFAULT_AMD_OPTIONS } as Record<string, any>
    });

    const [editForm, setEditForm] = useState({ ...createForm });

    const modalTimezone = (campaignModalMode === 'create' ? createForm.timezone : editForm.timezone) || 'UTC';
    const modalTimezoneValid = isTimezoneValid(modalTimezone);
    const modalForm = campaignModalMode === 'create' ? createForm : editForm;
    const modalConsentEnabled = Boolean((modalForm as any)?.consent_enabled);
    const modalVoicemailEnabled = Boolean((modalForm as any)?.voicemail_drop_enabled);
    const modalDialplanSnippet = buildDialplanSnippet({
        stasisAppName: 'asterisk-ai-voice-agent',
        consentEnabled: modalConsentEnabled,
        voicemailEnabled: modalVoicemailEnabled,
    });
    const modalDialplanSnippetForDisplayAndCopy = useMemo(() => {
        const lines = modalDialplanSnippet.split('\n');
        return lines
            .map(line => {
                const group = classifyDialplanLine(line);
                const isInactive =
                    (group === 'consent' && !modalConsentEnabled) || (group === 'voicemail' && !modalVoicemailEnabled);
                if (!isInactive) return line;

                if (group === 'consent' && !modalConsentEnabled) {
                    if (line.includes(' same => n(human),')) return ' same => n(human),Goto(human_done)';
                    if (line.includes(' same => n(human_done),')) return line;
                }

                if (group === 'voicemail' && !modalVoicemailEnabled) {
                    if (line.includes(' same => n(machine),')) return ' same => n(machine),Goto(machine_done)';
                }

                return _commentOutLine(line);
            })
            .join('\n');
    }, [modalDialplanSnippet, modalConsentEnabled, modalVoicemailEnabled]);

    useEffect(() => {
        const id = setInterval(() => setClockTick(t => t + 1), 1000);
        return () => clearInterval(id);
    }, []);

    const refreshMeta = async () => {
        const res = await axios.get('/api/outbound/meta');
        setMeta(res.data || null);
        try {
            const serverNowIso = res.data?.server_now_iso;
            if (serverNowIso) {
                const serverNowUtc = new Date(serverNowIso).getTime();
                const clientNowUtc = Date.now();
                setServerOffsetMs(serverNowUtc - clientNowUtc);
            }
        } catch {
            // ignore
        }
        const serverTzName = (res.data?.server_timezone || '').trim();
        if (serverTzName) {
            setCreateForm(prev => ((prev.timezone || '').trim() && prev.timezone !== 'UTC' ? prev : { ...prev, timezone: serverTzName }));
        }
        const amdDefaults = res.data?.default_amd_options;
        if (amdDefaults && typeof amdDefaults === 'object') {
            setCreateForm(prev => {
                const keys = Object.keys(prev.amd_options || {});
                if (keys.length) return prev;
                return { ...prev, amd_options: { ...DEFAULT_AMD_OPTIONS, ...amdDefaults } };
            });
        }
    };

    const refreshCampaigns = async () => {
        const res = await axios.get('/api/outbound/campaigns', { params: { include_archived: showArchived } });
        const list = res.data || [];
        setCampaigns(list);
        if (!list.length) {
            setSelectedCampaignId(null);
            return;
        }
        if (!selectedCampaignId || !list.some((c: OutboundCampaign) => c.id === selectedCampaignId)) {
            setSelectedCampaignId(list[0].id);
        }
    };

    const refreshCampaignDetails = async (campaignId: string) => {
        const [statsRes, leadsRes] = await Promise.all([
            axios.get(`/api/outbound/campaigns/${campaignId}/stats`),
            axios.get(`/api/outbound/campaigns/${campaignId}/leads`, {
                params: {
                    page: leadPage,
                    page_size: 50,
                    state: leadStateFilter || undefined,
                    q: leadQuery || undefined
                }
            })
        ]);
        setStats(statsRes.data || {});
        setLeads(leadsRes.data?.leads || []);
        setLeadTotalPages(leadsRes.data?.total_pages || 1);
    };

    useEffect(() => {
        let mounted = true;
        (async () => {
            try {
                setLoading(true);
                await refreshMeta();
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
    }, [showArchived]);

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
    }, [selectedCampaignId, leadPage, leadStateFilter, leadQuery]);

    const openCreate = () => {
        setCampaignModalMode('create');
        setCampaignModalStep('settings');
        setDialplanNeedsReview(false);
        setShowCampaignModal(true);
    };

    const openEdit = () => {
        if (!selectedCampaign) return;
        setCampaignModalMode('edit');
        setCampaignModalStep('settings');
        setDialplanNeedsReview(false);
        setEditForm({
            name: selectedCampaign.name || '',
            timezone: selectedCampaign.timezone || 'UTC',
            daily_window_start_local: selectedCampaign.daily_window_start_local || '09:00',
            daily_window_end_local: selectedCampaign.daily_window_end_local || '17:00',
            max_concurrent: selectedCampaign.max_concurrent || 1,
            min_interval_seconds_between_calls: selectedCampaign.min_interval_seconds_between_calls || 5,
            default_context: selectedCampaign.default_context || 'default',
            voicemail_drop_enabled: Boolean((selectedCampaign as any).voicemail_drop_enabled ?? true),
            consent_enabled: Boolean((selectedCampaign as any).consent_enabled ?? false),
            consent_timeout_seconds: Number((selectedCampaign as any).consent_timeout_seconds ?? 5),
            amd_options:
                Object.keys(((selectedCampaign as any).amd_options || {}) as Record<string, any>).length > 0
                    ? ((selectedCampaign as any).amd_options || {})
                    : { ...defaultAmdOptions }
        });
        setShowCampaignModal(true);
    };

    const createCampaign = async () => {
        try {
            const res = await axios.post('/api/outbound/campaigns', createForm);
            const campaignId = res.data.id as string;

            if (createForm.consent_enabled && pendingConsentFile) {
                await uploadMedia(campaignId, 'consent', pendingConsentFile);
            }
            if (createForm.voicemail_drop_enabled && pendingVoicemailFile) {
                await uploadMedia(campaignId, 'voicemail', pendingVoicemailFile);
            }
            if (pendingImportFile) {
                await importLeads(campaignId, pendingImportFile);
            }

            await refreshCampaigns();
            setSelectedCampaignId(campaignId);
            setShowCampaignModal(false);
            setCampaignModalStep('settings');
            setPendingImportFile(null);
            setPendingVoicemailFile(null);
            setPendingConsentFile(null);
            setNotice({ type: 'success', message: 'Campaign created' });
            setCreateForm({
                name: '',
                timezone: serverTz,
                daily_window_start_local: '09:00',
                daily_window_end_local: '17:00',
                max_concurrent: 1,
                min_interval_seconds_between_calls: 5,
                default_context: 'default',
                voicemail_drop_enabled: true,
                consent_enabled: false,
                consent_timeout_seconds: 5,
                amd_options: {}
            });
        } catch (e: any) {
            setNotice({ type: 'error', message: e?.response?.data?.detail || e?.message || 'Failed to create campaign' });
        }
    };

    const saveEdit = async () => {
        if (!selectedCampaign) return;
        try {
            await axios.patch(`/api/outbound/campaigns/${selectedCampaign.id}`, editForm);
            await refreshCampaigns();
            await refreshCampaignDetails(selectedCampaign.id);
            setNotice({ type: 'success', message: 'Campaign updated' });
            setShowCampaignModal(false);
            setCampaignModalStep('settings');
        } catch (e: any) {
            setNotice({ type: 'error', message: e?.response?.data?.detail || e?.message || 'Failed to update campaign' });
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

    const archiveCampaign = async (campaignId: string) => {
        try {
            await axios.post(`/api/outbound/campaigns/${campaignId}/archive`);
            await refreshCampaigns();
            if (selectedCampaignId === campaignId) setSelectedCampaignId(null);
            setNotice({ type: 'success', message: 'Campaign archived' });
        } catch (e: any) {
            setNotice({ type: 'error', message: e?.response?.data?.detail || e?.message || 'Failed to archive campaign' });
        }
    };

    const deleteCampaign = async (campaignId: string) => {
        try {
            await axios.delete(`/api/outbound/campaigns/${campaignId}`);
            await refreshCampaigns();
            if (selectedCampaignId === campaignId) setSelectedCampaignId(null);
            setNotice({ type: 'success', message: 'Campaign deleted' });
        } catch (e: any) {
            setNotice({ type: 'error', message: e?.response?.data?.detail || e?.message || 'Failed to delete campaign' });
        }
    };

    const importLeads = async (campaignId: string, file: File) => {
        const formData = new FormData();
        formData.append('file', file);
        try {
            const res = await axios.post(`/api/outbound/campaigns/${campaignId}/leads/import?skip_existing=true`, formData, {
                headers: { 'Content-Type': 'multipart/form-data' }
            });
            const data = res.data;
            if (data?.rejected > 0 && data?.error_csv) {
                const blob = new Blob([data.error_csv], { type: 'text/csv;charset=utf-8' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `outbound_import_errors_${new Date().toISOString().slice(0, 19)}.csv`;
                a.click();
                URL.revokeObjectURL(url);
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

    const uploadMedia = async (campaignId: string, kind: 'voicemail' | 'consent', file: File) => {
        const formData = new FormData();
        formData.append('file', file);
        try {
            await axios.post(`/api/outbound/campaigns/${campaignId}/${kind}/upload`, formData, {
                headers: { 'Content-Type': 'multipart/form-data' }
            });
            await refreshCampaigns();
            await refreshCampaignDetails(campaignId);
            setNotice({ type: 'success', message: `${kind === 'voicemail' ? 'Voicemail' : 'Consent'} recording uploaded` });
        } catch (e: any) {
            setNotice({ type: 'error', message: e?.response?.data?.detail || e?.message || `Failed to upload ${kind} recording` });
        }
    };

    const previewMedia = async (campaignId: string, kind: 'voicemail' | 'consent') => {
        try {
            const res = await axios.get(`/api/outbound/campaigns/${campaignId}/${kind}/preview.wav`, { responseType: 'blob' });
            const url = URL.createObjectURL(res.data);
            const audio = new Audio(url);
            audio.onended = () => URL.revokeObjectURL(url);
            await audio.play();
        } catch (e: any) {
            setNotice({ type: 'error', message: e?.response?.data?.detail || e?.message || `Failed to preview ${kind}` });
        }
    };

    const downloadSampleCsv = async () => {
        try {
            const res = await axios.get('/api/outbound/sample.csv', { responseType: 'blob' });
            const url = URL.createObjectURL(res.data);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'outbound_sample_leads.csv';
            a.click();
            URL.revokeObjectURL(url);
        } catch (e: any) {
            setNotice({ type: 'error', message: e?.response?.data?.detail || e?.message || 'Failed to download sample CSV' });
        }
    };

    const ignoreLead = async (leadId: string) => {
        try {
            await axios.post(`/api/outbound/leads/${leadId}/ignore`);
            if (selectedCampaignId) await refreshCampaignDetails(selectedCampaignId);
            setNotice({ type: 'success', message: 'Lead ignored (canceled). Use Recycle to re-dial.' });
        } catch (e: any) {
            setNotice({ type: 'error', message: e?.response?.data?.detail || e?.message || 'Failed to ignore lead' });
        }
    };

    const recycleLead = async (leadId: string, mode: 'redial' | 'reset') => {
        try {
            await axios.post(`/api/outbound/leads/${leadId}/recycle`, { mode });
            if (selectedCampaignId) await refreshCampaignDetails(selectedCampaignId);
            setNotice({ type: 'success', message: mode === 'reset' ? 'Lead reset and re-queued' : 'Lead re-queued for re-dial' });
        } catch (e: any) {
            setNotice({ type: 'error', message: e?.response?.data?.detail || e?.message || 'Failed to recycle lead' });
        }
    };

    const deleteLead = async (leadId: string) => {
        if (!confirm('Delete this lead and all its attempts? This cannot be undone.')) return;
        try {
            await axios.delete(`/api/outbound/leads/${leadId}`);
            if (selectedCampaignId) await refreshCampaignDetails(selectedCampaignId);
            setNotice({ type: 'success', message: 'Lead deleted' });
        } catch (e: any) {
            setNotice({ type: 'error', message: e?.response?.data?.detail || e?.message || 'Failed to delete lead' });
        }
    };

    const openCallHistory = async (callRecordId: string) => {
        setCallHistoryModalId(callRecordId);
        setCallHistoryLoading(true);
        setCallHistoryError(null);
        setCallHistoryRecord(null);
        try {
            const res = await axios.get(`/api/calls/${callRecordId}`);
            setCallHistoryRecord(res.data);
        } catch (e: any) {
            setCallHistoryError(e?.response?.data?.detail || e?.message || 'Failed to load call history');
        } finally {
            setCallHistoryLoading(false);
        }
    };

    const renderLeadTime = (iso?: string | null) => {
        if (!iso || !selectedCampaign) return '-';
        try {
            return new Intl.DateTimeFormat('en-US', {
                timeZone: selectedCampaign.timezone || 'UTC',
                year: 'numeric',
                month: 'numeric',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            }).format(new Date(iso));
        } catch {
            return iso;
        }
    };

    const renderDuration = (seconds?: number | null) => {
        if (seconds == null) return '-';
        const s = Math.max(0, Math.floor(seconds));
        if (s < 60) return `${s}s`;
        const m = Math.floor(s / 60);
        const r = s % 60;
        return `${m}m ${r}s`;
    };

    if (loading) {
        return (
            <div className="p-6">
                <div className="flex items-center gap-2 text-muted-foreground">
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    Loading call scheduling…
                </div>
            </div>
        );
    }

    return (
        <div className="p-6 space-y-6">
            <datalist id="aava-iana-timezones">
                {ianaTimezones.map(tz => (
                    <option key={tz} value={tz} />
                ))}
            </datalist>

            <div className="flex items-start justify-between gap-4">
                <div>
                    <h1 className="text-3xl font-bold flex items-center gap-2">
                        <CalendarClock className="w-7 h-7" />
                        Call Scheduling
                    </h1>
                    <p className="text-sm text-muted-foreground">
                        Campaign scheduler (MVP): lead list + optional voicemail drop + optional consent gate.
                    </p>
                </div>
                <div className="flex flex-col items-end gap-2">
                    <div className="flex items-center gap-2">
                        <button
                            onClick={async () => {
                                await refreshCampaigns();
                                if (selectedCampaignId) await refreshCampaignDetails(selectedCampaignId);
                            }}
                            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border hover:bg-muted text-sm"
                        >
                            <RefreshCw className="w-4 h-4" />
                            Refresh
                        </button>
                        <button
                            onClick={openCreate}
                            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 text-sm"
                        >
                            <Plus className="w-4 h-4" />
                            New Campaign
                        </button>
                    </div>
                    <div className="flex items-center gap-3 text-xs text-muted-foreground">
                        <span className="inline-flex items-center gap-1">
                            <Clock className="w-3 h-3" />
                            Server: <span className="font-mono text-foreground">{formatClock(serverNow, serverTz)}</span>{' '}
                            <span className="font-mono">{serverTz}</span>
                        </span>
                        {selectedCampaign && (
                            <span className="inline-flex items-center gap-1">
                                <Clock className="w-3 h-3" />
                                Campaign: <span className="font-mono text-foreground">{formatClock(serverNow, selectedTz)}</span>{' '}
                                <span className="font-mono">{selectedTz}</span>
                            </span>
                        )}
                    </div>
                </div>
            </div>

            {notice && (
                <div
                    className={`rounded-lg border p-3 text-sm ${
                        notice.type === 'error'
                            ? 'border-red-500/30 bg-red-500/10 text-red-600'
                            : notice.type === 'success'
                              ? 'border-green-500/30 bg-green-500/10 text-green-700'
                              : 'border-border bg-muted/30 text-foreground'
                    }`}
                >
                    <div className="flex items-center justify-between gap-3">
                        <span>{notice.message}</span>
                        <button className="text-muted-foreground hover:text-foreground" onClick={() => setNotice(null)}>
                            ×
                        </button>
                    </div>
                </div>
            )}

            {error && <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-600">{error}</div>}

            <div className="grid grid-cols-[320px_1fr] gap-6">
                <div className="border rounded-lg p-3 bg-card">
                    <div className="flex items-center justify-between mb-2">
                        <div className="font-semibold">Campaigns</div>
                        <label className="flex items-center gap-2 text-xs text-muted-foreground">
                            <input type="checkbox" checked={showArchived} onChange={e => setShowArchived(e.target.checked)} />
                            Show archived
                        </label>
                    </div>
                    <div className="max-h-[520px] overflow-auto">
                        {campaigns.length === 0 ? (
                            <div className="text-sm text-muted-foreground py-6 text-center">No campaigns yet.</div>
                        ) : (
                            <div className="space-y-2">
                                {campaigns.map(c => (
                                    <button
                                        key={c.id}
                                        onClick={() => {
                                            setSelectedCampaignId(c.id);
                                            setLeadPage(1);
                                        }}
                                        className={`w-full text-left rounded-lg border p-3 hover:bg-muted/40 transition-colors ${
                                            selectedCampaignId === c.id ? 'border-primary/40 bg-primary/5' : 'border-border'
                                        }`}
                                    >
                                        <div className="flex items-center justify-between gap-2">
                                            <div className="font-medium truncate">{c.name}</div>
                                            <span className="text-xs text-muted-foreground">{c.status}</span>
                                        </div>
                                        <div className="text-xs text-muted-foreground mt-1">
                                            TZ: <span className="font-mono">{c.timezone}</span> · Window: {c.daily_window_start_local}–{c.daily_window_end_local}
                                        </div>
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                </div>

                <div className="border rounded-lg p-4 bg-card">
                    {!selectedCampaign ? (
                        <div className="text-sm text-muted-foreground py-10 text-center">Select a campaign to view details.</div>
                    ) : (
                        <>
                            <div className="flex items-start justify-between gap-4">
                                <div>
                                    <div className="flex items-center gap-2">
                                        <div className="text-xl font-semibold">{selectedCampaign.name}</div>
                                        <span className="text-xs px-2 py-0.5 rounded border">{selectedCampaign.status}</span>
                                    </div>
                                    <div className="text-sm text-muted-foreground mt-1">
                                        Default context: <span className="font-mono">{selectedCampaign.default_context}</span> · Max concurrent:{' '}
                                        {selectedCampaign.max_concurrent} · Min interval: {selectedCampaign.min_interval_seconds_between_calls}s
                                    </div>
                                    <div className="text-xs text-muted-foreground mt-1">
                                        TZ: <span className="font-mono">{selectedCampaign.timezone}</span> · Window:{' '}
                                        {selectedCampaign.daily_window_start_local}–{selectedCampaign.daily_window_end_local}
                                        {windowInfo && (
                                            <span>
                                                {' '}
                                                · Now: <span className="font-mono">{windowInfo.nowLocal}</span>{' '}
                                                {windowInfo.within ? <span className="text-green-600">within</span> : <span className="text-yellow-600">outside</span>}
                                            </span>
                                        )}
                                    </div>
                                </div>
                                <div className="flex flex-wrap items-center justify-end gap-2">
                                    <button className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border hover:bg-muted text-sm" onClick={openEdit}>
                                        <Pencil className="w-4 h-4" /> Edit
                                    </button>
                                    <button
                                        className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border hover:bg-muted text-sm"
                                        onClick={() => cloneCampaign(selectedCampaign.id)}
                                    >
                                        <Copy className="w-4 h-4" /> Clone
                                    </button>
                                    {selectedCampaign.status !== 'archived' && (
                                        <button
                                            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border hover:bg-muted text-sm"
                                            onClick={() => archiveCampaign(selectedCampaign.id)}
                                        >
                                            <Ban className="w-4 h-4" /> Archive
                                        </button>
                                    )}
                                    <button
                                        className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-red-500/30 text-red-600 hover:bg-red-500/10 text-sm disabled:opacity-50"
                                        disabled={selectedCampaign.status === 'running'}
                                        onClick={() => {
                                            if (!confirm('Permanently delete this campaign and all leads/attempts? This cannot be undone.')) return;
                                            deleteCampaign(selectedCampaign.id);
                                        }}
                                    >
                                        <Trash2 className="w-4 h-4" /> Delete
                                    </button>
                                    {selectedCampaign.status !== 'running' ? (
                                        <button
                                            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 text-sm"
                                            disabled={(stats?.lead_states?.pending || 0) <= 0}
                                            title={
                                                (stats?.lead_states?.pending || 0) <= 0
                                                    ? 'No pending leads to dial. Recycle/uncancel leads first.'
                                                    : ''
                                            }
                                            onClick={() => setStatus(selectedCampaign.id, 'running')}
                                        >
                                            <Play className="w-4 h-4" /> Start
                                        </button>
                                    ) : (
                                        <>
                                            <button
                                                className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-yellow-500 text-black hover:bg-yellow-500/90 text-sm"
                                                onClick={() => setStatus(selectedCampaign.id, 'paused')}
                                            >
                                                <Pause className="w-4 h-4" /> Pause
                                            </button>
                                            <button
                                                className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-red-500 text-white hover:bg-red-500/90 text-sm"
                                                onClick={() => {
                                                    const cancel = confirm(
                                                        'Stop campaign. Cancel all remaining pending leads?\n\nOK = Stop + cancel pending (non-resumable)\nCancel = Stop only (resumable)'
                                                    );
                                                    setStatus(selectedCampaign.id, 'stopped', cancel);
                                                }}
                                            >
                                                <Square className="w-4 h-4" /> Stop
                                            </button>
                                        </>
                                    )}
                                </div>
                            </div>

                            <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mt-4">
                                <div className="border rounded-lg p-3">
                                    <div className="text-xs text-muted-foreground">QUEUE</div>
                                    <div className="text-sm mt-1">
                                        Pending: <span className="font-medium">{stats?.lead_states?.pending || 0}</span> · Leased:{' '}
                                        <span className="font-medium">{stats?.lead_states?.leased || 0}</span>
                                    </div>
                                </div>
                                <div className="border rounded-lg p-3">
                                    <div className="text-xs text-muted-foreground">IN PROGRESS</div>
                                    <div className="text-sm mt-1">
                                        Dialing: <span className="font-medium">{stats?.lead_states?.dialing || 0}</span> · In call:{' '}
                                        <span className="font-medium">{stats?.lead_states?.in_progress || 0}</span>
                                    </div>
                                </div>
                                <div className="border rounded-lg p-3">
                                    <div className="text-xs text-muted-foreground">OUTCOMES</div>
                                    <div className="text-sm mt-1">
                                        VM: <span className="font-medium">{stats?.attempt_outcomes?.voicemail_dropped || 0}</span> · Human:{' '}
                                        <span className="font-medium">{stats?.attempt_outcomes?.answered_human || 0}</span> · Errors:{' '}
                                        <span className="font-medium">{stats?.attempt_outcomes?.error || 0}</span>
                                    </div>
                                </div>
                                <div className="border rounded-lg p-3">
                                    <div className="text-xs text-muted-foreground">STATUS</div>
                                    <div className="text-sm mt-1">
                                        {statusInfo?.within === false ? (
                                            <span className="inline-flex items-center gap-1 text-yellow-600">
                                                <AlertTriangle className="w-4 h-4" /> Outside window
                                            </span>
                                        ) : (
                                            <span className="inline-flex items-center gap-1 text-green-700">
                                                <PhoneCall className="w-4 h-4" /> Ready
                                            </span>
                                        )}
                                    </div>
                                    {statusInfo?.missing?.length ? (
                                        <div className="text-xs text-yellow-700 mt-1 space-y-0.5">
                                            {statusInfo.missing.map(m => (
                                                <div key={m} className="flex items-center gap-1">
                                                    <AlertTriangle className="w-3 h-3" /> {m}
                                                </div>
                                            ))}
                                        </div>
                                    ) : null}
                                </div>
                            </div>
                        </>
                    )}
                </div>

                <div className="col-span-2 border rounded-lg bg-card">
                    <div className="flex items-center justify-between gap-3 p-3 border-b">
                        <div className="flex items-center gap-2">
                            <div className="font-semibold">Leads</div>
                            {selectedCampaign && leads.length === 0 && (
                                <button
                                    className="inline-flex items-center gap-2 px-2 py-1 rounded-md border hover:bg-muted text-xs"
                                    onClick={() => {
                                        setCampaignModalMode('edit');
                                        setCampaignModalStep('leads');
                                        setShowCampaignModal(true);
                                    }}
                                >
                                    <Upload className="w-3 h-3" /> Import leads
                                </button>
                            )}
                        </div>
                        <div className="flex items-center gap-2">
                            <div className="relative">
                                <Search className="w-4 h-4 absolute left-2 top-2.5 text-muted-foreground" />
                                <input
                                    value={leadQuery}
                                    onChange={e => {
                                        setLeadQuery(e.target.value);
                                        setLeadPage(1);
                                    }}
                                    placeholder="Search name/number…"
                                    className="pl-8 pr-3 py-2 rounded-lg border bg-background text-sm w-56"
                                />
                            </div>
                            <select
                                value={leadStateFilter}
                                onChange={e => {
                                    setLeadStateFilter(e.target.value);
                                    setLeadPage(1);
                                }}
                                className="px-3 py-2 rounded-lg border bg-background text-sm"
                            >
                                <option value="">All states</option>
                                <option value="pending">pending</option>
                                <option value="leased">leased</option>
                                <option value="dialing">dialing</option>
                                <option value="in_progress">in_progress</option>
                                <option value="completed">completed</option>
                                <option value="failed">failed</option>
                                <option value="canceled">canceled</option>
                            </select>
                        </div>
                    </div>

                    <div className="overflow-x-auto">
                        <table className="min-w-[1500px] w-full text-sm">
                            <thead className="bg-muted/30 text-muted-foreground">
                                <tr className="text-left">
                                    <th className="py-2 px-3">Name</th>
                                    <th className="py-2 px-3">Number</th>
                                    <th className="py-2 px-3">State</th>
                                    <th className="py-2 px-3">Context</th>
                                    <th className="py-2 px-3">Provider</th>
                                    <th className="py-2 px-3">Time</th>
                                    <th className="py-2 px-3">Duration</th>
                                    <th className="py-2 px-3">Attempts</th>
                                    <th className="py-2 px-3">Outcome</th>
                                    <th className="py-2 px-3">AMD</th>
                                    <th className="py-2 px-3">DTMF</th>
                                    <th className="py-2 px-3">Call History</th>
                                    <th className="py-2 px-3">Last Error</th>
                                    <th className="py-2 px-3 text-right">Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {leads.map(l => {
                                    const effectiveContext = (l.last_context || l.context_override || selectedCampaign?.default_context || 'default') as string;
                                    const provider = (l.last_provider || '-') as string;
                                    const amd = l.last_amd_status ? `${l.last_amd_status}${l.last_amd_cause ? `/${l.last_amd_cause}` : ''}` : '-';
                                    const outcome = l.last_outcome_attempt || l.last_outcome || '-';
                                    const dtmf = l.last_consent_dtmf || '-';
                                    const canDelete = Boolean(selectedCampaign && selectedCampaign.status !== 'running');
                                    const isIgnored = l.state === 'canceled';
                                    return (
                                        <tr key={l.id} className="border-b border-border/50">
                                            <td className="py-2 px-3">{l.name || '-'}</td>
                                            <td className="py-2 px-3 font-mono">{l.phone_number}</td>
                                            <td className="py-2 px-3">{l.state}</td>
                                            <td className="py-2 px-3 font-mono">{effectiveContext}</td>
                                            <td className="py-2 px-3 font-mono">{provider}</td>
                                            <td className="py-2 px-3">{renderLeadTime(l.last_started_at_utc || l.last_attempt_at_utc)}</td>
                                            <td className="py-2 px-3">{renderDuration(l.last_duration_seconds ?? null)}</td>
                                            <td className="py-2 px-3">{l.attempt_count}</td>
                                            <td className="py-2 px-3">{outcome}</td>
                                            <td className="py-2 px-3 font-mono">{amd}</td>
                                            <td className="py-2 px-3 font-mono">{dtmf}</td>
                                            <td className="py-2 px-3">
                                                {l.last_call_history_call_id ? (
                                                    <button
                                                        className="inline-flex items-center gap-2 px-2 py-1 rounded-md border hover:bg-muted text-xs"
                                                        onClick={() => openCallHistory(l.last_call_history_call_id as string)}
                                                    >
                                                        <ExternalLink className="w-3 h-3" /> Open
                                                    </button>
                                                ) : (
                                                    <span className="text-muted-foreground">-</span>
                                                )}
                                            </td>
                                            <td className="py-2 px-3 max-w-[260px] truncate" title={l.last_error_message || ''}>
                                                {l.last_error_message ? (
                                                    <span className="text-red-600">{l.last_error_message}</span>
                                                ) : (
                                                    <span className="text-muted-foreground">-</span>
                                                )}
                                            </td>
                                            <td className="py-2 px-3 text-right whitespace-nowrap">
                                                <button
                                                    className="inline-flex items-center gap-2 px-2 py-1 rounded-md hover:bg-accent text-xs"
                                                    onClick={() => {
                                                        setRecycleLeadRow(l);
                                                        setRecycleMode('redial');
                                                    }}
                                                    title="Recycle lead"
                                                >
                                                    <RotateCcw className="w-3 h-3" /> Recycle
                                                </button>
                                                <button
                                                    className="inline-flex items-center gap-2 px-2 py-1 rounded-md hover:bg-accent text-xs"
                                                    onClick={() => ignoreLead(l.id)}
                                                    disabled={isIgnored}
                                                    title="Ignore lead (canceled, reversible)"
                                                >
                                                    <Ban className="w-3 h-3" /> {isIgnored ? 'Ignored' : 'Ignore'}
                                                </button>
                                                <button
                                                    className="inline-flex items-center gap-2 px-2 py-1 rounded-md hover:bg-accent text-xs text-red-600 disabled:opacity-50"
                                                    onClick={() => deleteLead(l.id)}
                                                    disabled={!canDelete}
                                                    title={canDelete ? 'Delete lead' : 'Pause/stop campaign to delete leads'}
                                                >
                                                    <Trash2 className="w-3 h-3" /> Delete
                                                </button>
                                            </td>
                                        </tr>
                                    );
                                })}
                                {leads.length === 0 && (
                                    <tr>
                                        <td colSpan={14} className="py-10 text-center text-sm text-muted-foreground">
                                            No leads yet. Use the campaign modal to import a CSV.
                                        </td>
                                    </tr>
                                )}
                            </tbody>
                        </table>
                    </div>

                    <div className="flex items-center justify-between p-3">
                        <div className="text-xs text-muted-foreground">
                            Page {leadPage} of {leadTotalPages}
                        </div>
                        <div className="flex items-center gap-2">
                            <button
                                className="px-3 py-1 rounded border text-sm disabled:opacity-50"
                                disabled={leadPage <= 1 || !selectedCampaignId}
                                onClick={() => setLeadPage(p => Math.max(1, p - 1))}
                            >
                                Prev
                            </button>
                            <button
                                className="px-3 py-1 rounded border text-sm disabled:opacity-50"
                                disabled={leadPage >= leadTotalPages || !selectedCampaignId}
                                onClick={() => setLeadPage(p => Math.min(leadTotalPages, p + 1))}
                            >
                                Next
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            {/* Campaign modal */}
            {showCampaignModal && (
                <Modal
                    isOpen={true}
                    title={campaignModalMode === 'create' ? 'Create Campaign' : 'Campaign Setup'}
                    onClose={() => {
                        setShowCampaignModal(false);
                        setCampaignModalStep('settings');
                        setDialplanNeedsReview(false);
                        setPendingImportFile(null);
                        setPendingVoicemailFile(null);
                        setPendingConsentFile(null);
                    }}
                    footer={
                        <>
                            <button
                                className="px-3 py-2 rounded-lg border hover:bg-muted text-sm"
                                onClick={() => {
                                    setShowCampaignModal(false);
                                    setCampaignModalStep('settings');
                                    setDialplanNeedsReview(false);
                                    setPendingImportFile(null);
                                    setPendingVoicemailFile(null);
                                    setPendingConsentFile(null);
                                }}
                            >
                                Close
                            </button>
                            {campaignModalMode === 'create' ? (
                                <button
                                    className="px-3 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 text-sm disabled:opacity-50"
                                    onClick={createCampaign}
                                    disabled={!createForm.name.trim() || !modalTimezoneValid}
                                >
                                    Create
                                </button>
                            ) : (
                                <button
                                    className="px-3 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 text-sm disabled:opacity-50"
                                    onClick={saveEdit}
                                    disabled={!modalTimezoneValid}
                                >
                                    Save
                                </button>
                            )}
                        </>
                    }
                    size="xl"
                >
                    <div className="space-y-4">
                        <div className="flex items-center justify-between text-xs text-muted-foreground">
                            <span className="inline-flex items-center gap-1">
                                <Clock className="w-3 h-3" /> Server now:{' '}
                                <span className="font-mono text-foreground">{formatClock(serverNow, serverTz)}</span> <span className="font-mono">{serverTz}</span>
                            </span>
                            <span className="inline-flex items-center gap-1">
                                <Clock className="w-3 h-3" /> Campaign now:{' '}
                                <span className="font-mono text-foreground">
                                    {formatClock(serverNow, (campaignModalMode === 'create' ? createForm.timezone : editForm.timezone) || 'UTC')}
                                </span>{' '}
                                <span className="font-mono">{(campaignModalMode === 'create' ? createForm.timezone : editForm.timezone) || 'UTC'}</span>
                            </span>
                        </div>

                        <div className="flex items-center gap-2">
                            <button
                                className={`px-3 py-1 rounded border text-sm ${
                                    campaignModalStep === 'settings' ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
                                }`}
                                onClick={() => setCampaignModalStep('settings')}
                            >
                                Settings
                            </button>
                            <button
                                className={`px-3 py-1 rounded border text-sm ${
                                    campaignModalStep === 'leads' ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
                                }`}
                                onClick={() => setCampaignModalStep('leads')}
                            >
                                Leads
                            </button>
                            <button
                                className={`px-3 py-1 rounded border text-sm ${
                                    campaignModalStep === 'recordings' ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
                                }`}
                                onClick={() => setCampaignModalStep('recordings')}
                            >
                                Recordings
                            </button>
                            <button
                                className={`px-3 py-1 rounded border text-sm ${
                                    campaignModalStep === 'setup' ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
                                }`}
                                onClick={() => setCampaignModalStep('setup')}
                            >
                                Setup Guide
                            </button>
                            <button
                                className={`px-3 py-1 rounded border text-sm ${
                                    campaignModalStep === 'advanced' ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
                                }`}
                                onClick={() => setCampaignModalStep('advanced')}
                            >
                                Advanced (AMD)
                            </button>
                        </div>

                        {dialplanNeedsReview && (
                            <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                                Consent/voicemail settings changed in this modal. Review the updated dialplan in the “Setup Guide” tab and reload Asterisk dialplan if needed.
                            </div>
                        )}

                        {campaignModalStep === 'settings' ? (
                            <div className="space-y-3">
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                    <div>
                                        <label className="text-sm font-medium">Name</label>
                                        <input
                                            value={campaignModalMode === 'create' ? createForm.name : editForm.name}
                                            onChange={e =>
                                                campaignModalMode === 'create'
                                                    ? setCreateForm(p => ({ ...p, name: e.target.value }))
                                                    : setEditForm(p => ({ ...p, name: e.target.value }))
                                            }
                                            className="mt-1 w-full px-3 py-2 rounded-lg border bg-background"
                                        />
                                    </div>
                                    <div>
                                        <label className="text-sm font-medium">Timezone</label>
                                        <input
                                            list="aava-iana-timezones"
                                            value={campaignModalMode === 'create' ? createForm.timezone : editForm.timezone}
                                            onChange={e =>
                                                campaignModalMode === 'create'
                                                    ? setCreateForm(p => ({ ...p, timezone: e.target.value }))
                                                    : setEditForm(p => ({ ...p, timezone: e.target.value }))
                                            }
                                            className="mt-1 w-full px-3 py-2 rounded-lg border bg-background font-mono"
                                        />
                                        {!modalTimezoneValid && (
                                            <div className="mt-1 text-xs text-yellow-700 flex items-center gap-1">
                                                <AlertTriangle className="w-3 h-3" /> Invalid timezone; use an IANA timezone (e.g., America/Phoenix).
                                            </div>
                                        )}
                                    </div>
                                    <div>
                                        <label className="text-sm font-medium">Daily Window Start (local)</label>
                                        <input
                                            type="time"
                                            value={campaignModalMode === 'create' ? createForm.daily_window_start_local : editForm.daily_window_start_local}
                                            onChange={e =>
                                                campaignModalMode === 'create'
                                                    ? setCreateForm(p => ({ ...p, daily_window_start_local: e.target.value }))
                                                    : setEditForm(p => ({ ...p, daily_window_start_local: e.target.value }))
                                            }
                                            className="mt-1 w-full px-3 py-2 rounded-lg border bg-background"
                                        />
                                    </div>
                                    <div>
                                        <label className="text-sm font-medium">Daily Window End (local)</label>
                                        <input
                                            type="time"
                                            value={campaignModalMode === 'create' ? createForm.daily_window_end_local : editForm.daily_window_end_local}
                                            onChange={e =>
                                                campaignModalMode === 'create'
                                                    ? setCreateForm(p => ({ ...p, daily_window_end_local: e.target.value }))
                                                    : setEditForm(p => ({ ...p, daily_window_end_local: e.target.value }))
                                            }
                                            className="mt-1 w-full px-3 py-2 rounded-lg border bg-background"
                                        />
                                        {(() => {
                                            const s = campaignModalMode === 'create' ? createForm.daily_window_start_local : editForm.daily_window_start_local;
                                            const e = campaignModalMode === 'create' ? createForm.daily_window_end_local : editForm.daily_window_end_local;
                                            const crosses = Boolean(s && e && e < s);
                                            return crosses ? (
                                                <div className="mt-1 text-xs text-yellow-700 flex items-center gap-1">
                                                    <AlertTriangle className="w-3 h-3" /> Crosses midnight
                                                </div>
                                            ) : null;
                                        })()}
                                    </div>
                                    <div>
                                        <label className="text-sm font-medium">Max Concurrent</label>
                                        <input
                                            type="number"
                                            min={1}
                                            max={5}
                                            value={campaignModalMode === 'create' ? createForm.max_concurrent : editForm.max_concurrent}
                                            onChange={e =>
                                                campaignModalMode === 'create'
                                                    ? setCreateForm(p => ({ ...p, max_concurrent: Number(e.target.value) || 1 }))
                                                    : setEditForm(p => ({ ...p, max_concurrent: Number(e.target.value) || 1 }))
                                            }
                                            className="mt-1 w-full px-3 py-2 rounded-lg border bg-background"
                                        />
                                    </div>
                                    <div>
                                        <label className="text-sm font-medium">Min Interval Between Calls (sec)</label>
                                        <input
                                            type="number"
                                            min={0}
                                            value={
                                                campaignModalMode === 'create'
                                                    ? createForm.min_interval_seconds_between_calls
                                                    : editForm.min_interval_seconds_between_calls
                                            }
                                            onChange={e =>
                                                campaignModalMode === 'create'
                                                    ? setCreateForm(p => ({ ...p, min_interval_seconds_between_calls: Number(e.target.value) || 0 }))
                                                    : setEditForm(p => ({ ...p, min_interval_seconds_between_calls: Number(e.target.value) || 0 }))
                                            }
                                            className="mt-1 w-full px-3 py-2 rounded-lg border bg-background"
                                        />
                                    </div>
                                    <div className="md:col-span-2">
                                        <label className="text-sm font-medium">Default Context</label>
                                        <input
                                            value={campaignModalMode === 'create' ? createForm.default_context : editForm.default_context}
                                            onChange={e =>
                                                campaignModalMode === 'create'
                                                    ? setCreateForm(p => ({ ...p, default_context: e.target.value }))
                                                    : setEditForm(p => ({ ...p, default_context: e.target.value }))
                                            }
                                            className="mt-1 w-full px-3 py-2 rounded-lg border bg-background font-mono"
                                        />
                                    </div>
                                </div>

                                <div className="border rounded-lg p-3 space-y-2">
                                    <div className="font-medium text-sm">Features</div>
                                    <label className="flex items-center gap-2 text-sm">
                                        <input
                                            type="checkbox"
                                            checked={
                                                Boolean(
                                                    campaignModalMode === 'create' ? createForm.voicemail_drop_enabled : editForm.voicemail_drop_enabled
                                                )
                                            }
                                            onChange={e =>
                                                campaignModalMode === 'create'
                                                    ? setCreateForm(p => {
                                                          setDialplanNeedsReview(true);
                                                          return { ...p, voicemail_drop_enabled: e.target.checked };
                                                      })
                                                    : setEditForm(p => {
                                                          setDialplanNeedsReview(true);
                                                          return { ...p, voicemail_drop_enabled: e.target.checked };
                                                      })
                                            }
                                        />
                                        Voicemail drop (AMD MACHINE/NOTSURE → leave voicemail recording)
                                    </label>
                                    <label className="flex items-center gap-2 text-sm">
                                        <input
                                            type="checkbox"
                                            checked={Boolean(campaignModalMode === 'create' ? createForm.consent_enabled : editForm.consent_enabled)}
                                            onChange={e =>
                                                campaignModalMode === 'create'
                                                    ? setCreateForm(p => {
                                                          setDialplanNeedsReview(true);
                                                          return { ...p, consent_enabled: e.target.checked };
                                                      })
                                                    : setEditForm(p => {
                                                          setDialplanNeedsReview(true);
                                                          return { ...p, consent_enabled: e.target.checked };
                                                      })
                                            }
                                        />
                                        Consent gate (HUMAN → play consent prompt, DTMF 1 accept / 2 deny)
                                    </label>
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-2">
                                        <div>
                                            <label className="text-sm font-medium">Consent timeout (sec)</label>
                                            <input
                                                type="number"
                                                min={1}
                                                max={30}
                                                value={Number((campaignModalMode === 'create' ? createForm.consent_timeout_seconds : editForm.consent_timeout_seconds) || 5)}
                                                onChange={e =>
                                                    campaignModalMode === 'create'
                                                        ? setCreateForm(p => ({ ...p, consent_timeout_seconds: Number(e.target.value) || 5 }))
                                                        : setEditForm(p => ({ ...p, consent_timeout_seconds: Number(e.target.value) || 5 }))
                                                }
                                                className="mt-1 w-full px-3 py-2 rounded-lg border bg-background"
                                            />
                                        </div>
                                    </div>
                                </div>

                            </div>
                        ) : campaignModalStep === 'leads' ? (
                            <div className="space-y-4">
                                <div className="border rounded-lg p-3 space-y-2">
                                    <div className="font-medium text-sm">Leads (CSV)</div>
                                    <div className="text-xs text-muted-foreground">
                                        Import leads from CSV. Default behavior is <span className="font-mono">skip_existing</span>.
                                        {campaignModalMode === 'create' ? ' Choose a CSV now; it will import after Create.' : ''}
                                    </div>
                                    <div className="flex items-center gap-2 flex-wrap">
                                        <button
                                            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border hover:bg-muted text-sm"
                                            onClick={downloadSampleCsv}
                                        >
                                            <FileDown className="w-4 h-4" /> Sample CSV
                                        </button>
                                        <input type="file" accept=".csv,text/csv" onChange={e => setPendingImportFile(e.target.files?.[0] || null)} />
                                        <button
                                            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 text-sm disabled:opacity-50"
                                            disabled={!pendingImportFile || (campaignModalMode === 'edit' && !selectedCampaign)}
                                            onClick={async () => {
                                                if (!pendingImportFile) return;
                                                if (campaignModalMode === 'create') {
                                                    setNotice({ type: 'info', message: 'CSV selected. It will import after campaign creation.' });
                                                    return;
                                                }
                                                if (!selectedCampaign) return;
                                                await importLeads(selectedCampaign.id, pendingImportFile);
                                                setPendingImportFile(null);
                                            }}
                                        >
                                            <Upload className="w-4 h-4" /> Import CSV (skip existing)
                                        </button>
                                    </div>
                                    {campaignModalMode === 'create' && pendingImportFile && (
                                        <div className="text-xs text-muted-foreground">
                                            Queued: <span className="font-mono">{pendingImportFile.name}</span>
                                        </div>
                                    )}
                                    {campaignModalMode === 'edit' && !selectedCampaign && (
                                        <div className="text-xs text-muted-foreground">Select a campaign to import leads.</div>
                                    )}
                                </div>
                            </div>
                        ) : campaignModalStep === 'recordings' ? (
                            <div className="space-y-4">
                                <div className="border rounded-lg p-3 space-y-3">
                                    <div className="font-medium text-sm">Consent prompt</div>
                                    <div className="text-xs text-muted-foreground">
                                        Used only when “Consent gate” is enabled (DTMF 1 accept / 2 deny).
                                    </div>
                                    <div className="text-xs text-muted-foreground">
                                        {campaignModalMode === 'edit' ? (
                                            <>
                                                Current: <span className="font-mono">{(selectedCampaign as any)?.consent_media_uri || '(not set)'}</span>
                                            </>
                                        ) : (
                                            <>
                                                Queued: <span className="font-mono">{pendingConsentFile?.name || '(none)'}</span>
                                            </>
                                        )}
                                    </div>
                                    <div className="flex items-center gap-2 flex-wrap">
                                        <input
                                            type="file"
                                            accept=".wav,.ulaw,audio/wav"
                                            onChange={e => {
                                                setPendingConsentFile(e.target.files?.[0] || null);
                                                setDialplanNeedsReview(true);
                                            }}
                                        />
                                        <button
                                            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border hover:bg-muted text-sm disabled:opacity-50"
                                            disabled={campaignModalMode === 'create' || !pendingConsentFile || !selectedCampaign}
                                            onClick={async () => {
                                                if (!pendingConsentFile || !selectedCampaign) return;
                                                await uploadMedia(selectedCampaign.id, 'consent', pendingConsentFile);
                                                setPendingConsentFile(null);
                                                setDialplanNeedsReview(true);
                                            }}
                                        >
                                            <Upload className="w-4 h-4" /> Upload
                                        </button>
                                        <button
                                            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border hover:bg-muted text-sm disabled:opacity-50"
                                            disabled={campaignModalMode === 'create' || !selectedCampaign || !Boolean(((selectedCampaign as any)?.consent_media_uri || '').trim())}
                                            onClick={() => selectedCampaign && previewMedia(selectedCampaign.id, 'consent')}
                                        >
                                            Preview
                                        </button>
                                    </div>
                                </div>

                                <div className="border rounded-lg p-3 space-y-3">
                                    <div className="font-medium text-sm">Voicemail drop</div>
                                    <div className="text-xs text-muted-foreground">
                                        Used only when “Voicemail drop” is enabled and AMD indicates MACHINE/NOTSURE.
                                    </div>
                                    <div className="text-xs text-muted-foreground">
                                        {campaignModalMode === 'edit' ? (
                                            <>
                                                Current: <span className="font-mono">{selectedCampaign?.voicemail_drop_media_uri || '(not set)'}</span>
                                            </>
                                        ) : (
                                            <>
                                                Queued: <span className="font-mono">{pendingVoicemailFile?.name || '(none)'}</span>
                                            </>
                                        )}
                                    </div>
                                    <div className="flex items-center gap-2 flex-wrap">
                                        <input
                                            type="file"
                                            accept=".wav,.ulaw,audio/wav"
                                            onChange={e => {
                                                setPendingVoicemailFile(e.target.files?.[0] || null);
                                                setDialplanNeedsReview(true);
                                            }}
                                        />
                                        <button
                                            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border hover:bg-muted text-sm disabled:opacity-50"
                                            disabled={campaignModalMode === 'create' || !pendingVoicemailFile || !selectedCampaign}
                                            onClick={async () => {
                                                if (!pendingVoicemailFile || !selectedCampaign) return;
                                                await uploadMedia(selectedCampaign.id, 'voicemail', pendingVoicemailFile);
                                                setPendingVoicemailFile(null);
                                                setDialplanNeedsReview(true);
                                            }}
                                        >
                                            <Upload className="w-4 h-4" /> Upload
                                        </button>
                                        <button
                                            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border hover:bg-muted text-sm disabled:opacity-50"
                                            disabled={campaignModalMode === 'create' || !selectedCampaign || !Boolean((selectedCampaign?.voicemail_drop_media_uri || '').trim())}
                                            onClick={() => selectedCampaign && previewMedia(selectedCampaign.id, 'voicemail')}
                                        >
                                            Preview
                                        </button>
                                    </div>
                                </div>

                                {campaignModalMode === 'create' && (
                                    <div className="text-xs text-muted-foreground">
                                        Files are queued in this modal and will upload right after campaign creation.
                                    </div>
                                )}
                            </div>
                        ) : campaignModalStep === 'setup' ? (
                            <div className="space-y-3">
                                <div className="text-sm text-muted-foreground">
                                    Add this to <span className="font-mono">/etc/asterisk/extensions_custom.conf</span> and reload the dialplan.
                                </div>
                                <div className="rounded-lg border bg-muted/20 p-3 text-sm">
                                    <div className="font-medium">Generated from current campaign settings</div>
                                    <div className="text-xs text-muted-foreground mt-1">
                                        Consent enabled: <span className="font-mono">{String(modalConsentEnabled)}</span>
                                        {' · '}
                                        Voicemail drop enabled: <span className="font-mono">{String(modalVoicemailEnabled)}</span>
                                    </div>
                                    <div className="text-xs text-muted-foreground mt-1">
                                        Replace <span className="font-mono">asterisk-ai-voice-agent</span> with your configured ARI app name if different.
                                    </div>
                                </div>
                                <div className="rounded-lg border bg-muted/10 p-3 text-xs text-muted-foreground">
                                    <div className="font-medium text-foreground">Legend</div>
                                    <div className="mt-1">
                                        <span className="text-red-700 font-medium">Red</span> lines are commented out (prefixed with <span className="font-mono">;</span>) to match the current campaign settings.
                                    </div>
                                    <div className="mt-1">
                                        If you later enable the feature again, re-copy this snippet so those lines are uncommented.
                                    </div>
                                </div>
                                <pre className="bg-muted/30 rounded-lg p-3 overflow-x-auto text-xs">
                                    {modalDialplanSnippetForDisplayAndCopy.split('\n').map((line, idx) => {
                                        const group = classifyDialplanLine(line);
                                        const isInactive =
                                            (group === 'consent' && !modalConsentEnabled) ||
                                            (group === 'voicemail' && !modalVoicemailEnabled);
                                        const cls = isInactive && _isCommented(line) ? 'text-red-700' : 'text-foreground';
                                        return (
                                            <span key={idx} className={cls}>
                                                {line}
                                                {'\n'}
                                            </span>
                                        );
                                    })}
                                </pre>
                                <div className="flex items-center gap-2">
                                    <button
                                        className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border hover:bg-muted text-sm"
                                        onClick={async () => {
                                            const ok = await copyTextToClipboard(modalDialplanSnippetForDisplayAndCopy);
                                            if (ok) setNotice({ type: 'success', message: 'Dialplan copied to clipboard' });
                                            else setNotice({ type: 'error', message: 'Clipboard copy failed (try selecting the text manually)' });
                                        }}
                                    >
                                        <Copy className="w-4 h-4" /> Copy
                                    </button>
                                </div>
                                {(modalConsentEnabled || modalVoicemailEnabled) && (
                                    <div className="text-xs text-muted-foreground">
                                        If you change consent/voicemail settings later, re-check this tab and update the dialplan if needed.
                                    </div>
                                )}
                            </div>
                        ) : (
                            <div className="space-y-4">
                                <div className="border rounded-lg p-3">
                                    <div className="font-medium text-sm">Advanced AMD settings</div>
                                    <div className="text-xs text-muted-foreground mt-1">
                                        AMD() positional args. Leave blank to use defaults.
                                    </div>
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
                                        {([
                                            ['initial_silence_ms', 'Initial silence (ms)'],
                                            ['greeting_ms', 'Greeting (ms)'],
                                            ['after_greeting_silence_ms', 'After greeting silence (ms)'],
                                            ['total_analysis_time_ms', 'Total analysis time (ms)'],
                                            ['minimum_word_length_ms', 'Min word length (ms)'],
                                            ['between_words_silence_ms', 'Between words silence (ms)'],
                                            ['maximum_number_of_words', 'Max number of words'],
                                            ['silence_threshold', 'Silence threshold'],
                                            ['maximum_word_length_ms', 'Max word length (ms)']
                                        ] as Array<[string, string]>).map(([key, label]) => {
                                            const form = campaignModalMode === 'create' ? createForm : editForm;
                                            const value = (form.amd_options || {})[key] ?? '';
                                            return (
                                                <div key={key}>
                                                    <label className="text-sm font-medium">{label}</label>
                                                    <input
                                                        value={value}
                                                        onChange={e => {
                                                            const v = e.target.value;
                                                            const next = { ...(form.amd_options || {}) };
                                                            if (v === '' || v == null) delete next[key];
                                                            else next[key] = Number(v);
                                                            campaignModalMode === 'create'
                                                                ? setCreateForm(p => ({ ...p, amd_options: next }))
                                                                : setEditForm(p => ({ ...p, amd_options: next }));
                                                        }}
                                                        className="mt-1 w-full px-3 py-2 rounded-lg border bg-background"
                                                        placeholder="(blank)"
                                                    />
                                                </div>
                                            );
                                        })}
                                    </div>
                                </div>
                            </div>
                        )}

                    </div>
                </Modal>
            )}

            {/* Recycle modal */}
            {recycleLeadRow && (
                <Modal isOpen={true} title="Recycle Lead" onClose={() => setRecycleLeadRow(null)}>
                    <div className="space-y-3">
                        <div className="text-sm">
                            Lead: <span className="font-mono">{recycleLeadRow.phone_number}</span>
                        </div>
                        <label className="flex items-start gap-2 text-sm">
                            <input type="radio" checked={recycleMode === 'redial'} onChange={() => setRecycleMode('redial')} />
                            <span>
                                <span className="font-medium">Re-dial</span>
                                <div className="text-xs text-muted-foreground">Keep attempt history; set lead back to pending.</div>
                            </span>
                        </label>
                        <label className="flex items-start gap-2 text-sm">
                            <input type="radio" checked={recycleMode === 'reset'} onChange={() => setRecycleMode('reset')} />
                            <span>
                                <span className="font-medium">Reset completely</span>
                                <div className="text-xs text-muted-foreground">Delete attempts for this lead and reset counters; then re-queue.</div>
                            </span>
                        </label>
                        <div className="flex justify-end gap-2">
                            <button className="px-3 py-2 rounded-lg border hover:bg-muted text-sm" onClick={() => setRecycleLeadRow(null)}>
                                Cancel
                            </button>
                            <button
                                className="px-3 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 text-sm"
                                onClick={async () => {
                                    const leadId = recycleLeadRow.id;
                                    setRecycleLeadRow(null);
                                    await recycleLead(leadId, recycleMode);
                                }}
                            >
                                Confirm
                            </button>
                        </div>
                    </div>
                </Modal>
            )}

            {/* Call history inline modal */}
            {callHistoryModalId && (
                <Modal
                    isOpen={true}
                    title="Call History"
                    onClose={() => {
                        setCallHistoryModalId(null);
                        setCallHistoryRecord(null);
                        setCallHistoryError(null);
                    }}
                >
                    {callHistoryLoading ? (
                        <div className="flex items-center gap-2 text-muted-foreground text-sm">
                            <RefreshCw className="w-4 h-4 animate-spin" /> Loading…
                        </div>
                    ) : callHistoryError ? (
                        <div className="text-sm text-red-600">{callHistoryError}</div>
                    ) : callHistoryRecord ? (
                        <div className="space-y-3">
                            <div className="text-sm">
                                <div className="text-xs text-muted-foreground">Call ID</div>
                                <div className="font-mono">{callHistoryRecord.call_id}</div>
                            </div>
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                                <div>
                                    <div className="text-xs text-muted-foreground">Number</div>
                                    <div className="font-mono">{callHistoryRecord.caller_number || '-'}</div>
                                </div>
                                <div>
                                    <div className="text-xs text-muted-foreground">Name</div>
                                    <div>{callHistoryRecord.caller_name || '-'}</div>
                                </div>
                                <div>
                                    <div className="text-xs text-muted-foreground">Outcome</div>
                                    <div className="font-medium">{callHistoryRecord.outcome || '-'}</div>
                                </div>
                                <div>
                                    <div className="text-xs text-muted-foreground">Duration</div>
                                    <div className="font-medium">{Math.round(callHistoryRecord.duration_seconds || 0)}s</div>
                                </div>
                            </div>
                            {callHistoryRecord.error_message && (
                                <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-700">{callHistoryRecord.error_message}</div>
                            )}
                            <div>
                                <div className="font-medium text-sm mb-1">Transcript</div>
                                <div className="bg-muted/30 rounded-lg p-3 max-h-64 overflow-y-auto text-sm space-y-2">
                                    {(callHistoryRecord.conversation_history || []).length ? (
                                        callHistoryRecord.conversation_history.map((m: any, idx: number) => (
                                            <div key={idx}>
                                                <div className="text-xs text-muted-foreground">{m.role}</div>
                                                <div>{m.content}</div>
                                            </div>
                                        ))
                                    ) : (
                                        <div className="text-muted-foreground">No transcript available.</div>
                                    )}
                                </div>
                            </div>
                        </div>
                    ) : (
                        <div className="text-sm text-muted-foreground">No record loaded.</div>
                    )}
                </Modal>
            )}
        </div>
    );
};

export default CallSchedulingPage;
