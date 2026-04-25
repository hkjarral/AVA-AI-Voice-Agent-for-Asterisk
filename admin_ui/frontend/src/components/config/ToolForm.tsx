import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { Plus, Trash2, Settings, Loader2 } from 'lucide-react';
import { FormInput, FormSwitch, FormSelect, FormLabel } from '../ui/FormComponents';
import { Modal } from '../ui/Modal';
import { EmailTemplateModal } from './EmailTemplateModal';

interface ToolFormProps {
    config: any;
    contexts?: Record<string, any>;
    hangupUsage?: {
        googleLiveMarkersEnabled: boolean | null;
        pipelineEndCallOverrides: string[];
        pipelineModeOverrides: { name: string; mode: string }[];
        pipelineGuardrailOverrides: { name: string; enabled: boolean }[];
    };
    onChange: (newConfig: any) => void;
    onContextsChange?: (newContexts: Record<string, any>) => void;
    onSaveNow?: (newConfig: any) => Promise<void>;
}

const DEFAULT_ATTENDED_ANNOUNCEMENT_TEMPLATE =
    "Hi, this is Ava. I'm transferring {caller_display} regarding {context_name}.";
const DEFAULT_ATTENDED_AI_BRIEFING_INTRO_TEMPLATE =
    "Hi, this is Ava. Here is a short summary of the caller.";
const DEFAULT_ATTENDED_AGENT_DTMF_PROMPT_TEMPLATE =
    "Press 1 to accept this transfer, or 2 to decline.";
const DEFAULT_ATTENDED_CALLER_CONNECTED_PROMPT = "Connecting you now.";
const DEFAULT_ATTENDED_CALLER_DECLINED_PROMPT =
    "I’m not able to complete that transfer right now. Would you like me to take a message, or is there anything else I can help with?";
const DEFAULT_HANGUP_POLICY_MODE = 'normal';
const DEFAULT_HANGUP_END_CALL_MARKERS = [
    "no transcript",
    "no transcript needed",
    "don't send a transcript",
    "no thanks",
    "that's all",
    "nothing else",
    "end call",
    "hang up",
    "goodbye",
    "bye",
];
const DEFAULT_HANGUP_ASSISTANT_FAREWELL_MARKERS = [
    "goodbye",
    "bye",
    "thank you for calling",
    "have a great day",
    "take care",
];

const HANGUP_EXPERT_STORAGE_KEY = 'aava.ui.tools.hangupExpertSettings';

const parseMarkerList = (value: string) =>
    (value || '')
        .split('\n')
        .map((line) => line.trim())
        .filter((line) => line.length > 0);

const renderMarkerList = (value: string[] | undefined, fallback: string[]) =>
    (Array.isArray(value) && value.length > 0 ? value : fallback).join('\n');

const hasLiveAgentExpertSettings = (ext: any) => {
    const actionType = String(ext?.action_type || 'transfer').trim() || 'transfer';
    const deviceStateTech = String(ext?.device_state_tech || 'auto').trim() || 'auto';
    const aliases = Array.isArray(ext?.aliases)
        ? ext.aliases.map((item: any) => String(item || '').trim()).filter(Boolean)
        : [];
    return actionType !== 'transfer' || deviceStateTech !== 'auto' || aliases.length > 0;
};

const ToolForm = ({ config, contexts, hangupUsage, onChange, onContextsChange, onSaveNow }: ToolFormProps) => {
    // Migrate calendar key references in all contexts' selected_calendars
    const migrateCalendarKeyInContexts = (oldKey: string, newKey: string | null) => {
        if (!contexts || !onContextsChange) return;
        const updated = { ...contexts };
        let changed = false;
        for (const [ctxName, ctx] of Object.entries(updated)) {
            const sel: string[] | undefined = (ctx as any)?.tool_overrides?.google_calendar?.selected_calendars;
            if (!Array.isArray(sel) || !sel.includes(oldKey)) continue;
            changed = true;
            const nextSel = newKey
                ? sel.map((k: string) => (k === oldKey ? newKey : k))
                : sel.filter((k: string) => k !== oldKey);
            updated[ctxName] = {
                ...(ctx as any),
                tool_overrides: {
                    ...((ctx as any)?.tool_overrides || {}),
                    google_calendar: {
                        ...((ctx as any)?.tool_overrides?.google_calendar || {}),
                        selected_calendars: nextSel,
                    },
                },
            };
        }
        if (changed) onContextsChange(updated);
    };

    const commitCalendarKeyDraft = (stableKey: string) => {
        const nextKey = String(calKeyDraftByKey[stableKey] ?? stableKey).trim();
        if (!nextKey || nextKey === stableKey) {
            setCalKeyDraftByKey((prev) => ({ ...prev, [stableKey]: stableKey }));
            return;
        }
        const cals = { ...(config.google_calendar?.calendars || {}) };
        if (Object.prototype.hasOwnProperty.call(cals, nextKey)) {
            toast.error(`Calendar '${nextKey}' already exists`);
            setCalKeyDraftByKey((prev) => ({ ...prev, [stableKey]: stableKey }));
            return;
        }
        const copy = { ...cals[stableKey] };
        delete cals[stableKey];
        cals[nextKey] = copy;
        migrateCalendarKeyInContexts(stableKey, nextKey);
        setCalKeyDraftByKey((prev) => {
            const next = { ...prev };
            delete next[stableKey];
            return next;
        });
        onChange({
            ...config,
            google_calendar: { ...(config.google_calendar || {}), calendars: cals }
        });
    };

			    const [editingDestination, setEditingDestination] = useState<string | null>(null);
			    const [destinationForm, setDestinationForm] = useState<any>({});
	        const [emailDefaults, setEmailDefaults] = useState<any>(null);
	        const [emailDefaultsError, setEmailDefaultsError] = useState<string | null>(null);
	        const [showSummaryEmailAdvanced, setShowSummaryEmailAdvanced] = useState(false);
	        const [showTranscriptEmailAdvanced, setShowTranscriptEmailAdvanced] = useState(false);
	        const [templateModalOpen, setTemplateModalOpen] = useState(false);
	        const [templateModalTool, setTemplateModalTool] = useState<'send_email_summary' | 'request_transcript'>('send_email_summary');
	        const [internalAliasesDraftByRowId, setInternalAliasesDraftByRowId] = useState<Record<string, string>>({});
	        const internalAliasesCommittedRef = useRef<Record<string, string>>({});
	        const [internalExtKeyDraftByRowId, setInternalExtKeyDraftByRowId] = useState<Record<string, string>>({});
	        const internalExtKeyCommittedRef = useRef<Record<string, string>>({});
	        const [calKeyDraftByKey, setCalKeyDraftByKey] = useState<Record<string, string>>({});

        // ─── Google Calendar per-row Identity & Verify state ───────────────────
        // Identity = SA metadata read from the configured credentials_path file.
        // Auto-fetched when credentials_path becomes non-empty so the operator
        // sees the SA email (to share their calendar with) without having to
        // crack open the JSON. Cached by the (key + path) tuple so we don't
        // refetch when unrelated state changes trigger a re-render.
        type CalIdentityState = {
            path: string;          // path we tried — used as dedupe key
            loading?: boolean;
            client_email?: string;
            client_id?: string;
            project_id?: string;
            error?: string;
            errorCode?: string;
        };
        const [identityByKey, setIdentityByKey] = useState<Record<string, CalIdentityState>>({});

        // Verify = result of POSTing /verify. Either explicit success (with
        // calendar metadata) or an explicit error code the UI can render
        // meaningfully (forbidden_calendar = "share the calendar with the SA",
        // calendar_not_found = "wrong calendar id", auth_failed = "bad creds",
        // dwd_not_configured = "set up DWD at admin.google.com", etc.).
        type CalVerifyState = {
            loading?: boolean;
            ok?: boolean;
            calendar_summary?: string;
            calendar_actual_timezone?: string;
            drift_warning?: string;
            errorCode?: string;
            errorMessage?: string;
            // Fingerprint of the (path, calendar_id, timezone, subject) tuple
            // verified. Lets the blur-handler invalidate stale results when
            // the operator edits a field after a successful verify — without
            // this, the row keeps showing the old green check even though the
            // current values haven't been verified. Codex feedback #3.
            verifiedFor?: string;
        };
        const _verifyFingerprint = (cal: any): string => {
            const path = ((cal?.credentials_path || '') as string).trim();
            const calId = ((cal?.calendar_id || '') as string).trim();
            const tz = ((cal?.timezone || '') as string).trim();
            const subj = ((cal?.subject || '') as string).trim();
            return `${path}|${calId}|${tz}|${subj}`;
        };
        const [verifyByKey, setVerifyByKey] = useState<Record<string, CalVerifyState>>({});

        const loadCalendarIdentity = async (key: string, path: string) => {
            try {
                // Send the current form-state path as a query param so /info
                // doesn't load stale identity from the persisted YAML for
                // unsaved manual path edits. Codex feedback #4.
                const res = await axios.get(
                    `/api/config/google-calendar/${encodeURIComponent(key)}/info`,
                    { params: { credentials_path: path } }
                );
                setIdentityByKey((prev) => ({
                    ...prev,
                    [key]: {
                        path,
                        loading: false,
                        client_email: res.data?.client_email || '',
                        client_id: res.data?.client_id || '',
                        project_id: res.data?.project_id || '',
                    },
                }));
            } catch (err: any) {
                const detail = err?.response?.data?.detail || {};
                setIdentityByKey((prev) => ({
                    ...prev,
                    [key]: {
                        path,
                        loading: false,
                        error: detail.message || err?.message || 'Failed to read identity',
                        errorCode: detail.error_code || 'unknown',
                    },
                }));
            }
        };

        // Fire identity fetch whenever a calendar's credentials_path changes
        // to a new non-empty value. Effect dep is the JSON of (key, path)
        // pairs only, so unrelated state changes (e.g. typing in calendar_id)
        // don't retrigger.
        const calendarPathsKey = (() => {
            const cals = config?.google_calendar?.calendars || {};
            return JSON.stringify(
                Object.entries(cals).map(([k, v]: [string, any]) => [k, (v?.credentials_path || '').trim()])
            );
        })();
        useEffect(() => {
            const cals = config?.google_calendar?.calendars || {};
            const liveKeys = new Set(Object.keys(cals));
            // Clear cached identity entries whose calendar key was deleted
            // OR whose credentials_path was cleared — without this, stale
            // SA email/client ID lingers in the UI after the operator
            // removes the path or the whole calendar. CodeRabbit minor
            // finding.
            setIdentityByKey((prev) => {
                let changed = false;
                const next = { ...prev };
                for (const cachedKey of Object.keys(prev)) {
                    if (!liveKeys.has(cachedKey)) {
                        delete next[cachedKey];
                        changed = true;
                        continue;
                    }
                    const path = (((cals as any)[cachedKey] || {})?.credentials_path || '').trim();
                    if (!path) {
                        delete next[cachedKey];
                        changed = true;
                    }
                }
                return changed ? next : prev;
            });
            Object.entries(cals).forEach(([key, val]: [string, any]) => {
                const path = (val?.credentials_path || '').trim();
                if (!path) return;
                const existing = identityByKey[key];
                if (existing && existing.path === path && !existing.error) return; // already loaded
                setIdentityByKey((prev) => ({ ...prev, [key]: { path, loading: true } }));
                loadCalendarIdentity(key, path);
            });
            // eslint-disable-next-line react-hooks/exhaustive-deps
        }, [calendarPathsKey]);

        const runCalendarVerify = async (key: string) => {
            const cal = config?.google_calendar?.calendars?.[key] || {};
            setVerifyByKey((prev) => ({ ...prev, [key]: { loading: true } }));
            try {
                const res = await axios.post(
                    `/api/config/google-calendar/${encodeURIComponent(key)}/verify`,
                    {
                        // Send current form state so we test what the operator sees,
                        // not the persisted YAML (which may be stale from unsaved edits).
                        credentials_path: cal.credentials_path || '',
                        calendar_id: cal.calendar_id || '',
                        timezone: cal.timezone || '',
                        // Include DWD subject if set — verify endpoint maps
                        // unauthorized_client / invalid_grant token failures to
                        // the dwd_not_configured error code so the UI can render
                        // a helpful "set up DWD at admin.google.com" message.
                        subject: cal.subject || '',
                    }
                );
                // Auto-fill Timezone if it's empty: verify already returned the
                // calendar's actual timezone, no need to make the operator copy
                // it manually. Only fill when empty — never overwrite a value
                // the operator has explicitly typed (e.g. they want a different
                // display tz than the calendar's own tz).
                const calActualTz = res.data?.calendar_actual_timezone || '';
                if (calActualTz && !((cal as any)?.timezone || '').trim()) {
                    const cals = { ...(config.google_calendar?.calendars || {}) };
                    cals[key] = { ...(cals[key] || {}), timezone: calActualTz };
                    onChange({
                        ...config,
                        google_calendar: { ...(config.google_calendar || {}), calendars: cals },
                    });
                }
                // Use the post-autofill cal snapshot for the fingerprint, not
                // the pre-verify one — if Verify just auto-filled timezone
                // from calendar_actual_timezone, the original `cal` object
                // was missing that field and the fingerprint would record a
                // pre-autofill state. The next field-change check would
                // immediately invalidate the green check on something that
                // didn't actually change. CodeRabbit minor finding.
                const calForFingerprint = (calActualTz && !((cal as any)?.timezone || '').trim())
                    ? { ...cal, timezone: calActualTz }
                    : cal;
                setVerifyByKey((prev) => ({
                    ...prev,
                    [key]: {
                        loading: false,
                        ok: true,
                        calendar_summary: res.data?.calendar_summary || '',
                        calendar_actual_timezone: calActualTz,
                        drift_warning: res.data?.drift_warning || undefined,
                        verifiedFor: _verifyFingerprint(calForFingerprint),
                    },
                }));
            } catch (err: any) {
                const detail = err?.response?.data?.detail || {};
                setVerifyByKey((prev) => ({
                    ...prev,
                    [key]: {
                        loading: false,
                        ok: false,
                        errorCode: detail.error_code || 'unknown',
                        errorMessage: detail.message || err?.message || 'Verify failed',
                    },
                }));
            }
        };

        const copyToClipboard = async (text: string) => {
            // navigator.clipboard.writeText returns a Promise — without
            // awaiting we'd toast.success before the copy completes, and
            // async failures (permission denied, no clipboard available)
            // would never reach the catch. CodeRabbit minor finding.
            if (!navigator.clipboard) {
                toast.error('Clipboard not available in this browser');
                return;
            }
            try {
                await navigator.clipboard.writeText(text);
                toast.success('Copied');
            } catch {
                toast.error('Copy failed');
            }
        };

        // ─── Google Calendar JSON upload + auto-discover ─────────────────────
        // Uploading a JSON file POSTs it to /api/config/google-calendar/credentials,
        // which writes the file under secrets/ with a stable hash filename,
        // extracts the SA identity, and discovers which calendars the SA has
        // been shared with. Discovery results are used to auto-fill calendar_id
        // and timezone (when exactly 1 calendar is accessible) or to surface
        // an actionable picker (when multiple).
        type CalDiscoveredCalendar = {
            id: string;
            summary: string;
            timezone: string;
            access_role: string;
        };
        type CalUploadState = {
            uploading?: boolean;
            error?: string;
            replaced?: boolean;
            discovery?: {
                ok: boolean;
                calendars?: CalDiscoveredCalendar[];
                error_code?: string;
                error_message?: string;
            };
        };
        const [uploadByKey, setUploadByKey] = useState<Record<string, CalUploadState>>({});
        const calendarFileInputRefs = useRef<Record<string, HTMLInputElement | null>>({});

        const handleCalendarFileUpload = async (key: string, file: File) => {
            setUploadByKey((prev) => ({ ...prev, [key]: { uploading: true } }));
            try {
                const formData = new FormData();
                formData.append('file', file);
                const res = await axios.post(
                    '/api/config/google-calendar/credentials',
                    formData,
                    { headers: { 'Content-Type': 'multipart/form-data' } }
                );
                const data = res.data || {};
                const containerPath: string = data.container_path || '';
                const wasReplaced: boolean = !!data.replaced;
                const discovery = data.discovery || {};
                const accessibleCalendars: CalDiscoveredCalendar[] = (discovery.calendars || []) as CalDiscoveredCalendar[];

                // Build the next config in one shot. Always set credentials_path; conditionally
                // set calendar_id + timezone if discovery returned exactly 1 reachable calendar.
                const cals = { ...(config.google_calendar?.calendars || {}) };
                const existing = cals[key] || {};
                const updates: any = {
                    ...existing,
                    credentials_path: containerPath,
                };
                if (discovery.ok && accessibleCalendars.length === 1) {
                    const only = accessibleCalendars[0];
                    updates.calendar_id = only.id;
                    updates.timezone = only.timezone;
                }
                cals[key] = updates;
                onChange({
                    ...config,
                    google_calendar: { ...(config.google_calendar || {}), calendars: cals },
                });

                setUploadByKey((prev) => ({
                    ...prev,
                    [key]: {
                        uploading: false,
                        replaced: wasReplaced,
                        discovery: {
                            ok: !!discovery.ok,
                            calendars: accessibleCalendars,
                            error_code: discovery.error_code,
                            error_message: discovery.error_message,
                        },
                    },
                }));

                // Seed identityByKey from the upload response so the badge
                // displays immediately, without waiting for /info to read
                // PERSISTED config (which doesn't have the new path until
                // the operator clicks Save). Bug observed during 0b smoke test.
                if (data.identity?.client_email) {
                    setIdentityByKey((prev) => ({
                        ...prev,
                        [key]: {
                            path: containerPath,
                            loading: false,
                            client_email: data.identity.client_email,
                            client_id: data.identity.client_id,
                            project_id: data.identity.project_id,
                        },
                    }));
                }

                // Toast feedback. Prioritize the most useful message.
                if (wasReplaced) {
                    toast.success(`Replaced credentials for ${data.identity?.client_email || 'service account'}`);
                } else if (discovery.ok && accessibleCalendars.length === 1) {
                    toast.success(`Uploaded — auto-filled calendar "${accessibleCalendars[0].summary || accessibleCalendars[0].id}"`);
                } else if (discovery.ok && accessibleCalendars.length > 1) {
                    toast.success(`Uploaded — ${accessibleCalendars.length} calendars accessible, pick one below`);
                } else if (discovery.ok && accessibleCalendars.length === 0) {
                    toast.message('Uploaded — no subscribed calendars yet', {
                        description: `If you've already shared a calendar with the SA, paste the Calendar ID and click Verify. Otherwise share your calendar with ${data.identity?.client_email} first.`,
                    });
                } else {
                    toast.warning('Uploaded — could not discover calendars', {
                        description: discovery.error_message || 'See row for details.',
                    });
                }
            } catch (err: any) {
                const detail = err?.response?.data?.detail || {};
                const errMessage = detail.message || err?.message || 'Upload failed';
                setUploadByKey((prev) => ({
                    ...prev,
                    [key]: { uploading: false, error: errMessage },
                }));
                toast.error('Upload failed', { description: errMessage });
            }
        };

        // Operator picks a calendar from the multi-calendar accessible list →
        // fill calendar_id + timezone in the row's form state.
        const pickDiscoveredCalendar = (key: string, picked: CalDiscoveredCalendar) => {
            const cals = { ...(config.google_calendar?.calendars || {}) };
            cals[key] = {
                ...(cals[key] || {}),
                calendar_id: picked.id,
                timezone: picked.timezone,
            };
            onChange({
                ...config,
                google_calendar: { ...(config.google_calendar || {}), calendars: cals },
            });
            toast.success(`Selected "${picked.summary || picked.id}"`);
        };

        // ─── Microsoft Calendar device-code OAuth ────────────────────────────
        type MsCalendarSummary = {
            id: string;
            name: string;
            is_default_calendar?: boolean;
        };
        type MsDeviceState = {
            flowId?: string;
            status?: 'idle' | 'pending' | 'success' | 'error' | 'expired';
            userCode?: string;
            verificationUri?: string;
            message?: string;
            error?: string;
        };
        type MsVerifyState = {
            loading?: boolean;
            ok?: boolean;
            message?: string;
            calendarName?: string;
            errorCode?: string;
        };
        const [msDevice, setMsDevice] = useState<MsDeviceState>({ status: 'idle' });
        const [msCalendars, setMsCalendars] = useState<MsCalendarSummary[]>([]);
        const [msVerify, setMsVerify] = useState<MsVerifyState>({});

        const microsoftAccount = config.microsoft_calendar?.accounts?.default || {};
        const updateMicrosoftAccount = (patch: Record<string, any>) => {
            const accounts = { ...(config.microsoft_calendar?.accounts || {}) };
            accounts.default = { ...(accounts.default || {}), ...patch };
            onChange({
                ...config,
                microsoft_calendar: {
                    ...(config.microsoft_calendar || {}),
                    accounts,
                },
            });
        };

        const startMicrosoftDeviceFlow = async () => {
            const account = config.microsoft_calendar?.accounts?.default || {};
            const tenantId = (account.tenant_id || '').trim();
            const clientId = (account.client_id || '').trim();
            if (!tenantId || !clientId) {
                toast.error('Tenant ID and Client ID are required before connecting Microsoft Calendar');
                return;
            }
            try {
                setMsDevice({ status: 'pending' });
                const res = await axios.post('/api/config/microsoft-calendar/device/start', {
                    tenant_id: tenantId,
                    client_id: clientId,
                    account_key: 'default',
                });
                setMsDevice({
                    status: 'pending',
                    flowId: res.data?.flow_id,
                    userCode: res.data?.user_code,
                    verificationUri: res.data?.verification_uri,
                    message: res.data?.message,
                });
            } catch (err: any) {
                const detail = err?.response?.data?.detail || {};
                const message = detail.message || err?.message || 'Could not start Microsoft device-code flow';
                setMsDevice({ status: 'error', error: message });
                toast.error('Microsoft connect failed', { description: message });
            }
        };

        useEffect(() => {
            if (!msDevice.flowId || msDevice.status !== 'pending') return;
            let stopped = false;
            const poll = async () => {
                try {
                    const res = await axios.get(`/api/config/microsoft-calendar/device/status/${encodeURIComponent(msDevice.flowId || '')}`);
                    if (stopped) return;
                    const status = res.data?.status;
                    if (status === 'success') {
                        const result = res.data?.result || {};
                        const calendars: MsCalendarSummary[] = result.calendars || [];
                        const defaultCal = calendars.find((c) => c.is_default_calendar) || calendars[0];
                        setMsCalendars(calendars);
                        updateMicrosoftAccount({
                            token_cache_path: result.token_cache_path || '',
                            user_principal_name: result.user_principal_name || '',
                            calendar_id: defaultCal?.id || microsoftAccount.calendar_id || '',
                            timezone: microsoftAccount.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
                        });
                        setMsDevice((prev) => ({ ...prev, status: 'success' }));
                        toast.success('Microsoft Calendar connected');
                        return;
                    }
                    if (status === 'error' || status === 'expired') {
                        const message = res.data?.error?.message || (status === 'expired' ? 'Device code expired' : 'Microsoft authorization failed');
                        setMsDevice((prev) => ({ ...prev, status, error: message }));
                    }
                } catch (err: any) {
                    if (!stopped) {
                        setMsDevice((prev) => ({ ...prev, status: 'error', error: err?.message || 'Device-code polling failed' }));
                    }
                }
            };
            const timer = setInterval(poll, 3000);
            poll();
            return () => {
                stopped = true;
                clearInterval(timer);
            };
            // eslint-disable-next-line react-hooks/exhaustive-deps
        }, [msDevice.flowId, msDevice.status]);

        const runMicrosoftVerify = async () => {
            const account = config.microsoft_calendar?.accounts?.default || {};
            setMsVerify({ loading: true });
            try {
                const res = await axios.post('/api/config/microsoft-calendar/verify', {
                    account_key: 'default',
                    tenant_id: account.tenant_id || '',
                    client_id: account.client_id || '',
                    token_cache_path: account.token_cache_path || '',
                    user_principal_name: account.user_principal_name || '',
                    calendar_id: account.calendar_id || '',
                    timezone: account.timezone || '',
                });
                setMsVerify({
                    loading: false,
                    ok: true,
                    message: `Reachable: ${res.data?.calendar_name || account.calendar_id || 'calendar'}`,
                    calendarName: res.data?.calendar_name || '',
                });
                toast.success('Microsoft Calendar verified');
            } catch (err: any) {
                const detail = err?.response?.data?.detail || {};
                setMsVerify({
                    loading: false,
                    ok: false,
                    errorCode: detail.error_code || 'unknown',
                    message: detail.message || err?.message || 'Verify failed',
                });
            }
        };

        const disconnectMicrosoftCalendar = async () => {
            const account = config.microsoft_calendar?.accounts?.default || {};
            try {
                await axios.post('/api/config/microsoft-calendar/disconnect', {
                    account_key: 'default',
                    token_cache_path: account.token_cache_path || '',
                });
                updateMicrosoftAccount({
                    token_cache_path: '',
                    user_principal_name: '',
                    calendar_id: '',
                });
                setMsVerify({});
                setMsCalendars([]);
                setMsDevice({ status: 'idle' });
                toast.success('Microsoft Calendar disconnected');
            } catch (err: any) {
                const detail = err?.response?.data?.detail || {};
                toast.error('Disconnect failed', { description: detail.message || err?.message || 'Unknown error' });
            }
        };
	        const [showHangupExpert, setShowHangupExpert] = useState<boolean>(() => {
	            try {
	                const v = localStorage.getItem(HANGUP_EXPERT_STORAGE_KEY);
	                if (v === 'true') return true;
                if (v === 'false') return false;
            } catch {
                // Ignore storage failures (private browsing, blocked storage, etc.).
            }
            return false;
        });
        const [showLiveAgentsExpert, setShowLiveAgentsExpert] = useState<boolean>(() =>
            Object.values(config?.extensions?.internal || {}).some((ext: any) => hasLiveAgentExpertSettings(ext))
        );
        const [showSummaryEmailExpert, setShowSummaryEmailExpert] = useState<boolean>(() => Boolean(config?.send_email_summary?.from_name));
        const [showTranscriptEmailExpert, setShowTranscriptEmailExpert] = useState<boolean>(() => Boolean(config?.request_transcript?.from_name));

	        useEffect(() => {
	            try {
	                localStorage.setItem(HANGUP_EXPERT_STORAGE_KEY, showHangupExpert ? 'true' : 'false');
	            } catch {
	                // Ignore.
	            }
	        }, [showHangupExpert]);

	        useEffect(() => {
	            const internal = config?.extensions?.internal || {};
	            const rowIdsInUse = new Set<string>();

	            setInternalAliasesDraftByRowId((prev) => {
	                let next: Record<string, string> | null = null;
	                const ensureNext = () => (next ??= { ...prev });

	                Object.entries(internal).forEach(([key, ext]: [string, any]) => {
	                    const rowId = getInternalExtRowId(key);
	                    rowIdsInUse.add(rowId);

	                    const committed = Array.isArray(ext?.aliases) ? ext.aliases.join(', ') : String(ext?.aliases || '');
	                    const prevCommitted = internalAliasesCommittedRef.current[rowId];
	                    const draft = prev[rowId];

	                    internalAliasesCommittedRef.current[rowId] = committed;

	                    // Sync committed -> draft when (a) draft is uninitialized, or (b) draft matches the
	                    // last committed value (meaning the user hasn't started editing).
	                    if (draft === undefined || (prevCommitted !== undefined && draft === prevCommitted && draft !== committed)) {
	                        ensureNext()[rowId] = committed;
	                    }
	                });

	                // Drop draft rows that no longer exist.
	                Object.keys(prev).forEach((rowId) => {
	                    if (!rowIdsInUse.has(rowId)) {
	                        ensureNext();
	                        delete next![rowId];
	                        delete internalAliasesCommittedRef.current[rowId];
	                    }
	                });

	                return next ?? prev;
	            });

	            setInternalExtKeyDraftByRowId((prev) => {
	                let next: Record<string, string> | null = null;
	                const ensureNext = () => (next ??= { ...prev });

	                Object.entries(internal).forEach(([key]) => {
	                    const rowId = getInternalExtRowId(key);
	                    rowIdsInUse.add(rowId);

	                    const prevCommitted = internalExtKeyCommittedRef.current[rowId];
	                    const draft = prev[rowId];
	                    internalExtKeyCommittedRef.current[rowId] = key;

	                    if (draft === undefined || (prevCommitted !== undefined && draft === prevCommitted && draft !== key)) {
	                        ensureNext()[rowId] = key;
	                    }
	                });

	                Object.keys(prev).forEach((rowId) => {
	                    if (!rowIdsInUse.has(rowId)) {
	                        ensureNext();
	                        delete next![rowId];
	                        delete internalExtKeyCommittedRef.current[rowId];
	                    }
	                });

	                return next ?? prev;
	            });
	        }, [config?.extensions?.internal]);

	        // Per-context override draft rows
	        const [summaryAdminCtx, setSummaryAdminCtx] = useState('');
	        const [summaryAdminVal, setSummaryAdminVal] = useState('');
	        const [summaryFromCtx, setSummaryFromCtx] = useState('');
        const [summaryFromVal, setSummaryFromVal] = useState('');
        const [transcriptAdminCtx, setTranscriptAdminCtx] = useState('');
        const [transcriptAdminVal, setTranscriptAdminVal] = useState('');
        const [transcriptFromCtx, setTranscriptFromCtx] = useState('');
        const [transcriptFromVal, setTranscriptFromVal] = useState('');

        // Keep a stable React key per internal extension row so key renames don't blow away focus/cursor.
        const internalExtRowIdsRef = useRef<Record<string, string>>({});
        const internalExtRowIdCounterRef = useRef(0);
        const internalExtRowMetaRef = useRef<Record<string, { autoDerivedKey: boolean }>>({});
        const internalExtRenameToastKeyRef = useRef<string>('');
        const [internalExtStatusByRowId, setInternalExtStatusByRowId] = useState<Record<string, any>>({});
        const internalExtStatusControllersRef = useRef<Record<string, AbortController>>({});
        const internalExtStatusTimeoutsRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
        const liveAgentsCount = Object.keys(config.extensions?.internal || {}).length;
        const hasLiveAgents = liveAgentsCount > 0;
        const hasLiveAgentDestinationOverride = Boolean((config.transfer?.live_agent_destination_key || '').trim());
        const [showLiveAgentRoutingAdvanced, setShowLiveAgentRoutingAdvanced] = useState<boolean>(
            () => !hasLiveAgents || hasLiveAgentDestinationOverride
        );

        const isNumericKey = (k: string) => /^\d+$/.test((k || '').trim());

        const extractNumericExtensionKeyFromDialString = (raw: string): string => {
            const s = (raw || '').trim();
            if (!s) return '';

            const digitsOnly = s.match(/^(\d+)$/);
            if (digitsOnly) return digitsOnly[1];

            // Common dial-string formats: PJSIP/2765, SIP/6000, Local/2765@from-internal
            const m = s.match(/(?:^|[^A-Za-z0-9])(?:PJSIP|SIP|IAX2|DAHDI|LOCAL)\/(\d+)/i);
            return m ? (m[1] || '') : '';
        };

        const getInternalExtRowId = (configKey: string) => {
            const map = internalExtRowIdsRef.current;
            if (!map[configKey]) {
                internalExtRowIdCounterRef.current += 1;
                map[configKey] = `internal-ext-row-${internalExtRowIdCounterRef.current}`;
            }
            const rowId = map[configKey];
            if (!internalExtRowMetaRef.current[rowId]) {
                internalExtRowMetaRef.current[rowId] = { autoDerivedKey: false };
            }
            return rowId;
        };

        const getInternalExtRowMeta = (rowId: string) => {
            if (!internalExtRowMetaRef.current[rowId]) {
                internalExtRowMetaRef.current[rowId] = { autoDerivedKey: false };
            }
            return internalExtRowMetaRef.current[rowId];
        };

        const moveInternalExtRowId = (fromKey: string, toKey: string) => {
            const map = internalExtRowIdsRef.current;
            if (fromKey === toKey) return;
            if (!map[fromKey]) {
                getInternalExtRowId(fromKey);
            }
            if (!map[toKey] && map[fromKey]) {
                map[toKey] = map[fromKey];
            }
            delete map[fromKey];
        };

        const deleteInternalExtRowId = (k: string) => {
            const rowId = internalExtRowIdsRef.current[k];
            if (rowId) {
                delete internalExtRowMetaRef.current[rowId];
            }
            delete internalExtRowIdsRef.current[k];
        };

        const renameInternalExtensionKey = (fromKey: string, rawNextKey: string) => {
            const nextKey = (rawNextKey || '').trim();
            if (!nextKey || nextKey === fromKey) return;
            if (!isNumericKey(nextKey)) {
                toast.error('Live Agent extension keys must be numeric.');
                return;
            }

            const existing = { ...(config.extensions?.internal || {}) };
            if (Object.prototype.hasOwnProperty.call(existing, nextKey)) {
                toast.error(`An extension with key '${nextKey}' already exists.`);
                return;
            }

            const rowId = getInternalExtRowId(fromKey);
            getInternalExtRowMeta(rowId).autoDerivedKey = false;

            const renamed: Record<string, any> = {};
            Object.entries(existing).forEach(([k, v]) => {
                if (k === fromKey) renamed[nextKey] = v;
                else renamed[k] = v;
            });
            moveInternalExtRowId(fromKey, nextKey);
            updateNestedConfig('extensions', 'internal', renamed);
        };

        const commitInternalExtensionKeyDraft = (rowId: string, fromKey: string) => {
            const nextKey = String(internalExtKeyDraftByRowId[rowId] ?? fromKey).trim();
            if (!nextKey || nextKey === fromKey) {
                setInternalExtKeyDraftByRowId((prev) => ({ ...prev, [rowId]: fromKey }));
                return;
            }
            if (!isNumericKey(nextKey)) {
                toast.error('Live Agent extension keys must be numeric.');
                setInternalExtKeyDraftByRowId((prev) => ({ ...prev, [rowId]: fromKey }));
                return;
            }
            renameInternalExtensionKey(fromKey, nextKey);
        };

        const _statusDotClass = (status: string, loading: boolean) => {
            if (loading) return 'bg-muted animate-pulse';
            if (status === 'available') return 'bg-emerald-500';
            if (status === 'busy') return 'bg-red-500';
            return 'bg-amber-500';
        };

        const _statusPillClass = (status: string, loading: boolean) => {
            if (loading) return 'border-border bg-muted/40 text-muted-foreground';
            if (status === 'available') return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700';
            if (status === 'busy') return 'border-red-500/30 bg-red-500/10 text-red-700';
            return 'border-amber-500/30 bg-amber-500/10 text-amber-800';
        };

        const _statusLabel = (status: string, loading: boolean, checkedAt?: string) => {
            if (loading) return 'Checking';
            if (!checkedAt) return 'Check status';
            if (status === 'available') return 'Available';
            if (status === 'busy') return 'Busy';
            return 'Unknown';
        };
        const checkLiveAgentStatus = async (rowId: string, key: string, ext: any, isAuto: boolean = false) => {
            const dialString = String(ext?.dial_string || '');
            const tech = String(ext?.device_state_tech || 'auto');
            const numericKey = isNumericKey(key) ? String(key).trim() : extractNumericExtensionKeyFromDialString(dialString);
            if (!numericKey) {
                if (!isAuto) toast.error('Set a numeric extension or dial string (e.g. PJSIP/2765) before checking status.');
                return;
            }

            internalExtStatusControllersRef.current[rowId]?.abort();
            const previousTimeout = internalExtStatusTimeoutsRef.current[rowId];
            if (previousTimeout) {
                clearTimeout(previousTimeout);
                delete internalExtStatusTimeoutsRef.current[rowId];
            }
            const controller = new AbortController();
            internalExtStatusControllersRef.current[rowId] = controller;
            internalExtStatusTimeoutsRef.current[rowId] = setTimeout(() => controller.abort(), 10000);

            // In auto-mode, skip showing loading to avoid UI flicker
            if (!isAuto) {
                setInternalExtStatusByRowId((prev) => ({
                    ...prev,
                    [rowId]: { ...(prev[rowId] || {}), loading: true, error: '' },
                }));
            }

            try {
                const res = await axios.get('/api/system/ari/extension-status', {
                    params: { key: numericKey, device_state_tech: tech, dial_string: dialString },
                    signal: controller.signal,
                });
                if (internalExtStatusControllersRef.current[rowId] !== controller) return;
                const data = res?.data || {};
                setInternalExtStatusByRowId((prev) => ({
                    ...prev,
                    [rowId]: {
                        loading: false,
                        success: Boolean(data.success),
                        status: String(data.status || 'unknown'),
                        state: String(data.state || ''),
                        source: String(data.source || ''),
                        checkedAt: new Date().toISOString(),
                        error: String(data.error || ''),
                    },
                }));
                if (!data.success && data.error && !isAuto) {
                    toast.error(String(data.error));
                }
            } catch (e: any) {
                if (controller.signal.aborted || e?.name === 'CanceledError' || e?.code === 'ERR_CANCELED' || axios.isCancel?.(e)) {
                    if (internalExtStatusControllersRef.current[rowId] === controller) {
                        setInternalExtStatusByRowId((prev) => ({
                            ...prev,
                            [rowId]: { ...(prev[rowId] || {}), loading: false },
                        }));
                    }
                    return;
                }
                if (internalExtStatusControllersRef.current[rowId] !== controller) return;
                const err = e?.response?.data?.detail || e?.message || 'Status check failed.';
                setInternalExtStatusByRowId((prev) => ({
                    ...prev,
                    [rowId]: { ...(prev[rowId] || {}), loading: false, success: false, status: 'unknown', error: String(err) },
                }));
                if (!isAuto) {
                    toast.error(String(err));
                }
            } finally {
                if (internalExtStatusControllersRef.current[rowId] === controller) {
                    delete internalExtStatusControllersRef.current[rowId];
                }
                const timeoutId = internalExtStatusTimeoutsRef.current[rowId];
                if (timeoutId) {
                    clearTimeout(timeoutId);
                    delete internalExtStatusTimeoutsRef.current[rowId];
                }
            }
        };

        const checkLiveAgentStatusRef = useRef(checkLiveAgentStatus);
        const internalExtsRef = useRef(config.extensions?.internal || {});

        useEffect(() => {
            checkLiveAgentStatusRef.current = checkLiveAgentStatus;
            internalExtsRef.current = config.extensions?.internal || {};
        });

        useEffect(() => {
            let mounted = true;

            const poll = () => {
                if (!mounted) return;
                const extensions = internalExtsRef.current;
                Object.entries(extensions).forEach(([key, ext]) => {
                    const rowId = getInternalExtRowId(key);
                    checkLiveAgentStatusRef.current(rowId, key, ext, true);
                });
            };

            const initialTimer = setTimeout(poll, 1500);
            const intervalTimer = setInterval(poll, 60000);

            return () => {
                mounted = false;
                clearTimeout(initialTimer);
                clearInterval(intervalTimer);
                Object.values(internalExtStatusControllersRef.current).forEach((controller) => controller.abort());
                Object.values(internalExtStatusTimeoutsRef.current).forEach((timeoutId) => clearTimeout(timeoutId));
                internalExtStatusControllersRef.current = {};
                internalExtStatusTimeoutsRef.current = {};
            };
        }, []);

    const updateConfig = (field: string, value: any) => {
        onChange({ ...config, [field]: value });
    };

    const updateNestedConfig = (section: string, field: string, value: any) => {
        onChange({
            ...config,
            [section]: {
                ...config[section],
                [field]: value
            }
        });
    };

    const unsetNestedConfig = (section: string, field: string) => {
        const next = { ...config };
        const current = next[section];
        if (!current || typeof current !== 'object') return;
        const copy = { ...current };
        delete copy[field];
        next[section] = copy;
        onChange(next);
    };

    const updateByContextMap = (section: string, key: string, contextName: string, value: string) => {
        const next = { ...config };
        const toolCfg = { ...(next[section] || {}) };
        const mapKey = `${key}_by_context`;
        const existing = (toolCfg as any)[mapKey];
        const map = (existing && typeof existing === 'object' && !Array.isArray(existing)) ? { ...existing } : {};
        (map as any)[contextName] = value;
        (toolCfg as any)[mapKey] = map;
        next[section] = toolCfg;
        onChange(next);
    };

    const updateHangupPolicy = (field: string, value: any) => {
        const current = config.hangup_call?.policy || {};
        updateNestedConfig('hangup_call', 'policy', { ...current, [field]: value });
    };

    const updateHangupMarkers = (field: 'end_call' | 'assistant_farewell', value: string[]) => {
        const current = config.hangup_call?.policy || {};
        const currentMarkers =
            current.markers && typeof current.markers === 'object' && !Array.isArray(current.markers)
                ? current.markers
                : {};
        const markers = { ...(currentMarkers || {}) };
        if (!value || value.length === 0) {
            delete (markers as any)[field];
        } else {
            (markers as any)[field] = value;
        }
        const nextPolicy = { ...current };
        if (Object.keys(markers).length === 0) {
            delete (nextPolicy as any).markers;
        } else {
            (nextPolicy as any).markers = markers;
        }
        updateNestedConfig('hangup_call', 'policy', nextPolicy);
    };

    const removeByContextKey = (section: string, key: string, contextName: string) => {
        const next = { ...config };
        const toolCfg = { ...(next[section] || {}) };
        const mapKey = `${key}_by_context`;
        const existing = (toolCfg as any)[mapKey];
        if (!existing || typeof existing !== 'object' || Array.isArray(existing)) return;
        const map = { ...existing };
        delete (map as any)[contextName];
        (toolCfg as any)[mapKey] = map;
        next[section] = toolCfg;
        onChange(next);
    };

    const contextNames = Object.keys(contexts || {}).slice().sort();
    const endCallMarkerText = renderMarkerList(
        config.hangup_call?.policy?.markers?.end_call,
        DEFAULT_HANGUP_END_CALL_MARKERS
    );
    const assistantFarewellMarkerText = renderMarkerList(
        config.hangup_call?.policy?.markers?.assistant_farewell,
        DEFAULT_HANGUP_ASSISTANT_FAREWELL_MARKERS
    );
    const [endCallMarkerDraft, setEndCallMarkerDraft] = useState<string>(endCallMarkerText);
    const [assistantFarewellMarkerDraft, setAssistantFarewellMarkerDraft] = useState<string>(assistantFarewellMarkerText);

    useEffect(() => {
        setEndCallMarkerDraft(endCallMarkerText);
    }, [endCallMarkerText]);

    useEffect(() => {
        setAssistantFarewellMarkerDraft(assistantFarewellMarkerText);
    }, [assistantFarewellMarkerText]);

    const getDefaultEmailTemplate = (tool: 'send_email_summary' | 'request_transcript') => {
        if (!emailDefaults) return '';
        return tool === 'send_email_summary' ? (emailDefaults.send_email_summary || '') : (emailDefaults.request_transcript || '');
    };

    const isTemplateOverrideEnabled = (section: string) => {
        const raw = config?.[section]?.html_template;
        return typeof raw === 'string' && raw.trim().length > 0;
    };

    const loadEmailDefaults = async () => {
        try {
            setEmailDefaultsError(null);
            const res = await axios.get('/api/tools/email-templates/defaults');
            setEmailDefaults(res.data || null);
            return true;
        } catch (e: any) {
            setEmailDefaults(null);
            setEmailDefaultsError(e?.response?.data?.detail || e?.message || 'Failed to load defaults.');
            return false;
        }
    };

    useEffect(() => {
        let cancelled = false;
        const load = async () => {
            try {
                if (cancelled) return;
                await loadEmailDefaults();
            } catch {
                // ignore
            }
        };
        load();
        return () => {
            cancelled = true;
        };
    }, []);

    useEffect(() => {
        // If user has no Live Agents configured or already has an override set, keep advanced visible.
        if (!hasLiveAgents || hasLiveAgentDestinationOverride) {
            setShowLiveAgentRoutingAdvanced(true);
        }
    }, [hasLiveAgents, hasLiveAgentDestinationOverride]);

    useEffect(() => {
        const policy = config?.hangup_call?.policy;
        if (!policy || typeof policy !== 'object') return;

        const mode = String((policy as any).mode || '').trim();
        const markers = (policy as any).markers;
        const hasMarkers =
            markers &&
            typeof markers === 'object' &&
            !Array.isArray(markers) &&
            ((Array.isArray((markers as any).end_call) && (markers as any).end_call.length > 0) ||
                (Array.isArray((markers as any).assistant_farewell) && (markers as any).assistant_farewell.length > 0));

        // Auto-open only when the operator has configured meaningful overrides AND the user hasn't
        // explicitly chosen a persisted preference for showing/hiding this expert section.
        if (!mode && !hasMarkers) return;
        try {
            const persisted = localStorage.getItem(HANGUP_EXPERT_STORAGE_KEY);
            if (persisted === null) {
                setShowHangupExpert(true);
            }
        } catch {
            setShowHangupExpert(true);
        }
    }, [
        config?.hangup_call?.policy?.mode,
        config?.hangup_call?.policy?.markers?.end_call,
        config?.hangup_call?.policy?.markers?.assistant_farewell,
    ]);

    useEffect(() => {
        if (Object.values(config?.extensions?.internal || {}).some((ext: any) => hasLiveAgentExpertSettings(ext))) {
            setShowLiveAgentsExpert(true);
        }
    }, [config?.extensions?.internal]);

    useEffect(() => {
        if (config?.send_email_summary?.from_name) {
            setShowSummaryEmailExpert(true);
        }
    }, [config?.send_email_summary?.from_name]);

    useEffect(() => {
        if (config?.request_transcript?.from_name) {
            setShowTranscriptEmailExpert(true);
        }
    }, [config?.request_transcript?.from_name]);

    const openTemplateModal = (tool: 'send_email_summary' | 'request_transcript') => {
        setTemplateModalTool(tool);
        setTemplateModalOpen(true);
        if (!emailDefaults && !emailDefaultsError) {
            loadEmailDefaults();
        }
    };

    const handleAttendedTransferToggle = (enabled: boolean) => {
        const existing = config.attended_transfer || {};
        const next: any = { ...existing, enabled };
        if (enabled) {
            // Populate sensible defaults out of the box (user can override).
            if (next.moh_class == null) next.moh_class = 'default';
            if (next.dial_timeout_seconds == null) next.dial_timeout_seconds = 30;
            if (next.accept_timeout_seconds == null) next.accept_timeout_seconds = 15;
            if (next.tts_timeout_seconds == null) next.tts_timeout_seconds = 8;
            if (next.delivery_mode == null) next.delivery_mode = 'stream';
            if (next.stream_fallback_to_file == null) next.stream_fallback_to_file = true;
            if (next.screening_mode == null) next.screening_mode = 'basic_tts';
            if (next.ai_briefing_timeout_seconds == null) next.ai_briefing_timeout_seconds = 2;
            if (next.ai_briefing_language == null) next.ai_briefing_language = '';
            if (next.ai_briefing_intro_template == null) next.ai_briefing_intro_template = DEFAULT_ATTENDED_AI_BRIEFING_INTRO_TEMPLATE;
            if (next.caller_screening_prompt == null) next.caller_screening_prompt = 'Before I connect you, please say your name and the reason for your call.';
            if (next.caller_screening_max_seconds == null) next.caller_screening_max_seconds = 6;
            if (next.caller_screening_silence_ms == null) next.caller_screening_silence_ms = 1200;
            if (next.accept_digit == null) next.accept_digit = '1';
            if (next.decline_digit == null) next.decline_digit = '2';
            if (next.announcement_template == null) next.announcement_template = DEFAULT_ATTENDED_ANNOUNCEMENT_TEMPLATE;
            if (next.agent_accept_prompt_template == null) next.agent_accept_prompt_template = DEFAULT_ATTENDED_AGENT_DTMF_PROMPT_TEMPLATE;
            if (next.caller_connected_prompt == null) next.caller_connected_prompt = DEFAULT_ATTENDED_CALLER_CONNECTED_PROMPT;
            if (next.caller_declined_prompt == null) next.caller_declined_prompt = DEFAULT_ATTENDED_CALLER_DECLINED_PROMPT;
        }
        onChange({ ...config, attended_transfer: next });
    };

    // Transfer Destinations Management
    const handleEditDestination = (key: string, data: any) => {
        setEditingDestination(key);
        setDestinationForm({ key, ...data });
    };

    const handleAddDestination = () => {
        setEditingDestination('new_destination');
        setDestinationForm({ key: '', type: 'extension', target: '', description: '', attended_allowed: false, live_agent: false });
    };

    const handleSaveDestination = () => {
        if (!destinationForm.key) return;

        const destinations = { ...(config.transfer?.destinations || {}) };

        // If renaming, delete old key
        if (editingDestination !== 'new_destination' && editingDestination !== destinationForm.key) {
            delete destinations[editingDestination!];
        }

        const { key, ...data } = destinationForm;
        destinations[key] = data;

        updateNestedConfig('transfer', 'destinations', destinations);
        setEditingDestination(null);
    };

    const handleDeleteDestination = (key: string) => {
        const destinations = { ...(config.transfer?.destinations || {}) };
        delete destinations[key];
        updateNestedConfig('transfer', 'destinations', destinations);
    };

    return (
        <div className="space-y-8">
            {/* AI Identity & General Settings */}
            <div className="space-y-4 border-b border-border pb-6">
                <h3 className="text-lg font-semibold text-primary">General Settings</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <FormInput
                        label="Agent Name"
                        value={config.ai_identity?.name || 'AI Agent'}
                        onChange={(e) => updateNestedConfig('ai_identity', 'name', e.target.value)}
                        tooltip="The name displayed on the caller's phone during transfers."
                    />
                    <FormInput
                        label="Agent Number"
                        value={config.ai_identity?.number || '6789'}
                        onChange={(e) => updateNestedConfig('ai_identity', 'number', e.target.value)}
                        tooltip="The virtual extension number used by the AI agent."
                    />
                    <FormInput
                        label="Default Action Timeout (s)"
                        type="number"
                        value={config.default_action_timeout || 30}
                        onChange={(e) => updateConfig('default_action_timeout', parseInt(e.target.value))}
                        tooltip="Time to wait for tool execution before timing out."
                    />
                </div>
            </div>

            {/* Telephony Tools */}
            <div className="space-y-6">
                <h3 className="text-lg font-semibold text-primary">Telephony Tools</h3>

                {/* Transfer Tool */}
                <div className="border border-border rounded-lg p-4 bg-card/50">
                    <div className="flex justify-between items-center mb-4">
                        <FormSwitch
                            label="Transfer Tool"
                            description="Allow transferring calls to extensions, queues, or ring groups."
                            checked={config.transfer?.enabled ?? true}
                            onChange={(e) => updateNestedConfig('transfer', 'enabled', e.target.checked)}
                            className="mb-0 border-0 p-0 bg-transparent"
                        />
                    </div>

	                    {config.transfer?.enabled !== false && (
	                        <div className="mt-4 space-y-4">
	                            <FormInput
	                                label="Channel Technology"
	                                value={config.transfer?.technology || 'SIP'}
	                                onChange={(e) => updateNestedConfig('transfer', 'technology', e.target.value)}
	                                tooltip="Channel technology for extension transfers (SIP, PJSIP, IAX2, etc.). Default: SIP"
	                                placeholder="SIP"
	                            />
                                <FormSwitch
                                    label="Advanced: Route Live Agent via Destination"
                                    description={
                                        hasLiveAgents
                                            ? "Default: live_agent_transfer uses Live Agents. Enable only if you want live-agent requests routed to a transfer destination (queue/ring group/extension)."
                                            : "No Live Agents configured. Enable to select which transfer destination should handle live-agent requests."
                                    }
                                    checked={showLiveAgentRoutingAdvanced}
                                    onChange={(e) => {
                                        const enabled = e.target.checked;
                                        setShowLiveAgentRoutingAdvanced(enabled);
                                        if (!enabled) {
                                            // Disable override behavior and reduce config confusion.
                                            unsetNestedConfig('transfer', 'live_agent_destination_key');
                                        }
                                    }}
                                    className="mb-0 border border-border rounded-lg p-3 bg-background/50"
                                />
                                {showLiveAgentRoutingAdvanced && (
	                                <FormSelect
	                                    label="Live Agent Destination Key (Advanced)"
	                                    value={config.transfer?.live_agent_destination_key || ''}
	                                    onChange={(e) => updateNestedConfig('transfer', 'live_agent_destination_key', e.target.value)}
	                                    options={[
	                                        { value: '', label: 'Not set (auto: destinations.live_agent or key live_agent)' },
	                                        ...Object.entries(config.transfer?.destinations || {})
	                                            .filter(([key, dest]: [string, any]) => key === 'live_agent' || Boolean(dest?.live_agent))
	                                            .map(([key]) => key)
	                                            .sort()
	                                            .map((key) => ({ value: key, label: key })),
	                                    ]}
	                                    tooltip="Advanced/legacy override for live_agent_transfer. When set, live-agent requests route to this destination key instead of Live Agents."
	                                />
                                )}
	                            <div className="flex justify-between items-center">
	                                <FormLabel>Destinations</FormLabel>
	                                <button
	                                    onClick={handleAddDestination}
                                    className="text-xs flex items-center bg-secondary px-2 py-1 rounded hover:bg-secondary/80 transition-colors"
                                >
                                    <Plus className="w-3 h-3 mr-1" /> Add Destination
                                </button>
                            </div>

                            <div className="grid grid-cols-1 gap-2">
	                                {Object.entries(config.transfer?.destinations || {}).map(([key, dest]: [string, any]) => {
	                                    // AAVA-199: Guard against null/undefined destinations
	                                    if (!dest || typeof dest !== 'object') return null;
	                                    const destType = dest.type || 'extension';
	                                    const destTarget = dest.target || '';
	                                    const destDescription = dest.description || '';
	                                    return (
	                                    <div key={key} className="flex items-center justify-between p-3 bg-accent/30 rounded border border-border/50">
	                                        <div>
	                                            <div className="font-medium text-sm">{key}</div>
	                                            <div className="text-xs text-muted-foreground">
	                                                {destType} • {destTarget} • {destDescription}
	                                                {destType === 'extension' && dest.attended_allowed ? ' • attended' : ''}
	                                                {destType === 'extension' && showLiveAgentRoutingAdvanced && dest.live_agent ? ' • live-agent' : ''}
	                                            </div>
	                                        </div>
	                                        <div className="flex items-center gap-1">
	                                            <button onClick={() => handleEditDestination(key, dest)} className="p-1.5 hover:bg-background rounded text-muted-foreground hover:text-foreground">
	                                                <Settings className="w-4 h-4" />
                                            </button>
                                            <button onClick={() => handleDeleteDestination(key)} className="p-1.5 hover:bg-destructive/10 rounded text-destructive">
                                                <Trash2 className="w-4 h-4" />
                                            </button>
                                        </div>
                                    </div>
                                    );
                                })}
                            </div>
                        </div>
                    )}
                </div>

                {/* Attended (Warm) Transfer */}
                <div className="border border-border rounded-lg p-4 bg-card/50">
                    <FormSwitch
                        label="Attended Transfer (Warm)"
                        description="Warm transfer with MOH, one-way announcement to the agent, and DTMF accept/decline. Requires Local AI Server for TTS. AI Briefing is experimental, requires Local AI Server LLM capability, and falls back to Basic TTS when unavailable."
                        checked={config.attended_transfer?.enabled ?? false}
                        onChange={(e) => handleAttendedTransferToggle(e.target.checked)}
                        className="mb-0 border-0 p-0 bg-transparent"
                    />
                    {config.attended_transfer?.enabled && (
                        <div className="mt-4 pl-4 border-l-2 border-border ml-2 space-y-4">
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <FormSelect
                                    label="Announcement Delivery"
                                    value={config.attended_transfer?.delivery_mode || 'stream'}
                                    onChange={(e) => updateNestedConfig('attended_transfer', 'delivery_mode', e.target.value)}
                                    options={[
                                        { value: 'stream', label: 'Stream via ExternalMedia' },
                                        { value: 'file', label: 'File Playback' },
                                    ]}
                                    tooltip="Stream avoids shared-storage dependency for the called extension leg. File playback keeps the legacy behavior."
                                />
                                <FormSelect
                                    label="Screening Mode"
                                    value={config.attended_transfer?.screening_mode || 'basic_tts'}
                                    onChange={(e) => updateNestedConfig('attended_transfer', 'screening_mode', e.target.value)}
                                    options={[
                                        { value: 'basic_tts', label: 'Basic TTS' },
                                        { value: 'ai_briefing', label: 'AI Briefing (Experimental)' },
                                        { value: 'caller_recording', label: 'Caller Recording' },
                                    ]}
                                    tooltip="Basic TTS uses caller ID and context. AI Briefing is experimental and generates a short AI-written summary from the live conversation using Local AI Server LLM. Caller Recording asks the caller to state their name and reason, then plays that clip to the destination agent."
                                />
                                <FormInput
                                    label="MOH Class"
                                    value={config.attended_transfer?.moh_class || 'default'}
                                    onChange={(e) => updateNestedConfig('attended_transfer', 'moh_class', e.target.value)}
                                    tooltip="Asterisk Music On Hold class used while the destination is being dialed."
                                />
                                <FormInput
                                    label="Dial Timeout (seconds)"
                                    type="number"
                                    value={config.attended_transfer?.dial_timeout_seconds ?? 30}
                                    onChange={(e) => updateNestedConfig('attended_transfer', 'dial_timeout_seconds', parseInt(e.target.value) || 30)}
                                    tooltip="How long to ring the destination before aborting the transfer."
                                />
                                <FormInput
                                    label="Accept Timeout (seconds)"
                                    type="number"
                                    value={config.attended_transfer?.accept_timeout_seconds ?? 15}
                                    onChange={(e) => updateNestedConfig('attended_transfer', 'accept_timeout_seconds', parseInt(e.target.value) || 15)}
                                    tooltip="How long to wait for the destination to press a DTMF digit."
                                />
                                <FormInput
                                    label="TTS Timeout (seconds)"
                                    type="number"
                                    value={config.attended_transfer?.tts_timeout_seconds ?? 8}
                                    onChange={(e) => updateNestedConfig('attended_transfer', 'tts_timeout_seconds', parseInt(e.target.value) || 8)}
                                    tooltip="Max time to wait for Local AI Server TTS per prompt."
                                />
                                <FormInput
                                    label="Accept Digit"
                                    value={config.attended_transfer?.accept_digit || '1'}
                                    onChange={(e) => updateNestedConfig('attended_transfer', 'accept_digit', e.target.value)}
                                />
                                <FormInput
                                    label="Decline Digit"
                                    value={config.attended_transfer?.decline_digit || '2'}
                                    onChange={(e) => updateNestedConfig('attended_transfer', 'decline_digit', e.target.value)}
                                />
                                {config.attended_transfer?.screening_mode === 'caller_recording' && (
                                    <>
                                        <div className="md:col-span-2 space-y-2">
                                            <FormLabel tooltip="Spoken to the caller before screening capture begins. The AI/provider speaks this prompt, then the engine records the next caller utterance. Ensure your deployment satisfies any local caller notice or consent requirements before enabling caller recording.">
                                                Caller Screening Prompt
                                            </FormLabel>
                                            <textarea
                                                className="w-full p-3 rounded-md border border-input bg-transparent text-sm min-h-[80px] focus:outline-none focus:ring-1 focus:ring-ring"
                                                value={config.attended_transfer?.caller_screening_prompt || 'Before I connect you, please say your name and the reason for your call.'}
                                                onChange={(e) => updateNestedConfig('attended_transfer', 'caller_screening_prompt', e.target.value)}
                                                placeholder="Before I connect you, please say your name and the reason for your call."
                                            />
                                        </div>
                                        <FormInput
                                            label="Max Recording Seconds"
                                            type="number"
                                            value={config.attended_transfer?.caller_screening_max_seconds ?? 6}
                                            onChange={(e) => updateNestedConfig('attended_transfer', 'caller_screening_max_seconds', parseInt(e.target.value) || 6)}
                                            tooltip="Maximum length of the caller screening clip before it is finalized."
                                        />
                                        <FormInput
                                            label="Silence Timeout (ms)"
                                            type="number"
                                            value={config.attended_transfer?.caller_screening_silence_ms ?? 1200}
                                            onChange={(e) => updateNestedConfig('attended_transfer', 'caller_screening_silence_ms', parseInt(e.target.value) || 1200)}
                                            tooltip="How much trailing silence ends the screening capture."
                                        />
                                    </>
                                )}
                                {config.attended_transfer?.screening_mode === 'ai_briefing' && (
                                    <>
                                        <div className="md:col-span-2 space-y-2">
                                            <FormLabel tooltip="Spoken to the destination agent before the AI-generated summary. AI Briefing is experimental, requires Local AI Server LLM capability, and falls back to Basic TTS when summary generation is unavailable.">
                                                AI Briefing Intro Template (Experimental)
                                            </FormLabel>
                                            <textarea
                                                className="w-full p-3 rounded-md border border-input bg-transparent text-sm min-h-[80px] focus:outline-none focus:ring-1 focus:ring-ring"
                                                value={config.attended_transfer?.ai_briefing_intro_template ?? DEFAULT_ATTENDED_AI_BRIEFING_INTRO_TEMPLATE}
                                                onChange={(e) => updateNestedConfig('attended_transfer', 'ai_briefing_intro_template', e.target.value)}
                                                placeholder={DEFAULT_ATTENDED_AI_BRIEFING_INTRO_TEMPLATE}
                                            />
                                        </div>
                                        <FormInput
                                            label="Briefing Language"
                                            type="text"
                                            value={config.attended_transfer?.ai_briefing_language ?? ''}
                                            onChange={(e) => updateNestedConfig('attended_transfer', 'ai_briefing_language', e.target.value)}
                                            placeholder="e.g. German, French, Spanish (leave blank for English)"
                                            tooltip="Language for the AI-generated briefing text. Must match the language your Local AI Server TTS voice speaks. Leave blank for English."
                                        />
                                        <FormInput
                                            label="AI Briefing Timeout (seconds, Experimental)"
                                            type="number"
                                            value={config.attended_transfer?.ai_briefing_timeout_seconds ?? 2}
                                            onChange={(e) => updateNestedConfig('attended_transfer', 'ai_briefing_timeout_seconds', parseFloat(e.target.value) || 2)}
                                            tooltip="Maximum time to wait for the experimental Local AI Server LLM briefing before falling back to Basic TTS."
                                        />
                                    </>
                                )}
                            </div>

                            {config.attended_transfer?.screening_mode === 'basic_tts' && (
                                <div className="space-y-2">
                                    <FormLabel tooltip="Spoken to the destination agent (one-way) before requesting DTMF acceptance. Placeholders: {caller_display}, {caller_name}, {caller_number}, {context_name}, {destination_description}, {screening_summary}, {screened_caller_name}, {screened_call_reason}, {screened_caller_display}, {screened_reason_display}.">
                                        Agent Announcement Template
                                    </FormLabel>
                                    <textarea
                                        className="w-full p-3 rounded-md border border-input bg-transparent text-sm min-h-[100px] focus:outline-none focus:ring-1 focus:ring-ring"
                                        value={config.attended_transfer?.announcement_template ?? DEFAULT_ATTENDED_ANNOUNCEMENT_TEMPLATE}
                                        onChange={(e) => updateNestedConfig('attended_transfer', 'announcement_template', e.target.value)}
                                        placeholder="Hi, this is Ava. I'm transferring {caller_display} regarding {context_name}."
                                    />
                                </div>
                            )}

                            <div className="border border-border rounded-lg p-4 bg-background/40 space-y-4">
                                <div className="text-sm font-medium">Advanced Prompts</div>
                                <FormSwitch
                                    label="Fallback To File Playback"
                                    description="If helper streaming is unavailable, reuse the legacy file-based playback path for the called extension."
                                    checked={config.attended_transfer?.stream_fallback_to_file ?? true}
                                    onChange={(e) => updateNestedConfig('attended_transfer', 'stream_fallback_to_file', e.target.checked)}
                                    className="mb-0 border-0 p-0 bg-transparent"
                                />
                                <div className="space-y-2">
                                    <FormLabel tooltip="Spoken to the destination agent to request acceptance/decline (DTMF). Supports the same placeholders as the announcement template.">
                                        Agent DTMF Prompt Template
                                    </FormLabel>
                                    <textarea
                                        className="w-full p-3 rounded-md border border-input bg-transparent text-sm min-h-[80px] focus:outline-none focus:ring-1 focus:ring-ring"
                                        value={config.attended_transfer?.agent_accept_prompt_template ?? DEFAULT_ATTENDED_AGENT_DTMF_PROMPT_TEMPLATE}
                                        onChange={(e) => updateNestedConfig('attended_transfer', 'agent_accept_prompt_template', e.target.value)}
                                        placeholder="Press 1 to accept this transfer, or 2 to decline."
                                    />
                                </div>

                                <FormInput
                                    label="Caller Connected Prompt (Optional)"
                                    value={config.attended_transfer?.caller_connected_prompt ?? DEFAULT_ATTENDED_CALLER_CONNECTED_PROMPT}
                                    onChange={(e) => updateNestedConfig('attended_transfer', 'caller_connected_prompt', e.target.value)}
                                    tooltip="Optional phrase spoken to the caller right before bridging to the destination (e.g., 'Connecting you now.')."
                                    placeholder="Connecting you now."
                                />

                                <FormInput
                                    label="Caller Declined Prompt (Optional)"
                                    value={config.attended_transfer?.caller_declined_prompt ?? DEFAULT_ATTENDED_CALLER_DECLINED_PROMPT}
                                    onChange={(e) => updateNestedConfig('attended_transfer', 'caller_declined_prompt', e.target.value)}
                                    tooltip="Spoken to the caller when the destination declines or the attended transfer times out (keeps the conversation moving)."
                                    placeholder="I’m not able to complete that transfer right now. Would you like me to take a message?"
                                />
                            </div>
                        </div>
                    )}
                </div>

                {/* Cancel Transfer */}
                <div className="border border-border rounded-lg p-4 bg-card/50">
                    <FormSwitch
                        label="Cancel Transfer"
                        description="Allow callers to cancel an in-progress transfer."
                        checked={config.cancel_transfer?.enabled ?? true}
                        onChange={(e) => updateNestedConfig('cancel_transfer', 'enabled', e.target.checked)}
                        className="mb-0 border-0 p-0 bg-transparent"
                    />
                    {config.cancel_transfer?.enabled !== false && (
                        <div className="mt-4 pl-4 border-l-2 border-border ml-2">
                            <FormSwitch
                                label="Allow During Ring"
                                checked={config.cancel_transfer?.allow_during_ring ?? true}
                                onChange={(e) => updateNestedConfig('cancel_transfer', 'allow_during_ring', e.target.checked)}
                            />
                        </div>
                    )}
                </div>

                {/* Check Extension Status */}
                <div className="border border-border rounded-lg p-4 bg-card/50">
                    <div className="space-y-2">
                        <FormLabel tooltip="Controls whether availability checks are limited to extensions explicitly configured under Live Agents or Transfer Destinations. Disable only if you intentionally want the model to probe arbitrary extension numbers.">
                            Check Extension Status
                        </FormLabel>
                        <FormSwitch
                            label="Restrict To Configured Extensions"
                            description="Recommended safety guardrail. Prevents the AI from checking arbitrary extension numbers that are not configured under Tools."
                            checked={config.check_extension_status?.restrict_to_configured_extensions ?? true}
                            onChange={(e) => updateNestedConfig('check_extension_status', 'restrict_to_configured_extensions', e.target.checked)}
                            className="mb-0 border-0 p-0 bg-transparent"
                        />
                    </div>
                </div>

                {/* Hangup Call */}
                <div className="border border-border rounded-lg p-4 bg-card/50">
                    <FormSwitch
                        label="Hangup Call"
                        description="Allow the agent to end the call gracefully. Call ending behavior is controlled via context prompts."
                        checked={config.hangup_call?.enabled ?? true}
                        onChange={(e) => updateNestedConfig('hangup_call', 'enabled', e.target.checked)}
                        className="mb-0 border-0 p-0 bg-transparent"
                    />
                    {config.hangup_call?.enabled !== false && (
                        <div className="mt-4 pl-4 border-l-2 border-border ml-2 space-y-4">
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <FormInput
                                    label="Default Farewell Message"
                                    value={config.hangup_call?.farewell_message || ''}
                                    onChange={(e) => updateNestedConfig('hangup_call', 'farewell_message', e.target.value)}
                                    tooltip="Used when the AI calls hangup_call without specifying a farewell. The AI typically provides its own message."
                                />
                                <FormInput
                                    label="Farewell Hangup Delay (seconds)"
                                    type="number"
                                    step="0.5"
                                    value={config.farewell_hangup_delay_sec ?? 2.5}
                                    onChange={(e) => updateConfig('farewell_hangup_delay_sec', parseFloat(e.target.value) || 2.5)}
                                    tooltip="Time to wait after farewell audio before hanging up. Increase if farewell gets cut off."
                                />
                            </div>
                            <p className="text-sm text-muted-foreground">
                                <strong>Note:</strong> Call ending behavior (transcript offers, confirmation flows) is now controlled
                                via context prompts rather than code guardrails. Configure the CALL ENDING PROTOCOL section in your
                                context's system prompt to customize behavior.
                            </p>
                            <div className="border border-amber-300/40 rounded-lg p-3 bg-amber-500/5">
                                <FormSwitch
                                    label="Hangup Expert Settings"
                                    description="Tune guardrail mode and marker dictionaries for intent detection."
                                    checked={showHangupExpert}
                                    onChange={(e) => setShowHangupExpert(e.target.checked)}
                                    className="mb-0 border-0 p-0 bg-transparent"
                                />
                                {showHangupExpert && (
                                    <div className="mt-4 pt-4 border-t border-amber-300/20">
                                        <p className="text-xs text-amber-700 dark:text-amber-400">
                                            Warning: these values directly influence hangup intent matching and fallback behavior.
                                        </p>
                                        <p className="text-xs text-muted-foreground mt-2">
                                            These markers are global defaults. Pipelines can override end-of-call markers per pipeline under <code>Pipelines</code> → <code>LLM Expert Settings</code>.
                                        </p>
                                        {hangupUsage && (
                                            <div className="mt-3 text-xs text-muted-foreground space-y-1">
                                                <div className="font-medium text-foreground">Usage</div>
                                                <div>
                                                    Google Live marker heuristics:{' '}
                                                    <span className="font-mono">
                                                        {hangupUsage.googleLiveMarkersEnabled === null
                                                            ? 'unknown'
                                                            : hangupUsage.googleLiveMarkersEnabled
                                                                ? 'enabled'
                                                                : 'disabled'}
                                                    </span>
                                                </div>
                                                <div>
                                                    Pipelines overriding end-call markers:{' '}
                                                    {hangupUsage.pipelineEndCallOverrides.length > 0
                                                        ? hangupUsage.pipelineEndCallOverrides.join(', ')
                                                        : 'none'}
                                                </div>
                                                <div>
                                                    Pipelines overriding guardrail mode:{' '}
                                                    {hangupUsage.pipelineModeOverrides.length > 0
                                                        ? hangupUsage.pipelineModeOverrides.map((p) => `${p.name}=${p.mode}`).join(', ')
                                                        : 'none'}
                                                </div>
                                                <div>
                                                    Pipelines overriding guardrail enabled:{' '}
                                                    {hangupUsage.pipelineGuardrailOverrides.length > 0
                                                        ? hangupUsage.pipelineGuardrailOverrides
                                                            .map((p) => `${p.name}=${p.enabled ? 'on' : 'off'}`)
                                                            .join(', ')
                                                        : 'none'}
                                                </div>
                                            </div>
                                        )}
                                        <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-4">
                                            <FormSelect
                                                label="Hangup Guardrail Mode"
                                                value={config.hangup_call?.policy?.mode || DEFAULT_HANGUP_POLICY_MODE}
                                                onChange={(e) => updateHangupPolicy('mode', e.target.value)}
                                                tooltip="Controls how strict the engine is when matching end-of-call intent from text: Relaxed matches broader phrasing, Normal balances false positives vs misses, Strict requires stronger matches."
                                                options={[
                                                    { value: 'relaxed', label: 'Relaxed' },
                                                    { value: 'normal', label: 'Normal' },
                                                    { value: 'strict', label: 'Strict' },
                                                ]}
                                            />
                                        </div>
                                        <div className="mt-1 grid grid-cols-1 md:grid-cols-2 gap-4">
                                            <div className="space-y-2">
                                                <FormLabel tooltip="Caller-side phrases that indicate they want to end the call. If a transcript contains one of these markers, the hangup guardrail is more likely to allow call termination.">
                                                    End Call Markers
                                                </FormLabel>
                                                <textarea
                                                    className="w-full p-2 rounded border border-input bg-background text-sm min-h-[120px]"
                                                    value={endCallMarkerDraft}
                                                    onChange={(e) => setEndCallMarkerDraft(e.target.value)}
                                                    onBlur={() => updateHangupMarkers('end_call', parseMarkerList(endCallMarkerDraft))}
                                                />
                                                <p className="text-xs text-muted-foreground">
                                                    One phrase per line. Focus on user intent language (for example, "that&apos;s all", "no thanks", "end call").
                                                </p>
                                            </div>
                                            <div className="space-y-2">
                                                <FormLabel tooltip="Assistant-side phrases used to recognize that the AI has delivered a farewell. Helps fallback logic avoid hanging up before the closing message is complete.">
                                                    Assistant Farewell Markers
                                                </FormLabel>
                                                <textarea
                                                    className="w-full p-2 rounded border border-input bg-background text-sm min-h-[120px]"
                                                    value={assistantFarewellMarkerDraft}
                                                    onChange={(e) => setAssistantFarewellMarkerDraft(e.target.value)}
                                                    onBlur={() => updateHangupMarkers('assistant_farewell', parseMarkerList(assistantFarewellMarkerDraft))}
                                                />
                                                <p className="text-xs text-muted-foreground">
                                                    One phrase per line. Include common assistant closings (for example, "goodbye", "thank you for calling").
                                                </p>
                                            </div>
                                        </div>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}
                </div>

                {/* Leave Voicemail */}
                <div className="border border-border rounded-lg p-4 bg-card/50">
                    <FormSwitch
                        label="Leave Voicemail"
                        description="Transfer caller to a voicemail box."
                        checked={config.leave_voicemail?.enabled ?? true}
                        onChange={(e) => updateNestedConfig('leave_voicemail', 'enabled', e.target.checked)}
                        className="mb-0 border-0 p-0 bg-transparent"
                    />
                    {config.leave_voicemail?.enabled !== false && (
                        <div className="mt-4 pl-4 border-l-2 border-border ml-2">
                            <FormInput
                                label="Voicemail Extension"
                                value={config.leave_voicemail?.extension || ''}
                                onChange={(e) => updateNestedConfig('leave_voicemail', 'extension', e.target.value)}
                            />
                        </div>
                    )}
                </div>

	                {/* Extensions (basic editor) */}
	                <div className="border border-border rounded-lg p-4 bg-card/50">
	                    <div className="flex justify-between items-center mb-4">
	                        <FormLabel>Live Agents</FormLabel>
	                        <button
	                            onClick={() => {
	                                const existing = config.extensions?.internal || {};
	                                let idx = Object.keys(existing).length + 1;
	                                let key = `ext_${idx}`;
	                                while (Object.prototype.hasOwnProperty.call(existing, key)) {
	                                    idx += 1;
	                                    key = `ext_${idx}`;
	                                }
                                    const rowId = getInternalExtRowId(key);
                                    getInternalExtRowMeta(rowId).autoDerivedKey = true;
	                                updateNestedConfig('extensions', 'internal', { ...existing, [key]: { name: '', description: '', dial_string: '', transfer: true, device_state_tech: 'auto', action_type: 'transfer', aliases: [] } });
	                            }}
	                            className="text-xs flex items-center bg-secondary px-2 py-1 rounded hover:bg-secondary/80 transition-colors"
	                        >
	                            <Plus className="w-3 h-3 mr-1" /> Add Live Agent
		                        </button>
		                    </div>
                            <div className="mb-4 border border-amber-300/40 rounded-lg p-3 bg-amber-500/5">
                                <FormSwitch
                                    label="Live Agent Expert Settings"
                                    description="Expose advanced live-agent routing fields for each agent row."
                                    checked={showLiveAgentsExpert}
                                    onChange={(e) => setShowLiveAgentsExpert(e.target.checked)}
                                    className="mb-0 border-0 p-0 bg-transparent"
                                />
                                <p className={`text-xs mt-2 ${showLiveAgentsExpert ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'}`}>
                                    {showLiveAgentsExpert
                                        ? 'Warning: advanced routing fields can change transfer behavior in live calls.'
                                        : 'Advanced fields are visible with defaults and locked until enabled.'}
                                </p>
                            </div>
		                    <div className="space-y-2">
	                        {Object.entries(config.extensions?.internal || {}).map(([key, ext]: [string, any]) => (
                                (() => {
                                    const rowId = getInternalExtRowId(key);
                                    const st = internalExtStatusByRowId[rowId] || {};
                                    const status = String(st.status || 'unknown');
                                    const loading = Boolean(st.loading);
                                    const dotClass = _statusDotClass(status, loading);
                                    const pillClass = _statusPillClass(status, loading);
                                    const label = _statusLabel(status, loading, st.checkedAt);
                                    const titleParts: string[] = [];
                                    titleParts.push('Checks Asterisk ARI device/endpoint state');
                                    titleParts.push('Click to refresh');
                                    if (st.source) titleParts.push(`source=${st.source}`);
                                    if (st.state) titleParts.push(`state=${st.state}`);
                                    if (st.checkedAt) titleParts.push(`checked=${st.checkedAt}`);
                                    if (st.error) titleParts.push(`error=${st.error}`);
                                    const title = titleParts.join(' • ');

                                    return (
	                            <div key={rowId} className="flex flex-col gap-4 p-5 border border-border/60 rounded-lg bg-card/40 hover:bg-card/60 transition-colors shadow-sm">

                                {/* Core Info Row */}
                                <div className="flex flex-col xl:flex-row gap-4 xl:items-end items-start w-full">
	                                <div className="w-full xl:w-24 shrink-0">
                                        <label className="block text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1.5 ml-1">Ext</label>
	                                    <input
	                                        className="w-full border border-input rounded-md px-3 py-2 text-sm bg-background focus:ring-1 focus:ring-ring focus:outline-none transition-shadow"
	                                        placeholder="E.g. 6000"
	                                        value={internalExtKeyDraftByRowId[rowId] ?? String(key || '')}
	                                        onChange={(e) => setInternalExtKeyDraftByRowId((prev) => ({ ...prev, [rowId]: e.target.value }))}
	                                        onBlur={() => commitInternalExtensionKeyDraft(rowId, key)}
	                                        onKeyDown={(e) => {
	                                            if (e.key === 'Enter') {
	                                                e.preventDefault();
	                                                commitInternalExtensionKeyDraft(rowId, key);
	                                                (e.target as HTMLInputElement).blur();
	                                            }
	                                        }}
	                                        title="Numeric extension key used for Live Agent routing. New placeholder keys can be renamed here or auto-derived from the dial string."
	                                    />
	                                </div>
	                                <div className="w-full xl:w-64 shrink-0">
                                        <label className="block text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1.5 ml-1">Agent Name</label>
	                                    <input
	                                        className="w-full border border-input rounded-md px-3 py-2 text-sm bg-background focus:ring-1 focus:ring-ring focus:outline-none transition-shadow"
	                                        placeholder="E.g. Support Team"
                                            value={ext.name || ''}
                                            onChange={(e) => {
                                                const updated = { ...(config.extensions?.internal || {}) };
                                                updated[key] = { ...ext, name: e.target.value };
                                                updateNestedConfig('extensions', 'internal', updated);
                                            }}
                                            title="Agent Name"
                                        />
                                    </div>
	                                <div className="w-full xl:flex-1 shrink-0">
                                        <label className="block text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1.5 ml-1">Dial String</label>
	                                    <input
	                                        className="w-full border border-input rounded-md px-3 py-2 text-sm bg-background focus:ring-1 focus:ring-ring focus:outline-none transition-shadow"
	                                        placeholder="E.g. PJSIP/6000"
	                                        value={ext.dial_string || ''}
	                                        onChange={(e) => {
                                                const nextDial = e.target.value;
	                                            const existing = { ...(config.extensions?.internal || {}) };
	                                            existing[key] = { ...ext, dial_string: nextDial };

                                                const rowId = getInternalExtRowId(key);
                                                const meta = getInternalExtRowMeta(rowId);

                                                const derivedKey = extractNumericExtensionKeyFromDialString(nextDial);
                                                const canAutoRename =
                                                    Boolean(derivedKey) &&
                                                    derivedKey !== key &&
                                                    // Always allow placeholder keys to be renamed.
                                                    (!isNumericKey(key) || meta.autoDerivedKey);

                                                if (canAutoRename) {
                                                    if (Object.prototype.hasOwnProperty.call(existing, derivedKey)) {
                                                        const toastKey = `internal-ext-rename-conflict:${rowId}:${derivedKey}`;
                                                        if (internalExtRenameToastKeyRef.current !== toastKey) {
                                                            internalExtRenameToastKeyRef.current = toastKey;
                                                            toast.error(`An extension with key '${derivedKey}' already exists.`);
                                                        }
                                                    } else {
                                                        meta.autoDerivedKey = true;
                                                        const renamed: Record<string, any> = {};
                                                        Object.entries(existing).forEach(([k, v]) => {
                                                            if (k === key) renamed[derivedKey] = v;
                                                            else renamed[k] = v;
                                                        });
                                                        moveInternalExtRowId(key, derivedKey);
                                                        updateNestedConfig('extensions', 'internal', renamed);
                                                        return;
                                                    }
                                                }

	                                            updateNestedConfig('extensions', 'internal', existing);
	                                        }}
	                                        title="PJSIP/..."
	                                    />
	                                </div>

                                    {/* Action Buttons Compacted into Row 1 */}
                                    <div className="flex items-center gap-3 shrink-0 w-full xl:w-auto xl:justify-end mt-2 xl:mt-0 xl:pb-[1px]">
                                        <button
                                            type="button"
                                            className={`inline-flex items-center justify-center gap-2 px-4 py-2 h-[38px] rounded-md text-xs font-semibold border shadow-sm ${pillClass} hover:opacity-80 transition-opacity`}
                                            title={title}
                                            onClick={() => checkLiveAgentStatus(rowId, key, ext)}
                                        >
                                            {loading ? (
                                                <Loader2 className="w-4 h-4 animate-spin shrink-0" />
                                            ) : (
                                                <span className={`w-2 h-2 rounded-full ${dotClass} shadow-sm shrink-0`} />
                                            )}
                                            <span className="truncate max-w-[120px]">{label}</span>
                                        </button>
                                        
                                        <div className="flex items-center gap-2.5 bg-secondary/30 px-3 py-1.5 h-[38px] rounded-md border border-border/50 shadow-sm shrink-0">
                                            <span className="text-[10px] font-bold tracking-wide uppercase text-muted-foreground pt-[1px]">Enabled</span>
	                                        <FormSwitch
	                                            checked={ext.transfer ?? true}
	                                            onChange={(e) => {
	                                                const updated = { ...(config.extensions?.internal || {}) };
	                                                updated[key] = { ...ext, transfer: e.target.checked };
	                                                updateNestedConfig('extensions', 'internal', updated);
	                                            }}
	                                            className="mb-0 border-0 p-0 bg-transparent flex-shrink-0"
	                                            label=""
	                                            description=""
	                                        />
                                        </div>

                                        <button
                                            onClick={() => {
                                                const updated = { ...(config.extensions?.internal || {}) };
                                                delete updated[key];
                                                deleteInternalExtRowId(key);
                                                updateNestedConfig('extensions', 'internal', updated);
                                            }}
                                            className="h-[38px] w-[38px] flex items-center justify-center text-muted-foreground hover:text-destructive hover:bg-destructive/10 rounded-md transition-colors shrink-0"
                                            title="Delete Extension"
                                        >
                                            <Trash2 className="w-4.5 h-4.5" />
                                        </button>
                                    </div>
                                </div>

                                {/* Full width description row */}
                                <div className="w-full mt-2">
                                    <label className="block text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1.5 ml-1">Description</label>
                                    <input
                                        className="w-full border border-input rounded-md px-3 py-2 text-sm bg-background focus:ring-1 focus:ring-ring focus:outline-none transition-shadow"
                                        placeholder="Describe this extension..."
                                        value={ext.description || ''}
                                        onChange={(e) => {
                                            const updated = { ...(config.extensions?.internal || {}) };
                                            updated[key] = { ...ext, description: e.target.value };
                                            updateNestedConfig('extensions', 'internal', updated);
                                        }}
                                        title="Description"
                                    />
                                </div>

                                {/* Expert Row */}
                                {showLiveAgentsExpert && (
                                    <div className="flex flex-col gap-4 p-5 bg-secondary/30 border border-border/50 rounded-lg mt-3 relative">
                                        <div className="absolute -top-3 left-4 bg-background px-2.5 text-[10px] font-bold text-amber-600 dark:text-amber-500 tracking-wider uppercase rounded-full border border-amber-200 dark:border-amber-900/50 shadow-sm">
                                            Advanced Routing
                                        </div>
                                        <div className="grid grid-cols-1 md:grid-cols-3 gap-5 items-start mt-1">
                                            <div>
                                                <label className="block text-[11px] font-medium text-muted-foreground mb-1.5 uppercase tracking-wider">Device Tech</label>
                                                <select
                                                    className="w-full border border-input rounded-md px-3 py-2 text-sm bg-background focus:ring-1 focus:ring-ring focus:outline-none transition-shadow"
                                                    value={ext.device_state_tech || 'auto'}
                                                    onChange={(e) => {
                                                        const updated = { ...(config.extensions?.internal || {}) };
                                                        updated[key] = { ...ext, device_state_tech: e.target.value };
                                                        updateNestedConfig('extensions', 'internal', updated);
                                                    }}
                                                    title="Device state technology for availability checks"
                                                >
                                                    <option value="auto">auto</option>
                                                    <option value="PJSIP">PJSIP</option>
                                                    <option value="SIP">SIP</option>
                                                    <option value="IAX2">IAX2</option>
                                                    <option value="DAHDI">DAHDI</option>
                                                </select>
                                            </div>
                                            <div>
                                                <label className="block text-[11px] font-medium text-muted-foreground mb-1.5 uppercase tracking-wider">Action Type</label>
                                                <select
                                                    className="w-full border border-input rounded-md px-3 py-2 text-sm bg-background focus:ring-1 focus:ring-ring focus:outline-none transition-shadow disabled:cursor-not-allowed disabled:opacity-50"
                                                    value={ext.action_type || 'transfer'}
                                                    onChange={(e) => {
                                                        const updated = { ...(config.extensions?.internal || {}) };
                                                        updated[key] = { ...ext, action_type: e.target.value };
                                                        updateNestedConfig('extensions', 'internal', updated);
                                                    }}
                                                    title="Action type used when transfer tool resolves this target"
                                                    disabled={!showLiveAgentsExpert}
                                                >
                                                    <option value="transfer">transfer</option>
                                                    <option value="voicemail">voicemail</option>
                                                    <option value="queue">queue</option>
                                                    <option value="ringgroup">ringgroup</option>
                                                </select>
                                            </div>
                                            <div>
                                                <label className="block text-[11px] font-medium text-muted-foreground mb-1.5 uppercase tracking-wider">Aliases (comma-separated)</label>
                                                <input
                                                    className="w-full border border-input rounded-md px-3 py-2 text-sm bg-background focus:ring-1 focus:ring-ring focus:outline-none transition-shadow disabled:cursor-not-allowed disabled:opacity-50"
                                                    placeholder="e.g. support, agent"
                                                    value={internalAliasesDraftByRowId[rowId] ?? (Array.isArray(ext.aliases) ? ext.aliases.join(', ') : (ext.aliases || ''))}
                                                    onChange={(e) => {
                                                        const raw = String(e.target.value || '');
                                                        setInternalAliasesDraftByRowId((prev) => ({ ...prev, [rowId]: raw }));
                                                    }}
                                                    onBlur={() => {
                                                        const raw = internalAliasesDraftByRowId[rowId] ?? '';
                                                        const aliases = String(raw)
                                                            .split(',')
                                                            .map((s) => s.trim())
                                                            .filter(Boolean);
                                                        const committed = aliases.join(', ');

                                                        internalAliasesCommittedRef.current[rowId] = committed;
                                                        setInternalAliasesDraftByRowId((prev) => ({ ...prev, [rowId]: committed }));

                                                        const updated = { ...(config.extensions?.internal || {}) };
                                                        updated[key] = { ...ext, aliases };
                                                        updateNestedConfig('extensions', 'internal', updated);
                                                    }}
                                                    title="Alternative names users can say to target this live agent"
                                                    disabled={!showLiveAgentsExpert}
                                                />
                                            </div>
                                        </div>
                                    </div>
                                )}
	                            </div>
                                    );
                                })()
	                        ))}
	                        {Object.keys(config.extensions?.internal || {}).length === 0 && (
	                            <div className="text-sm text-muted-foreground">No live agents configured.</div>
	                        )}
	                    </div>
	                </div>
            </div>

            {/* Business Tools */}
            <div className="space-y-6 border-t border-border pt-6">
                <h3 className="text-lg font-semibold text-primary">Business Tools</h3>

                {/* Send Email Summary */}
                <div className="border border-border rounded-lg p-4 bg-card/50">
                    <FormSwitch
                        label="Send Email Summary"
                        description="Automatically send a call summary to the admin after each call."
                        checked={config.send_email_summary?.enabled ?? true}
                        onChange={(e) => updateNestedConfig('send_email_summary', 'enabled', e.target.checked)}
                        className="mb-0 border-0 p-0 bg-transparent"
                    />
                    {config.send_email_summary?.enabled !== false && (
                        <div className="mt-4 pl-4 border-l-2 border-border ml-2 grid grid-cols-1 md:grid-cols-2 gap-4">
                            <FormSelect
                                label="Email Provider"
                                options={[
                                    { value: 'auto', label: 'Auto (SMTP → Resend)' },
                                    { value: 'smtp', label: 'SMTP (local mail server)' },
                                    { value: 'resend', label: 'Resend (API)' },
                                ]}
                                value={config.send_email_summary?.provider || 'auto'}
                                onChange={(e) => updateNestedConfig('send_email_summary', 'provider', e.target.value)}
                                tooltip="Auto uses SMTP if SMTP_HOST is configured; otherwise uses Resend if RESEND_API_KEY is set."
                            />
                            <FormInput
                                label="From Email"
                                value={config.send_email_summary?.from_email || ''}
                                onChange={(e) => updateNestedConfig('send_email_summary', 'from_email', e.target.value)}
                            />
                            <FormInput
                                label="From Name"
                                value={config.send_email_summary?.from_name || ''}
                                onChange={(e) => updateNestedConfig('send_email_summary', 'from_name', e.target.value)}
                                placeholder="AI Voice Agent"
                                disabled={!showSummaryEmailExpert}
                            />
                            <FormInput
                                label="Admin Email (Recipient)"
                                value={config.send_email_summary?.admin_email || ''}
                                onChange={(e) => updateNestedConfig('send_email_summary', 'admin_email', e.target.value)}
                            />
                            <FormSwitch
                                label="Include Transcript"
                                checked={config.send_email_summary?.include_transcript ?? true}
                                onChange={(e) => updateNestedConfig('send_email_summary', 'include_transcript', e.target.checked)}
                            />
                            <div className="md:col-span-2 border border-amber-300/40 rounded-lg p-3 bg-amber-500/5">
                                <FormSwitch
                                    label="Email Summary Expert Settings"
                                    description="Enable editing of sender display-name override."
                                    checked={showSummaryEmailExpert}
                                    onChange={(e) => setShowSummaryEmailExpert(e.target.checked)}
                                    className="mb-0 border-0 p-0 bg-transparent"
                                />
                                <p className={`text-xs mt-2 ${showSummaryEmailExpert ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'}`}>
                                    {showSummaryEmailExpert
                                        ? 'Warning: custom sender naming may affect deliverability depending on your mail provider policy.'
                                        : 'From Name is shown with current/default value and is read-only until enabled.'}
                                </p>
                            </div>
                            <div className="md:col-span-2 border-t border-border pt-4 mt-2">
                                <button
                                    type="button"
                                    onClick={() => setShowSummaryEmailAdvanced(!showSummaryEmailAdvanced)}
                                    className="text-sm font-medium text-primary hover:underline"
                                >
                                    {showSummaryEmailAdvanced ? 'Hide' : 'Show'} Advanced Email Format
                                </button>

                                {showSummaryEmailAdvanced && (
                                    <div className="mt-4 space-y-4">
                                        <div className="space-y-2">
                                            <FormLabel>Per-Context Overrides</FormLabel>
                                            <p className="text-xs text-muted-foreground">
                                                Override recipients and sender per context (uses the call’s resolved context name).
                                            </p>
                                        </div>

                                        <div className="space-y-2">
                                            <div className="text-sm font-medium">Admin Email Overrides</div>
                                            {Object.entries(config.send_email_summary?.admin_email_by_context || {}).length === 0 ? (
                                                <div className="text-xs text-muted-foreground">No overrides configured.</div>
                                            ) : (
                                                <div className="space-y-2">
                                                    {Object.entries(config.send_email_summary?.admin_email_by_context || {}).map(([ctx, val]: [string, any]) => (
                                                        <div key={`summary-admin-${ctx}`} className="flex items-center gap-2">
                                                            <div className="text-xs w-40 truncate" title={ctx}>{ctx}</div>
                                                            <input
                                                                className="flex-1 border rounded px-2 py-1 text-sm bg-transparent"
                                                                value={String(val ?? '')}
                                                                onChange={(e) => updateByContextMap('send_email_summary', 'admin_email', ctx, e.target.value)}
                                                                placeholder="admin@yourdomain.com"
                                                            />
                                                            <button
                                                                type="button"
                                                                onClick={() => removeByContextKey('send_email_summary', 'admin_email', ctx)}
                                                                className="px-2 py-1 text-xs border rounded hover:bg-accent"
                                                            >
                                                                Remove
                                                            </button>
                                                        </div>
                                                    ))}
                                                </div>
                                            )}

                                            <div className="flex items-center gap-2">
                                                <select
                                                    className="border rounded px-2 py-1 text-sm bg-transparent"
                                                    value={summaryAdminCtx}
                                                    onChange={(e) => setSummaryAdminCtx(e.target.value)}
                                                >
                                                    <option value="">Select context…</option>
                                                    {contextNames.map((c) => (
                                                        <option key={`summary-admin-opt-${c}`} value={c}>{c}</option>
                                                    ))}
                                                </select>
                                                <input
                                                    className="flex-1 border rounded px-2 py-1 text-sm bg-transparent"
                                                    value={summaryAdminVal}
                                                    onChange={(e) => setSummaryAdminVal(e.target.value)}
                                                    placeholder="admin@yourdomain.com"
                                                />
                                                <button
                                                    type="button"
                                                    onClick={() => {
                                                        if (!summaryAdminCtx || !summaryAdminVal) return;
                                                        updateByContextMap('send_email_summary', 'admin_email', summaryAdminCtx, summaryAdminVal);
                                                        setSummaryAdminCtx('');
                                                        setSummaryAdminVal('');
                                                    }}
                                                    className="px-3 py-1 text-xs rounded bg-primary text-primary-foreground hover:bg-primary/90"
                                                >
                                                    Add
                                                </button>
                                            </div>
                                        </div>

                                        <div className="space-y-2">
                                            <div className="text-sm font-medium">From Email Overrides</div>
                                            {Object.entries(config.send_email_summary?.from_email_by_context || {}).length === 0 ? (
                                                <div className="text-xs text-muted-foreground">No overrides configured.</div>
                                            ) : (
                                                <div className="space-y-2">
                                                    {Object.entries(config.send_email_summary?.from_email_by_context || {}).map(([ctx, val]: [string, any]) => (
                                                        <div key={`summary-from-${ctx}`} className="flex items-center gap-2">
                                                            <div className="text-xs w-40 truncate" title={ctx}>{ctx}</div>
                                                            <input
                                                                className="flex-1 border rounded px-2 py-1 text-sm bg-transparent"
                                                                value={String(val ?? '')}
                                                                onChange={(e) => updateByContextMap('send_email_summary', 'from_email', ctx, e.target.value)}
                                                                placeholder="agent@yourdomain.com"
                                                            />
                                                            <button
                                                                type="button"
                                                                onClick={() => removeByContextKey('send_email_summary', 'from_email', ctx)}
                                                                className="px-2 py-1 text-xs border rounded hover:bg-accent"
                                                            >
                                                                Remove
                                                            </button>
                                                        </div>
                                                    ))}
                                                </div>
                                            )}

                                            <div className="flex items-center gap-2">
                                                <select
                                                    className="border rounded px-2 py-1 text-sm bg-transparent"
                                                    value={summaryFromCtx}
                                                    onChange={(e) => setSummaryFromCtx(e.target.value)}
                                                >
                                                    <option value="">Select context…</option>
                                                    {contextNames.map((c) => (
                                                        <option key={`summary-from-opt-${c}`} value={c}>{c}</option>
                                                    ))}
                                                </select>
                                                <input
                                                    className="flex-1 border rounded px-2 py-1 text-sm bg-transparent"
                                                    value={summaryFromVal}
                                                    onChange={(e) => setSummaryFromVal(e.target.value)}
                                                    placeholder="agent@yourdomain.com"
                                                />
                                                <button
                                                    type="button"
                                                    onClick={() => {
                                                        if (!summaryFromCtx || !summaryFromVal) return;
                                                        updateByContextMap('send_email_summary', 'from_email', summaryFromCtx, summaryFromVal);
                                                        setSummaryFromCtx('');
                                                        setSummaryFromVal('');
                                                    }}
                                                    className="px-3 py-1 text-xs rounded bg-primary text-primary-foreground hover:bg-primary/90"
                                                >
                                                    Add
                                                </button>
                                            </div>
                                        </div>

                                        <div className="space-y-2 pt-2 border-t border-border">
                                            <div className="flex items-center justify-between">
                                                <div>
                                                    <div className="text-sm font-medium">HTML Template</div>
                                                    <div className="text-xs text-muted-foreground">Advanced: customize the full email HTML (Jinja2).</div>
                                                </div>
                                                <div className="flex items-center gap-2">
                                                    <button
                                                        type="button"
                                                        onClick={() => openTemplateModal('send_email_summary')}
                                                        className="px-3 py-1 text-xs border rounded hover:bg-accent"
                                                    >
                                                        Edit / Preview
                                                    </button>
                                                </div>
                                            </div>
                                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-2">
                                                <FormInput
                                                    label="Subject Prefix (Optional)"
                                                    value={config.send_email_summary?.subject_prefix || ''}
                                                    onChange={(e) => updateNestedConfig('send_email_summary', 'subject_prefix', e.target.value)}
                                                    placeholder="[AAVA] "
                                                    tooltip="Prepended to the email subject. A space is automatically added if missing."
                                                />
                                                <FormSwitch
                                                    label="Include Context Tag in Subject"
                                                    checked={config.send_email_summary?.include_context_in_subject ?? true}
                                                    onChange={(e) => updateNestedConfig('send_email_summary', 'include_context_in_subject', e.target.checked)}
                                                    description="If enabled, subjects include a prefix like [support] or [demo_deepgram]."
                                                />
                                            </div>
                                            <div className="text-xs text-muted-foreground">
                                                Status: {isTemplateOverrideEnabled('send_email_summary') ? 'Custom template enabled' : 'Using default template'}
                                            </div>
                                        </div>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}
                </div>

                {/* Request Transcript */}
                <div className="border border-border rounded-lg p-4 bg-card/50">
                    <FormSwitch
                        label="Request Transcript"
                        description="Allow callers to request a transcript via email."
                        checked={config.request_transcript?.enabled ?? true}
                        onChange={(e) => updateNestedConfig('request_transcript', 'enabled', e.target.checked)}
                        className="mb-0 border-0 p-0 bg-transparent"
                    />
                    {config.request_transcript?.enabled !== false && (
                        <div className="mt-4 pl-4 border-l-2 border-border ml-2 grid grid-cols-1 md:grid-cols-2 gap-4">
                            <FormSelect
                                label="Email Provider"
                                options={[
                                    { value: 'auto', label: 'Auto (SMTP → Resend)' },
                                    { value: 'smtp', label: 'SMTP (local mail server)' },
                                    { value: 'resend', label: 'Resend (API)' },
                                ]}
                                value={config.request_transcript?.provider || 'auto'}
                                onChange={(e) => updateNestedConfig('request_transcript', 'provider', e.target.value)}
                                tooltip="Auto uses SMTP if SMTP_HOST is configured; otherwise uses Resend if RESEND_API_KEY is set."
                            />
                            <FormInput
                                label="From Email"
                                value={config.request_transcript?.from_email || ''}
                                onChange={(e) => updateNestedConfig('request_transcript', 'from_email', e.target.value)}
                                placeholder="agent@yourdomain.com"
                            />
                            <FormInput
                                label="From Name"
                                value={config.request_transcript?.from_name || ''}
                                onChange={(e) => updateNestedConfig('request_transcript', 'from_name', e.target.value)}
                                placeholder="AI Voice Agent"
                                disabled={!showTranscriptEmailExpert}
                            />
                            <FormInput
                                label="Admin Email (BCC)"
                                value={config.request_transcript?.admin_email || ''}
                                onChange={(e) => updateNestedConfig('request_transcript', 'admin_email', e.target.value)}
                            />
                            <FormSwitch
                                label="Confirm Email"
                                checked={config.request_transcript?.confirm_email ?? true}
                                onChange={(e) => updateNestedConfig('request_transcript', 'confirm_email', e.target.checked)}
                            />
                            <FormSwitch
                                label="Validate Domain"
                                checked={config.request_transcript?.validate_domain ?? true}
                                onChange={(e) => updateNestedConfig('request_transcript', 'validate_domain', e.target.checked)}
                            />
                            <div className="md:col-span-2 border border-amber-300/40 rounded-lg p-3 bg-amber-500/5">
                                <FormSwitch
                                    label="Transcript Expert Settings"
                                    description="Enable editing of transcript sender display-name override."
                                    checked={showTranscriptEmailExpert}
                                    onChange={(e) => setShowTranscriptEmailExpert(e.target.checked)}
                                    className="mb-0 border-0 p-0 bg-transparent"
                                />
                                <p className={`text-xs mt-2 ${showTranscriptEmailExpert ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'}`}>
                                    {showTranscriptEmailExpert
                                        ? 'Warning: custom sender naming may affect deliverability depending on your mail provider policy.'
                                        : 'From Name is shown with current/default value and is read-only until enabled.'}
                                </p>
                            </div>
                            <div className="md:col-span-2 border-t border-border pt-4 mt-2">
                                <button
                                    type="button"
                                    onClick={() => setShowTranscriptEmailAdvanced(!showTranscriptEmailAdvanced)}
                                    className="text-sm font-medium text-primary hover:underline"
                                >
                                    {showTranscriptEmailAdvanced ? 'Hide' : 'Show'} Advanced Email Format
                                </button>

                                {showTranscriptEmailAdvanced && (
                                    <div className="mt-4 space-y-4">
                                        <div className="space-y-2">
                                            <FormLabel>Per-Context Overrides</FormLabel>
                                            <p className="text-xs text-muted-foreground">
                                                Override BCC (admin) and sender per context.
                                            </p>
                                        </div>

                                        <div className="space-y-2">
                                            <div className="text-sm font-medium">Admin Email (BCC) Overrides</div>
                                            {Object.entries(config.request_transcript?.admin_email_by_context || {}).length === 0 ? (
                                                <div className="text-xs text-muted-foreground">No overrides configured.</div>
                                            ) : (
                                                <div className="space-y-2">
                                                    {Object.entries(config.request_transcript?.admin_email_by_context || {}).map(([ctx, val]: [string, any]) => (
                                                        <div key={`transcript-admin-${ctx}`} className="flex items-center gap-2">
                                                            <div className="text-xs w-40 truncate" title={ctx}>{ctx}</div>
                                                            <input
                                                                className="flex-1 border rounded px-2 py-1 text-sm bg-transparent"
                                                                value={String(val ?? '')}
                                                                onChange={(e) => updateByContextMap('request_transcript', 'admin_email', ctx, e.target.value)}
                                                                placeholder="admin@yourdomain.com"
                                                            />
                                                            <button
                                                                type="button"
                                                                onClick={() => removeByContextKey('request_transcript', 'admin_email', ctx)}
                                                                className="px-2 py-1 text-xs border rounded hover:bg-accent"
                                                            >
                                                                Remove
                                                            </button>
                                                        </div>
                                                    ))}
                                                </div>
                                            )}

                                            <div className="flex items-center gap-2">
                                                <select
                                                    className="border rounded px-2 py-1 text-sm bg-transparent"
                                                    value={transcriptAdminCtx}
                                                    onChange={(e) => setTranscriptAdminCtx(e.target.value)}
                                                >
                                                    <option value="">Select context…</option>
                                                    {contextNames.map((c) => (
                                                        <option key={`transcript-admin-opt-${c}`} value={c}>{c}</option>
                                                    ))}
                                                </select>
                                                <input
                                                    className="flex-1 border rounded px-2 py-1 text-sm bg-transparent"
                                                    value={transcriptAdminVal}
                                                    onChange={(e) => setTranscriptAdminVal(e.target.value)}
                                                    placeholder="admin@yourdomain.com"
                                                />
                                                <button
                                                    type="button"
                                                    onClick={() => {
                                                        if (!transcriptAdminCtx || !transcriptAdminVal) return;
                                                        updateByContextMap('request_transcript', 'admin_email', transcriptAdminCtx, transcriptAdminVal);
                                                        setTranscriptAdminCtx('');
                                                        setTranscriptAdminVal('');
                                                    }}
                                                    className="px-3 py-1 text-xs rounded bg-primary text-primary-foreground hover:bg-primary/90"
                                                >
                                                    Add
                                                </button>
                                            </div>
                                        </div>

                                        <div className="space-y-2">
                                            <div className="text-sm font-medium">From Email Overrides</div>
                                            {Object.entries(config.request_transcript?.from_email_by_context || {}).length === 0 ? (
                                                <div className="text-xs text-muted-foreground">No overrides configured.</div>
                                            ) : (
                                                <div className="space-y-2">
                                                    {Object.entries(config.request_transcript?.from_email_by_context || {}).map(([ctx, val]: [string, any]) => (
                                                        <div key={`transcript-from-${ctx}`} className="flex items-center gap-2">
                                                            <div className="text-xs w-40 truncate" title={ctx}>{ctx}</div>
                                                            <input
                                                                className="flex-1 border rounded px-2 py-1 text-sm bg-transparent"
                                                                value={String(val ?? '')}
                                                                onChange={(e) => updateByContextMap('request_transcript', 'from_email', ctx, e.target.value)}
                                                                placeholder="agent@yourdomain.com"
                                                            />
                                                            <button
                                                                type="button"
                                                                onClick={() => removeByContextKey('request_transcript', 'from_email', ctx)}
                                                                className="px-2 py-1 text-xs border rounded hover:bg-accent"
                                                            >
                                                                Remove
                                                            </button>
                                                        </div>
                                                    ))}
                                                </div>
                                            )}

                                            <div className="flex items-center gap-2">
                                                <select
                                                    className="border rounded px-2 py-1 text-sm bg-transparent"
                                                    value={transcriptFromCtx}
                                                    onChange={(e) => setTranscriptFromCtx(e.target.value)}
                                                >
                                                    <option value="">Select context…</option>
                                                    {contextNames.map((c) => (
                                                        <option key={`transcript-from-opt-${c}`} value={c}>{c}</option>
                                                    ))}
                                                </select>
                                                <input
                                                    className="flex-1 border rounded px-2 py-1 text-sm bg-transparent"
                                                    value={transcriptFromVal}
                                                    onChange={(e) => setTranscriptFromVal(e.target.value)}
                                                    placeholder="agent@yourdomain.com"
                                                />
                                                <button
                                                    type="button"
                                                    onClick={() => {
                                                        if (!transcriptFromCtx || !transcriptFromVal) return;
                                                        updateByContextMap('request_transcript', 'from_email', transcriptFromCtx, transcriptFromVal);
                                                        setTranscriptFromCtx('');
                                                        setTranscriptFromVal('');
                                                    }}
                                                    className="px-3 py-1 text-xs rounded bg-primary text-primary-foreground hover:bg-primary/90"
                                                >
                                                    Add
                                                </button>
                                            </div>
                                        </div>

                                        <div className="space-y-2 pt-2 border-t border-border">
                                            <div className="flex items-center justify-between">
                                                <div>
                                                    <div className="text-sm font-medium">HTML Template</div>
                                                    <div className="text-xs text-muted-foreground">Advanced: customize the full email HTML (Jinja2).</div>
                                                </div>
                                                <div className="flex items-center gap-2">
                                                    <button
                                                        type="button"
                                                        onClick={() => openTemplateModal('request_transcript')}
                                                        className="px-3 py-1 text-xs border rounded hover:bg-accent"
                                                    >
                                                        Edit / Preview
                                                    </button>
                                                </div>
                                            </div>
                                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-2">
                                                <FormInput
                                                    label="Subject Prefix (Optional)"
                                                    value={config.request_transcript?.subject_prefix || ''}
                                                    onChange={(e) => updateNestedConfig('request_transcript', 'subject_prefix', e.target.value)}
                                                    placeholder="[AAVA] "
                                                    tooltip="Prepended to the email subject. A space is automatically added if missing."
                                                />
                                                <FormSwitch
                                                    label="Include Context Tag in Subject"
                                                    checked={config.request_transcript?.include_context_in_subject ?? true}
                                                    onChange={(e) => updateNestedConfig('request_transcript', 'include_context_in_subject', e.target.checked)}
                                                    description="If enabled, subjects include a prefix like [support] or [demo_openai]."
                                                />
                                            </div>
                                            <div className="text-xs text-muted-foreground">
                                                Status: {isTemplateOverrideEnabled('request_transcript') ? 'Custom template enabled' : 'Using default template'}
                                            </div>
                                        </div>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}
                </div>

                {/* Google Calendar */}
                <div className="border border-border rounded-lg p-4 bg-card/50">
                    <FormSwitch
                        label="Google Calendar"
                        description="Enable the Google Calendar tool for listing events, creating events, and finding free slots."
                        checked={config.google_calendar?.enabled ?? false}
                        onChange={(e) => updateNestedConfig('google_calendar', 'enabled', e.target.checked)}
                        className="mb-0 border-0 p-0 bg-transparent"
                    />
                    {config.google_calendar?.enabled && (
                        <div className="mt-4 pl-4 border-l-2 border-border ml-2 space-y-4">
                            {/* Tool-level defaults applied to get_free_slots when the LLM doesn't override per-call.
                                Free prefix blank/absent → free/busy mode (Google native API + working-hours mask).
                                Free prefix non-empty → title-prefix mode using that prefix value.
                                The operator's choice always wins over LLM-supplied per-call values. */}
                            <div>
                                <div className="text-sm font-medium mb-1">Slot-finding defaults</div>
                                <div className="text-xs text-muted-foreground mb-3">
                                    Configures how <code className="px-1 bg-muted rounded">get_free_slots</code> determines availability.
                                    <strong className="block mt-1"> Free prefix non-empty</strong> →
                                    <em> title-prefix mode</em>: the tool scans your calendar for events titled with this prefix
                                    (e.g. "Open 9-5") and treats those windows as available, minus events titled with the busy prefix.
                                    <strong className="block mt-1"> Free prefix blank or unset</strong> →
                                    <em> free/busy mode</em>: the tool uses Google's native free/busy API intersected with a
                                    working-hours mask (Mon–Fri 09:00–17:00 by default; tunable via YAML).
                                    The operator's choice always wins — even if the LLM passes a prefix per-call, the operator
                                    setting takes precedence.
                                </div>
                                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
                                    <FormInput
                                        label="Free prefix"
                                        value={config.google_calendar?.free_prefix ?? ''}
                                        onChange={(e) => updateNestedConfig('google_calendar', 'free_prefix', e.target.value)}
                                        placeholder="(blank = use Google free/busy)"
                                        tooltip={
                                            "Two modes:\n" +
                                            "• Set a prefix (e.g. 'Open'): the tool scans your calendar for events titled with this prefix and treats those windows as your availability. Best when you want explicit, manually curated booking windows.\n" +
                                            "• Leave blank: the tool uses Google's native free/busy API instead. Anything not blocked by an existing event during configured working hours (Mon–Fri 09:00–17:00 by default) is treated as available. Best for operators who don't want to manually create 'Open' events.\n" +
                                            "Working-hours range can be overridden via YAML keys 'working_hours_start' / 'working_hours_end' / 'working_days' under tools.google_calendar."
                                        }
                                    />
                                    <FormInput
                                        label="Busy prefix"
                                        value={config.google_calendar?.busy_prefix ?? ''}
                                        onChange={(e) => updateNestedConfig('google_calendar', 'busy_prefix', e.target.value)}
                                        placeholder="Busy"
                                        tooltip="Only used in title-prefix mode. Events titled with this prefix block availability inside the open windows. Default: 'Busy'. Ignored when Free prefix is blank (free/busy mode uses any non-prefix-matching event as a busy block via Google's native API)."
                                    />
                                    <FormInput
                                        label="Default slot duration (minutes)"
                                        type="number"
                                        value={(config.google_calendar?.min_slot_duration_minutes ?? '').toString()}
                                        onChange={(e) => {
                                            const raw = e.target.value;
                                            const parsed = raw === '' ? undefined : parseInt(raw, 10);
                                            updateNestedConfig(
                                                'google_calendar',
                                                'min_slot_duration_minutes',
                                                Number.isFinite(parsed) ? parsed : undefined
                                            );
                                        }}
                                        placeholder="30"
                                        tooltip="Slot length in minutes if the LLM doesn't pass duration. Default: 30."
                                    />
                                    <FormInput
                                        label="Max slots returned"
                                        type="number"
                                        value={(config.google_calendar?.max_slots_returned ?? '').toString()}
                                        onChange={(e) => {
                                            const raw = e.target.value;
                                            const parsed = raw === '' ? undefined : parseInt(raw, 10);
                                            updateNestedConfig(
                                                'google_calendar',
                                                'max_slots_returned',
                                                Number.isFinite(parsed) ? parsed : undefined
                                            );
                                        }}
                                        placeholder="3"
                                        tooltip="Cap on how many slot start-times get_free_slots returns to the AI. Default: 3. Without a cap, a multi-day open window can return 20+ slots and the AI may read all of them aloud, producing minutes of robotic monologue. The tool also adds a 'showing N of M' nudge to the response so the AI summarizes 2–3 options to the caller. Set to 0 to disable the cap."
                                    />
                                </div>
                            </div>

                            <div>
                                <div className="text-sm font-medium mb-1">Calendars</div>
                                <div className="text-xs text-muted-foreground mb-3">
                                    Define one or more named calendars. Per-context selection is configured on the Contexts page.
                                </div>
                                <div className="space-y-2">
                                    {Object.entries(config.google_calendar?.calendars || {}).map(([key, val]: [string, any]) => (
                                        <div key={key} className="grid grid-cols-1 md:grid-cols-12 gap-2 items-end border border-border rounded p-2 bg-card/30">
                                            <div className="md:col-span-2">
                                                <FormInput
                                                    label="Key"
                                                    value={calKeyDraftByKey[key] ?? key}
                                                    onChange={(e) => setCalKeyDraftByKey((prev) => ({ ...prev, [key]: e.target.value }))}
                                                    onBlur={() => commitCalendarKeyDraft(key)}
                                                    onKeyDown={(e: any) => { if (e.key === 'Enter') { e.preventDefault(); commitCalendarKeyDraft(key); } }}
                                                    placeholder="work"
                                                    tooltip="A short name for this calendar (e.g. 'work', 'personal'). Used to reference this calendar in contexts."
                                                />
                                            </div>
                                            <div className="md:col-span-4">
                                                <FormInput
                                                    label="Credentials Path"
                                                    value={(val as any)?.credentials_path || ''}
                                                    onChange={(e) => {
                                                        const cals = { ...(config.google_calendar?.calendars || {}) };
                                                        cals[key] = { ...(cals[key] || {}), credentials_path: e.target.value };
                                                        onChange({ ...config, google_calendar: { ...(config.google_calendar || {}), calendars: cals } });
                                                    }}
                                                    placeholder="/app/secrets/service-account.json"
                                                    tooltip="Path to the Google service account JSON key file. Tip: use the Upload JSON button below the row to skip the manual SCP step — uploading also auto-fills Calendar ID and Timezone."
                                                />
                                                {/* Hidden file input — triggered by the Upload button in the
                                                    identity/verify sub-row below. Kept here next to the path
                                                    field so the ref is colocated with what it ultimately fills. */}
                                                <input
                                                    ref={(el) => { calendarFileInputRefs.current[key] = el; }}
                                                    type="file"
                                                    accept="application/json,.json"
                                                    className="hidden"
                                                    onChange={(e) => {
                                                        const f = e.target.files?.[0];
                                                        if (f) {
                                                            handleCalendarFileUpload(key, f);
                                                        }
                                                        // Reset so re-uploading the same file fires onChange again
                                                        if (e.target) e.target.value = '';
                                                    }}
                                                />
                                            </div>
                                            <div className="md:col-span-3">
                                                <FormInput
                                                    label="Calendar ID"
                                                    value={(val as any)?.calendar_id || ''}
                                                    onChange={(e) => {
                                                        const cals = { ...(config.google_calendar?.calendars || {}) };
                                                        cals[key] = { ...(cals[key] || {}), calendar_id: e.target.value };
                                                        onChange({ ...config, google_calendar: { ...(config.google_calendar || {}), calendars: cals } });
                                                    }}
                                                    onBlur={() => {
                                                        // Auto-verify on blur once both fields needed for verification
                                                        // are populated. If verify succeeds, runCalendarVerify
                                                        // auto-fills Timezone (when empty) and the backend
                                                        // auto-subscribes the calendar so future discovery sees it.
                                                        // Skip if we already have a successful result for this exact
                                                        // (path, calendar_id, timezone, subject) tuple — avoids
                                                        // duplicate API calls when the operator tabs through fields
                                                        // without changing values, but DOES re-fire if any verified
                                                        // field has changed since the last successful verify.
                                                        // Codex feedback #3.
                                                        const cal = config?.google_calendar?.calendars?.[key] || {};
                                                        const calId = ((cal as any)?.calendar_id || '').trim();
                                                        const path = ((cal as any)?.credentials_path || '').trim();
                                                        if (!calId || !path) return;
                                                        const existing = verifyByKey[key];
                                                        const currentFingerprint = _verifyFingerprint(cal);
                                                        if (existing && existing.ok && !existing.loading && existing.verifiedFor === currentFingerprint) return;
                                                        runCalendarVerify(key);
                                                    }}
                                                    placeholder="you@yourdomain.com or c_xxx@group.calendar.google.com"
                                                    tooltip="Google Calendar ID. Use your email for primary calendars (NOT 'primary' — that only works for service-account-impersonated calls). For secondary calendars: 'c_xxx@group.calendar.google.com' from Calendar Settings → Integrate calendar. Auto-verifies when you tab out of this field."
                                                />
                                            </div>
                                            <div className="md:col-span-2">
                                                <FormInput
                                                    label="Timezone"
                                                    value={(val as any)?.timezone || ''}
                                                    onChange={(e) => {
                                                        const cals = { ...(config.google_calendar?.calendars || {}) };
                                                        cals[key] = { ...(cals[key] || {}), timezone: e.target.value };
                                                        onChange({ ...config, google_calendar: { ...(config.google_calendar || {}), calendars: cals } });
                                                    }}
                                                    placeholder="America/New_York"
                                                    tooltip="IANA timezone for this calendar (e.g. America/New_York)."
                                                />
                                            </div>
                                            <div className="md:col-span-1 flex justify-end">
                                                <button
                                                    type="button"
                                                    className="px-2 py-1 text-xs border rounded hover:bg-accent text-destructive hover:text-destructive"
                                                    onClick={() => {
                                                        const cals = { ...(config.google_calendar?.calendars || {}) };
                                                        delete cals[key];
                                                        // Clear the removed row's draft entry. The add flow reuses freed
                                                        // keys (e.g. calendar_1), and a leftover draft here would cause
                                                        // the next row created with the same key to render stale text
                                                        // instead of its real key.
                                                        setCalKeyDraftByKey((prev) => {
                                                            const next = { ...prev };
                                                            delete next[key];
                                                            return next;
                                                        });
                                                        migrateCalendarKeyInContexts(key, null);
                                                        onChange({ ...config, google_calendar: { ...(config.google_calendar || {}), calendars: cals } });
                                                    }}
                                                >
                                                    Remove
                                                </button>
                                            </div>

                                            {/* Identity + Verify sub-row.
                                                Identity is auto-loaded from the configured credentials_path
                                                so the operator sees the SA email (to share their calendar with)
                                                and client_id (for DWD setup) without grepping the JSON.
                                                Verify is one-click — POSTs current form state, returns
                                                structured success or actionable error code. */}
                                            <div className="md:col-span-12 mt-2 pt-2 border-t border-border/50 space-y-2">
                                                {/* Identity row */}
                                                {(() => {
                                                    const ident = identityByKey[key];
                                                    if (!ident) {
                                                        return (
                                                            <div className="text-xs text-muted-foreground italic">
                                                                Set the Credentials Path above to load the service account identity.
                                                            </div>
                                                        );
                                                    }
                                                    if (ident.loading) {
                                                        return (
                                                            <div className="text-xs text-muted-foreground flex items-center gap-2">
                                                                <Loader2 className="h-3 w-3 animate-spin" /> Loading identity from {ident.path}…
                                                            </div>
                                                        );
                                                    }
                                                    if (ident.error) {
                                                        return (
                                                            <div className="text-xs text-destructive">
                                                                <span className="font-medium">Could not read credentials</span>
                                                                {ident.errorCode ? <span className="ml-1">({ident.errorCode})</span> : null}
                                                                <span className="ml-1">— {ident.error}</span>
                                                            </div>
                                                        );
                                                    }
                                                    return (
                                                        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs items-center">
                                                            {ident.client_email && (
                                                                <div className="flex items-center gap-1">
                                                                    <span className="text-muted-foreground">📧 Service Account:</span>
                                                                    <code className="px-1 bg-muted rounded">{ident.client_email}</code>
                                                                    <button
                                                                        type="button"
                                                                        onClick={() => copyToClipboard(ident.client_email!)}
                                                                        className="text-muted-foreground hover:text-foreground"
                                                                        title="Copy — share your calendar with this email"
                                                                    >📋</button>
                                                                </div>
                                                            )}
                                                            {ident.client_id && (
                                                                <div className="flex items-center gap-1">
                                                                    <span className="text-muted-foreground">🔑 Client ID:</span>
                                                                    <code className="px-1 bg-muted rounded">{ident.client_id}</code>
                                                                    <button
                                                                        type="button"
                                                                        onClick={() => copyToClipboard(ident.client_id!)}
                                                                        className="text-muted-foreground hover:text-foreground"
                                                                        title="Copy — used for Domain-Wide Delegation at admin.google.com (NOT the email)"
                                                                    >📋</button>
                                                                </div>
                                                            )}
                                                        </div>
                                                    );
                                                })()}

                                                {/* Discovered-calendars picker — only renders when the upload's
                                                    discovery returned multiple accessible calendars (or zero, with a
                                                    helpful share-with-this-email hint). For the 1-calendar case the
                                                    upload handler already auto-filled the row's calendar_id/tz, so
                                                    nothing extra is shown here. */}
                                                {(() => {
                                                    const upload = uploadByKey[key];
                                                    if (!upload?.discovery) return null;
                                                    const disc = upload.discovery;
                                                    if (!disc.ok) {
                                                        return (
                                                            <div className="text-xs text-yellow-600 dark:text-yellow-400">
                                                                ⚠ Could not discover accessible calendars
                                                                {disc.error_code ? <span className="ml-1">({disc.error_code})</span> : null}
                                                                {disc.error_message ? <span className="ml-1">— {disc.error_message}</span> : null}
                                                            </div>
                                                        );
                                                    }
                                                    const cals = disc.calendars || [];
                                                    if (cals.length === 0) {
                                                        // Honest messaging: calendarList.list() only returns calendars
                                                        // the SA has SUBSCRIBED to. A calendar shared with the SA via
                                                        // "Share with specific people" does NOT auto-subscribe — the
                                                        // SA has to call calendarList.insert() first. So an empty
                                                        // discovery doesn't mean the SA has no access; it just means
                                                        // we haven't subscribed yet. Direct `calendars.get(id)` works
                                                        // regardless, which is what the Verify button uses.
                                                        const ident = identityByKey[key];
                                                        const email = ident?.client_email;
                                                        return (
                                                            <div className="text-xs text-yellow-600 dark:text-yellow-400 space-y-1">
                                                                <div>⚠ No subscribed calendars on this service account yet.</div>
                                                                {email && (
                                                                    <>
                                                                        <div>
                                                                            <strong>If you've already shared a calendar</strong> with <code className="px-1 bg-muted rounded">{email}</code>:
                                                                            paste the Calendar ID above (your email for primary calendars, or <code className="px-1 bg-muted rounded">c_xxx@group.calendar.google.com</code> for secondary), then click <strong>Verify access</strong>.
                                                                        </div>
                                                                        <div>
                                                                            <strong>If you haven't shared yet</strong>: open Google Calendar → Settings & sharing → Share with specific people → add <code className="px-1 bg-muted rounded">{email}</code> (Make changes to events), then click <strong>Replace JSON</strong> to re-discover.
                                                                        </div>
                                                                    </>
                                                                )}
                                                            </div>
                                                        );
                                                    }
                                                    if (cals.length === 1) {
                                                        // Auto-filled by upload handler — quiet success indicator
                                                        return (
                                                            <div className="text-xs text-green-600 dark:text-green-400">
                                                                ✓ Auto-filled with the only accessible calendar: {cals[0].summary || cals[0].id}
                                                            </div>
                                                        );
                                                    }
                                                    // Multiple calendars: dropdown picker. Operator chooses one.
                                                    return (
                                                        <div className="flex flex-wrap items-center gap-2 text-xs">
                                                            <span className="text-muted-foreground">{cals.length} calendars accessible — pick one:</span>
                                                            <select
                                                                className="px-2 py-1 rounded border bg-background"
                                                                onChange={(e) => {
                                                                    const idx = parseInt(e.target.value, 10);
                                                                    if (Number.isFinite(idx) && cals[idx]) {
                                                                        pickDiscoveredCalendar(key, cals[idx]);
                                                                    }
                                                                }}
                                                                defaultValue=""
                                                            >
                                                                <option value="" disabled>Choose a calendar…</option>
                                                                {cals.map((c, i) => (
                                                                    <option key={c.id || i} value={i}>
                                                                        {c.summary || c.id} ({c.access_role}) — {c.timezone}
                                                                    </option>
                                                                ))}
                                                            </select>
                                                        </div>
                                                    );
                                                })()}

                                                {/* Action row: Upload, Verify, plus inline result/status text.
                                                    Grouped so all SA-related actions live in one place beneath
                                                    the row, leaving the form-field row above cleanly aligned. */}
                                                <div className="flex items-start gap-3 flex-wrap">
                                                    <button
                                                        type="button"
                                                        className="px-3 py-1 text-xs rounded border hover:bg-accent flex items-center gap-1"
                                                        disabled={uploadByKey[key]?.uploading}
                                                        onClick={() => calendarFileInputRefs.current[key]?.click()}
                                                        title="Upload an SA JSON. Auto-discovers and fills Calendar ID + Timezone if exactly one calendar is shared with the SA."
                                                    >
                                                        {uploadByKey[key]?.uploading
                                                            ? <Loader2 className="h-3 w-3 animate-spin" />
                                                            : <span>📁</span>}
                                                        {(val as any)?.credentials_path ? 'Replace JSON' : 'Upload JSON'}
                                                    </button>
                                                    {uploadByKey[key]?.error && (
                                                        <span className="text-xs text-destructive self-center">✗ {uploadByKey[key]?.error}</span>
                                                    )}
                                                    {(() => {
                                                        // Disable Verify when there's nothing meaningful to test
                                                        // against (no path or no calendar_id) — clicking would just
                                                        // surface the same missing_credentials_path / missing_calendar_id
                                                        // error from the backend. Make the disabled reason visible
                                                        // in the tooltip so the operator knows what to fix.
                                                        const hasPath = !!(val as any)?.credentials_path?.trim?.();
                                                        const hasCalId = !!(val as any)?.calendar_id?.trim?.();
                                                        const verifyDisabled = !!verifyByKey[key]?.loading || !hasPath || !hasCalId;
                                                        const verifyTitle = verifyByKey[key]?.loading
                                                            ? 'Verifying…'
                                                            : !hasPath
                                                                ? 'Upload a JSON or fill in Credentials Path first.'
                                                                : !hasCalId
                                                                    ? 'Fill in Calendar ID first (your email for primary, or c_xxx@group.calendar.google.com for secondary).'
                                                                    : 'Test that the credentials can read the configured calendar.';
                                                        return (
                                                            <button
                                                                type="button"
                                                                className="px-3 py-1 text-xs rounded border hover:bg-accent flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent"
                                                                disabled={verifyDisabled}
                                                                onClick={() => runCalendarVerify(key)}
                                                                title={verifyTitle}
                                                            >
                                                                {verifyByKey[key]?.loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <span>🩺</span>}
                                                                Verify access
                                                            </button>
                                                        );
                                                    })()}
                                                    {(() => {
                                                        const v = verifyByKey[key];
                                                        if (!v || v.loading) return null;
                                                        // Only show the green check if the fingerprint matches the
                                                        // current row state — otherwise the operator edited a
                                                        // verified field (path / calendar_id / tz / subject) after
                                                        // the last verify and the green check is stale.
                                                        // Codex feedback #3.
                                                        const cal = config?.google_calendar?.calendars?.[key] || {};
                                                        const currentFp = _verifyFingerprint(cal);
                                                        if (v.ok && v.verifiedFor && v.verifiedFor !== currentFp) return null;
                                                        if (v.ok) {
                                                            return (
                                                                <div className="text-xs flex flex-col">
                                                                    <span className="text-green-600 dark:text-green-400">
                                                                        ✓ Reachable: {v.calendar_summary || '(no summary)'} ({v.calendar_actual_timezone || 'no tz'})
                                                                    </span>
                                                                    {v.drift_warning && (
                                                                        <span className="text-yellow-600 dark:text-yellow-400 mt-0.5">
                                                                            ⚠ {v.drift_warning}
                                                                        </span>
                                                                    )}
                                                                </div>
                                                            );
                                                        }
                                                        return (
                                                            <div className="text-xs text-destructive">
                                                                <span className="font-medium">✗ {v.errorCode || 'failed'}</span>
                                                                <span className="ml-1">— {v.errorMessage || 'unknown error'}</span>
                                                            </div>
                                                        );
                                                    })()}
                                                </div>

                                                {/* Domain-Wide Delegation (advanced).
                                                    Optional per-row impersonation: when set, the SA acts AS this
                                                    user via creds.with_subject(). Required when org policy blocks
                                                    external sharing of calendars (the "Some sharing options have
                                                    been turned off by your administrator" wall).

                                                    Default-open if subject is already configured so operators
                                                    don't have to hunt for it on edit. Default-closed for fresh
                                                    rows since 90% of setups don't need DWD. */}
                                                <details className="text-xs" open={!!(val as any)?.subject?.trim?.()}>
                                                    <summary className="cursor-pointer text-muted-foreground hover:text-foreground select-none inline-flex items-center gap-1">
                                                        <span>🪪</span>
                                                        Domain-Wide Delegation (advanced)
                                                    </summary>
                                                    <div className="mt-2 space-y-2 pl-4 border-l-2 border-border/40">
                                                        <div className="flex items-center gap-2">
                                                            <label className="text-muted-foreground whitespace-nowrap min-w-[8rem]">Impersonate as:</label>
                                                            <input
                                                                type="email"
                                                                className="flex-1 px-2 py-1 rounded border bg-background"
                                                                placeholder="user@yourdomain.com"
                                                                value={(val as any)?.subject || ''}
                                                                onChange={(e) => {
                                                                    const cals = { ...(config.google_calendar?.calendars || {}) };
                                                                    cals[key] = { ...(cals[key] || {}), subject: e.target.value };
                                                                    onChange({ ...config, google_calendar: { ...(config.google_calendar || {}), calendars: cals } });
                                                                }}
                                                                onBlur={() => {
                                                                    // Re-fire verify on blur if all three are set
                                                                    // so DWD setup mistakes surface immediately
                                                                    // (dwd_not_configured error code) rather than
                                                                    // at first call time. Same dedupe as Calendar ID.
                                                                    const cal = config?.google_calendar?.calendars?.[key] || {};
                                                                    const calId = ((cal as any)?.calendar_id || '').trim();
                                                                    const path = ((cal as any)?.credentials_path || '').trim();
                                                                    const subj = ((cal as any)?.subject || '').trim();
                                                                    if (!calId || !path) return;
                                                                    // Reset previous result so the verify status
                                                                    // reflects the new subject value
                                                                    setVerifyByKey((prev) => ({ ...prev, [key]: undefined as any }));
                                                                    if (subj || verifyByKey[key]?.ok) runCalendarVerify(key);
                                                                }}
                                                            />
                                                        </div>
                                                        <div className="text-muted-foreground space-y-1">
                                                            <div>
                                                                Optional. When set, the service account impersonates this user.
                                                                Use when your Workspace policy blocks external sharing of calendars
                                                                (the "Some sharing options have been turned off by your administrator" wall).
                                                            </div>
                                                            {(() => {
                                                                const ident = identityByKey[key];
                                                                const cid = ident?.client_id;
                                                                return (
                                                                    <div>
                                                                        <strong>Setup:</strong> at <a href="https://admin.google.com/ac/owl/domainwidedelegation" target="_blank" rel="noreferrer" className="underline hover:text-foreground">admin.google.com → Security → API controls → Domain-wide delegation</a>,
                                                                        add a new client with <strong>Client ID</strong> <code className="px-1 bg-muted rounded">{cid || '(load identity above first)'}</code>
                                                                        {' '}(NOT the email!) and scope <code className="px-1 bg-muted rounded">https://www.googleapis.com/auth/calendar</code>.
                                                                    </div>
                                                                );
                                                            })()}
                                                        </div>
                                                    </div>
                                                </details>
                                            </div>
                                        </div>
                                    ))}
                                    {Object.keys(config.google_calendar?.calendars || {}).length === 0 && (
                                        <div className="text-xs text-muted-foreground italic border border-dashed border-border rounded p-3 space-y-1">
                                            <div className="font-medium text-foreground not-italic">No calendars configured.</div>
                                            <div>
                                                Click <strong>+ Add Calendar</strong> below to set up Google Calendar access.
                                                You'll need a Google service account JSON key file and a calendar shared with that
                                                service account (with <strong>"Make changes to events"</strong> permission).
                                            </div>
                                            <div>
                                                For <code className="px-1 bg-muted rounded">get_free_slots</code> to return availability,
                                                your calendar needs events titled with the free prefix (default: "Open") to define
                                                when bookings are allowed.
                                            </div>
                                            <div className="opacity-70">
                                                Legacy fallback: <code className="px-1 bg-muted rounded">GOOGLE_CALENDAR_*</code> env vars
                                                still work for one calendar (deprecated, will be removed in a future release).
                                            </div>
                                        </div>
                                    )}
                                    <button
                                        type="button"
                                        className="px-3 py-1.5 text-xs rounded border hover:bg-accent"
                                        onClick={() => {
                                            const cals = { ...(config.google_calendar?.calendars || {}) };
                                            let base = 'calendar'; let i = 1; let k = `${base}_${i}`;
                                            while (Object.prototype.hasOwnProperty.call(cals, k)) { i += 1; k = `${base}_${i}`; }
                                            cals[k] = { credentials_path: '', calendar_id: '', timezone: '' };
                                            onChange({ ...config, google_calendar: { ...(config.google_calendar || {}), calendars: cals } });
                                        }}
                                    >
                                        + Add Calendar
                                    </button>
                                </div>
                            </div>
                        </div>
                    )}
                </div>

                {/* Microsoft Calendar */}
                <div className="border border-border rounded-lg p-4 bg-card/50">
                    <FormSwitch
                        label="Microsoft Calendar"
                        description="Enable the Microsoft 365 Outlook Calendar tool using device-code OAuth."
                        checked={config.microsoft_calendar?.enabled ?? false}
                        onChange={(e) => updateNestedConfig('microsoft_calendar', 'enabled', e.target.checked)}
                        className="mb-0 border-0 p-0 bg-transparent"
                    />
                    {config.microsoft_calendar?.enabled && (
                        <div className="mt-4 pl-4 border-l-2 border-border ml-2 space-y-4">
                            <div>
                                <div className="text-sm font-medium mb-1">Availability defaults</div>
                                <div className="text-xs text-muted-foreground mb-3">
                                    Blank Free prefix uses Microsoft Graph free/busy plus working hours (Mon-Fri 09:00-17:00 by default).
                                    Set Free prefix only if you want title-prefix "Open" events instead.
                                </div>
                                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
                                    <FormInput
                                        label="Free prefix"
                                        value={config.microsoft_calendar?.free_prefix ?? ''}
                                        onChange={(e) => updateNestedConfig('microsoft_calendar', 'free_prefix', e.target.value)}
                                        placeholder="(blank = use Microsoft free/busy)"
                                        tooltip="Leave blank for native Microsoft free/busy mode. Set a value only for title-prefix availability windows."
                                    />
                                    <FormInput
                                        label="Busy prefix"
                                        value={config.microsoft_calendar?.busy_prefix ?? ''}
                                        onChange={(e) => updateNestedConfig('microsoft_calendar', 'busy_prefix', e.target.value)}
                                        placeholder="Busy"
                                        tooltip="Only used when Free prefix is set."
                                    />
                                    <FormInput
                                        label="Default slot duration (minutes)"
                                        type="number"
                                        value={(config.microsoft_calendar?.min_slot_duration_minutes ?? '').toString()}
                                        onChange={(e) => {
                                            const raw = e.target.value;
                                            const parsed = raw === '' ? undefined : parseInt(raw, 10);
                                            updateNestedConfig('microsoft_calendar', 'min_slot_duration_minutes', Number.isFinite(parsed) ? parsed : undefined);
                                        }}
                                        placeholder="30"
                                        tooltip="Slot length in minutes if the LLM doesn't pass duration. Default: 30."
                                    />
                                    <FormInput
                                        label="Max slots returned"
                                        type="number"
                                        value={(config.microsoft_calendar?.max_slots_returned ?? '').toString()}
                                        onChange={(e) => {
                                            const raw = e.target.value;
                                            const parsed = raw === '' ? undefined : parseInt(raw, 10);
                                            updateNestedConfig('microsoft_calendar', 'max_slots_returned', Number.isFinite(parsed) ? parsed : undefined);
                                        }}
                                        placeholder="3"
                                        tooltip="Cap on how many slot start-times get_free_slots returns to the AI. Default: 3."
                                    />
                                </div>
                            </div>

                            <div>
                                <div className="text-sm font-medium mb-1">Microsoft 365 account</div>
                                <div className="text-xs text-muted-foreground mb-3">
                                    V1 links one work or school Microsoft 365 account. In Azure App registrations, enable public client flows and use an explicit tenant ID.
                                </div>
                                <div className="grid grid-cols-1 md:grid-cols-12 gap-2 items-end border border-border rounded p-2 bg-card/30">
                                    <div className="md:col-span-3">
                                        <FormInput
                                            label="Tenant ID"
                                            value={microsoftAccount.tenant_id || ''}
                                            onChange={(e) => updateMicrosoftAccount({ tenant_id: e.target.value })}
                                            placeholder="contoso.onmicrosoft.com or tenant GUID"
                                            tooltip="Explicit Entra tenant ID or domain. Microsoft Calendar V1 does not use /common."
                                        />
                                    </div>
                                    <div className="md:col-span-3">
                                        <FormInput
                                            label="Client ID"
                                            value={microsoftAccount.client_id || ''}
                                            onChange={(e) => updateMicrosoftAccount({ client_id: e.target.value })}
                                            placeholder="Application (client) ID"
                                            tooltip="Azure App registrations → Overview → Application (client) ID. Public client flows must be enabled."
                                        />
                                    </div>
                                    <div className="md:col-span-3">
                                        <FormInput
                                            label="Signed-in user"
                                            value={microsoftAccount.user_principal_name || ''}
                                            onChange={(e) => updateMicrosoftAccount({ user_principal_name: e.target.value })}
                                            placeholder="filled after Connect"
                                            tooltip="The Microsoft 365 user who authorized the device-code flow."
                                        />
                                    </div>
                                    <div className="md:col-span-3">
                                        <FormInput
                                            label="Timezone"
                                            value={microsoftAccount.timezone || ''}
                                            onChange={(e) => updateMicrosoftAccount({ timezone: e.target.value })}
                                            placeholder="America/New_York"
                                            tooltip="IANA timezone used for working hours and local slot display. Graph requests are normalized to UTC internally."
                                        />
                                    </div>
                                    <div className="md:col-span-5">
                                        <FormInput
                                            label="Token cache path"
                                            value={microsoftAccount.token_cache_path || ''}
                                            onChange={(e) => updateMicrosoftAccount({ token_cache_path: e.target.value })}
                                            placeholder="/app/project/secrets/microsoft-calendar-default-token-cache.json"
                                            tooltip="Filled after Connect. Stored under /app/project/secrets with 0640 permissions so admin_ui and ai_engine can refresh tokens safely."
                                        />
                                    </div>
                                    <div className="md:col-span-5">
                                        <FormInput
                                            label="Calendar ID"
                                            value={microsoftAccount.calendar_id || ''}
                                            onChange={(e) => updateMicrosoftAccount({ calendar_id: e.target.value })}
                                            placeholder="filled after Connect or choose below"
                                            tooltip="Microsoft Graph calendar id. Events are created with /me/calendars/{calendar_id}/events so this selection is honored."
                                        />
                                    </div>
                                    <div className="md:col-span-2 flex gap-2 justify-end">
                                        <button
                                            type="button"
                                            className="px-3 py-1 text-xs rounded border hover:bg-accent disabled:opacity-50"
                                            disabled={msDevice.status === 'pending'}
                                            onClick={startMicrosoftDeviceFlow}
                                            title="Start device-code OAuth. The operator signs in at microsoft.com/devicelogin."
                                        >
                                            {msDevice.status === 'pending' ? <Loader2 className="h-3 w-3 animate-spin" /> : 'Connect'}
                                        </button>
                                        <button
                                            type="button"
                                            className="px-3 py-1 text-xs rounded border hover:bg-accent disabled:opacity-50"
                                            disabled={msVerify.loading || !microsoftAccount.token_cache_path || !microsoftAccount.calendar_id}
                                            onClick={runMicrosoftVerify}
                                            title="Verify the connected account can see the configured calendar."
                                        >
                                            {msVerify.loading ? <Loader2 className="h-3 w-3 animate-spin" /> : 'Verify'}
                                        </button>
                                    </div>

                                    <div className="md:col-span-12 pt-2 border-t border-border/50 space-y-2">
                                        {msDevice.status === 'pending' && (
                                            <div className="text-xs rounded border border-blue-500/30 bg-blue-500/10 p-2">
                                                <div className="font-medium">Authorize Microsoft Calendar</div>
                                                <div>Go to <a className="underline" href={msDevice.verificationUri || 'https://microsoft.com/devicelogin'} target="_blank" rel="noreferrer">{msDevice.verificationUri || 'https://microsoft.com/devicelogin'}</a> and enter code <code className="px-1 bg-muted rounded">{msDevice.userCode}</code>.</div>
                                            </div>
                                        )}
                                        {msDevice.status === 'error' && (
                                            <div className="text-xs text-destructive">Microsoft connect failed: {msDevice.error}</div>
                                        )}
                                        {msDevice.status === 'expired' && (
                                            <div className="text-xs text-yellow-600 dark:text-yellow-400">Device code expired. Click Connect to start again.</div>
                                        )}
                                        {msDevice.status === 'success' && (
                                            <div className="text-xs text-green-600 dark:text-green-400">Connected as {microsoftAccount.user_principal_name || 'Microsoft user'}.</div>
                                        )}
                                        {msCalendars.length > 0 && (
                                            <div className="flex items-center gap-2 text-xs flex-wrap">
                                                <span className="text-muted-foreground">Choose calendar:</span>
                                                <select
                                                    className="px-2 py-1 rounded border bg-background"
                                                    value={microsoftAccount.calendar_id || ''}
                                                    onChange={(e) => updateMicrosoftAccount({ calendar_id: e.target.value })}
                                                >
                                                    {msCalendars.map((cal) => (
                                                        <option key={cal.id} value={cal.id}>{cal.name || cal.id}{cal.is_default_calendar ? ' (default)' : ''}</option>
                                                    ))}
                                                </select>
                                            </div>
                                        )}
                                        {msVerify.ok && (
                                            <div className="text-xs text-green-600 dark:text-green-400">{msVerify.message}</div>
                                        )}
                                        {msVerify.ok === false && (
                                            <div className="text-xs text-destructive">
                                                <span className="font-medium">{msVerify.errorCode || 'verify_failed'}</span>
                                                <span className="ml-1">{msVerify.message}</span>
                                            </div>
                                        )}
                                        {microsoftAccount.token_cache_path && (
                                            <button
                                                type="button"
                                                className="px-3 py-1 text-xs rounded border hover:bg-accent text-destructive"
                                                onClick={disconnectMicrosoftCalendar}
                                            >
                                                Disconnect
                                            </button>
                                        )}
                                    </div>
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            </div>

            {/* Destination Edit Modal */}
            <Modal
                isOpen={!!editingDestination}
                onClose={() => setEditingDestination(null)}
                title={editingDestination === 'new_destination' ? 'Add Destination' : 'Edit Destination'}
                footer={
                    <>
                        <button onClick={() => setEditingDestination(null)} className="px-4 py-2 border rounded hover:bg-accent">Cancel</button>
                        <button onClick={handleSaveDestination} className="px-4 py-2 bg-primary text-primary-foreground rounded hover:bg-primary/90">Save</button>
                    </>
                }
            >
                <div className="space-y-4">
                    <FormInput
                        label="Key (Name)"
                        value={destinationForm.key || ''}
                        onChange={(e) => setDestinationForm({ ...destinationForm, key: e.target.value })}
                        placeholder="e.g., frontdesk_primary"
                        disabled={editingDestination !== 'new_destination'}
                    />
                    <FormSelect
                        label="Type"
                        options={[
                            { value: 'extension', label: 'Extension' },
                            { value: 'queue', label: 'Queue' },
                            { value: 'ringgroup', label: 'Ring Group' },
                        ]}
                        value={destinationForm.type || 'extension'}
                        onChange={(e) => setDestinationForm({ ...destinationForm, type: e.target.value })}
                    />
                    {destinationForm.type === 'extension' && (
                        <FormSwitch
                            label="Allow Attended Transfer"
                            description="Enable warm transfer for this destination (agent announcement + DTMF accept/decline)."
                            checked={destinationForm.attended_allowed ?? false}
                            onChange={(e) => setDestinationForm({ ...destinationForm, attended_allowed: e.target.checked })}
                        />
                    )}
	                    {destinationForm.type === 'extension' && (
	                        <FormSwitch
	                            label="Use As Live Agent Destination"
	                            description={
	                                showLiveAgentRoutingAdvanced
	                                    ? "Marks this destination as the live-agent target fallback when no explicit live_agent_destination_key is set."
	                                    : "Disabled. Enable 'Advanced: Route Live Agent via Destination' to use destination-based live-agent routing."
	                            }
	                            checked={destinationForm.live_agent ?? false}
	                            onChange={(e) => setDestinationForm({ ...destinationForm, live_agent: e.target.checked })}
	                            disabled={!showLiveAgentRoutingAdvanced}
	                        />
	                    )}
                    <FormInput
                        label="Target Number"
                        value={destinationForm.target || ''}
                        onChange={(e) => setDestinationForm({ ...destinationForm, target: e.target.value })}
                        placeholder="e.g., 6000"
                    />
                    <FormInput
                        label="Description"
                        value={destinationForm.description || ''}
                        onChange={(e) => setDestinationForm({ ...destinationForm, description: e.target.value })}
                        placeholder="e.g., Sales Support"
                    />
                </div>
            </Modal>

            <EmailTemplateModal
                isOpen={templateModalOpen}
                onClose={() => setTemplateModalOpen(false)}
                tool={templateModalTool}
                currentTemplate={(config?.[templateModalTool]?.html_template || '').trim() ? (config?.[templateModalTool]?.html_template || '') : null}
                includeTranscript={templateModalTool === 'send_email_summary' ? (config?.send_email_summary?.include_transcript ?? true) : true}
                defaultTemplate={getDefaultEmailTemplate(templateModalTool)}
                variableNames={(emailDefaults?.variables || []).map((v: any) => v?.name).filter(Boolean)}
                defaultsStatusText={
                    emailDefaultsError
                        ? `Defaults error: ${emailDefaultsError}`
                        : (emailDefaults ? 'Defaults loaded' : 'Defaults loading…')
                }
                onReloadDefaults={async () => {
                    const ok = await loadEmailDefaults();
                    if (ok) toast.success('Loaded default templates');
                    else toast.error('Failed to load defaults');
                }}
                onSave={async (nextTemplate) => {
                    const prevConfig = config;
                    const nextConfig = (() => {
                        if (!nextTemplate) {
                            const next = { ...config };
                            const current = next[templateModalTool];
                            if (!current || typeof current !== 'object') return next;
                            const copy = { ...current };
                            delete copy.html_template;
                            next[templateModalTool] = copy;
                            return next;
                        }
                        return {
                            ...config,
                            [templateModalTool]: {
                                ...config[templateModalTool],
                                html_template: nextTemplate
                            }
                        };
                    })();

                    onChange(nextConfig);
                    if (onSaveNow) {
                        try {
                            await onSaveNow(nextConfig);
                        } catch (e) {
                            // Revert local state so UI reflects the persisted config.
                            onChange(prevConfig);
                            throw e;
                        }
                    }
                }}
            />
        </div>
    );
};

export default ToolForm;
