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
    onSaveNow?: (newConfig: any) => Promise<void>;
}

const DEFAULT_ATTENDED_ANNOUNCEMENT_TEMPLATE =
    "Hi, this is Ava. I'm transferring {caller_display} regarding {context_name}.";
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
    const aliases = Array.isArray(ext?.aliases)
        ? ext.aliases.map((item: any) => String(item || '').trim()).filter(Boolean)
        : [];
    return actionType !== 'transfer' || Boolean(ext?.pass_caller_info) || aliases.length > 0;
};

const ToolForm = ({ config, contexts, hangupUsage, onChange, onSaveNow }: ToolFormProps) => {
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

        const checkLiveAgentStatus = async (rowId: string, key: string, ext: any) => {
            const dialString = String(ext?.dial_string || '');
            const tech = String(ext?.device_state_tech || 'auto');
            const numericKey = isNumericKey(key) ? String(key).trim() : extractNumericExtensionKeyFromDialString(dialString);
            if (!numericKey) {
                toast.error('Set a numeric extension or dial string (e.g. PJSIP/2765) before checking status.');
                return;
            }

            setInternalExtStatusByRowId((prev) => ({
                ...prev,
                [rowId]: { ...(prev[rowId] || {}), loading: true, error: '' },
            }));

            try {
                const res = await axios.get('/api/system/ari/extension-status', {
                    params: { key: numericKey, device_state_tech: tech, dial_string: dialString },
                });
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
                if (!data.success && data.error) {
                    toast.error(String(data.error));
                }
            } catch (e: any) {
                const err = e?.response?.data?.detail || e?.message || 'Status check failed.';
                setInternalExtStatusByRowId((prev) => ({
                    ...prev,
                    [rowId]: { ...(prev[rowId] || {}), loading: false, success: false, status: 'unknown', error: String(err) },
                }));
                toast.error(String(err));
            }
        };

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

    const renameInternalExtensionKey = (fromKey: string, toKeyRaw: string) => {
        const toKey = (toKeyRaw || '').trim();
        if (!toKey) {
            toast.error('Extension key cannot be empty.');
            return;
        }
        if (toKey === fromKey) return;

        const existing = { ...(config.extensions?.internal || {}) };
        if (Object.prototype.hasOwnProperty.call(existing, toKey)) {
            toast.error(`An extension with key '${toKey}' already exists.`);
            return;
        }

        const renamed: Record<string, any> = {};
        Object.entries(existing).forEach(([k, v]) => {
            if (k === fromKey) renamed[toKey] = v;
            else renamed[k] = v;
        });
        moveInternalExtRowId(fromKey, toKey);
        updateNestedConfig('extensions', 'internal', renamed);
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
	                                {Object.entries(config.transfer?.destinations || {}).map(([key, dest]: [string, any]) => (
	                                    <div key={key} className="flex items-center justify-between p-3 bg-accent/30 rounded border border-border/50">
	                                        <div>
	                                            <div className="font-medium text-sm">{key}</div>
	                                            <div className="text-xs text-muted-foreground">
	                                                {dest.type} • {dest.target} • {dest.description}
	                                                {dest.type === 'extension' && dest.attended_allowed ? ' • attended' : ''}
	                                                {dest.type === 'extension' && showLiveAgentRoutingAdvanced && dest.live_agent ? ' • live-agent' : ''}
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
                                ))}
                            </div>
                        </div>
                    )}
                </div>

                {/* Attended (Warm) Transfer */}
                <div className="border border-border rounded-lg p-4 bg-card/50">
                    <FormSwitch
                        label="Attended Transfer (Warm)"
                        description="Warm transfer with MOH, one-way announcement to the agent, and DTMF accept/decline. Requires Local AI Server for TTS."
                        checked={config.attended_transfer?.enabled ?? false}
                        onChange={(e) => handleAttendedTransferToggle(e.target.checked)}
                        className="mb-0 border-0 p-0 bg-transparent"
                    />
                    {config.attended_transfer?.enabled && (
                        <div className="mt-4 pl-4 border-l-2 border-border ml-2 space-y-4">
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
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
                            </div>

                            <div className="space-y-2">
                                <FormLabel tooltip="Spoken to the destination agent (one-way) before requesting DTMF acceptance. Placeholders: {caller_display}, {caller_name}, {caller_number}, {context_name}, {destination_description}.">
                                    Agent Announcement Template
                                </FormLabel>
                                <textarea
                                    className="w-full p-3 rounded-md border border-input bg-transparent text-sm min-h-[100px] focus:outline-none focus:ring-1 focus:ring-ring"
                                    value={config.attended_transfer?.announcement_template ?? DEFAULT_ATTENDED_ANNOUNCEMENT_TEMPLATE}
                                    onChange={(e) => updateNestedConfig('attended_transfer', 'announcement_template', e.target.value)}
                                    placeholder="Hi, this is Ava. I'm transferring {caller_display} regarding {context_name}."
                                />
                            </div>

                            <div className="space-y-2">
                                <FormLabel tooltip="Spoken to the destination agent to request acceptance/decline (DTMF).">
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
                                <p className={`text-xs mt-2 ${showHangupExpert ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'}`}>
                                    {showHangupExpert
                                        ? 'Warning: these values directly influence hangup intent matching and fallback behavior.'
                                        : 'Expert values are shown with defaults and are read-only until enabled.'}
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
                                        disabled={!showHangupExpert}
                                    />
                                </div>
                                <div className="mt-1 grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <div className="space-y-2">
                                        <FormLabel tooltip="Caller-side phrases that indicate they want to end the call. If a transcript contains one of these markers, the hangup guardrail is more likely to allow call termination.">
                                            End Call Markers
                                        </FormLabel>
                                        <textarea
                                            className="w-full p-2 rounded border border-input bg-background text-sm min-h-[120px] disabled:cursor-not-allowed disabled:opacity-50"
                                            value={endCallMarkerDraft}
                                            onChange={(e) => setEndCallMarkerDraft(e.target.value)}
                                            onBlur={() => updateHangupMarkers('end_call', parseMarkerList(endCallMarkerDraft))}
                                            disabled={!showHangupExpert}
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
                                            className="w-full p-2 rounded border border-input bg-background text-sm min-h-[120px] disabled:cursor-not-allowed disabled:opacity-50"
                                            value={assistantFarewellMarkerDraft}
                                            onChange={(e) => setAssistantFarewellMarkerDraft(e.target.value)}
                                            onBlur={() => updateHangupMarkers('assistant_farewell', parseMarkerList(assistantFarewellMarkerDraft))}
                                            disabled={!showHangupExpert}
                                        />
                                        <p className="text-xs text-muted-foreground">
                                            One phrase per line. Include common assistant closings (for example, "goodbye", "thank you for calling").
                                        </p>
                                    </div>
                                </div>
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
	                                updateNestedConfig('extensions', 'internal', { ...existing, [key]: { name: '', description: '', dial_string: '', transfer: true, device_state_tech: 'auto', action_type: 'transfer', aliases: [], pass_caller_info: false } });
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
	                            <div key={rowId} className="grid grid-cols-1 md:grid-cols-12 gap-2 p-3 border rounded bg-background/50 items-center">
	                                <div className="md:col-span-1">
                                        {(() => {
                                            const derived = extractNumericExtensionKeyFromDialString(ext?.dial_string || '');
                                            const displayKey = isNumericKey(key) ? key : derived;
                                            return (
	                                            <input
	                                                className="w-full border rounded px-2 py-1 text-sm bg-muted text-muted-foreground"
	                                                placeholder="Auto"
	                                                value={displayKey || ''}
	                                                disabled
	                                                title="Auto-derived from dial string (e.g. PJSIP/2765 -> 2765). Numeric keys are locked to prevent accidental renames."
	                                            />
                                            );
                                        })()}
	                                </div>
	                                <div className="md:col-span-2">
	                                    <input
	                                        className="w-full border rounded px-2 py-1 text-sm"
	                                        placeholder="Name"
                                        value={ext.name || ''}
                                        onChange={(e) => {
                                            const updated = { ...(config.extensions?.internal || {}) };
                                            updated[key] = { ...ext, name: e.target.value };
                                            updateNestedConfig('extensions', 'internal', updated);
                                        }}
                                        title="Agent Name"
                                    />
                                </div>
	                                <div className="md:col-span-2">
	                                    <input
	                                        className="w-full border rounded px-2 py-1 text-sm"
	                                        placeholder="Dial String"
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
                                <div className="md:col-span-2">
                                    <select
                                        className="w-full border rounded px-2 py-1 text-sm bg-background"
                                        value={ext.device_state_tech || 'auto'}
                                        onChange={(e) => {
                                            const updated = { ...(config.extensions?.internal || {}) };
                                            updated[key] = { ...ext, device_state_tech: e.target.value };
                                            updateNestedConfig('extensions', 'internal', updated);
                                        }}
                                        title="Device state technology for availability checks"
                                    >
                                        <option value="auto">Device Tech: auto</option>
                                        <option value="PJSIP">PJSIP</option>
                                        <option value="SIP">SIP</option>
                                        <option value="IAX2">IAX2</option>
                                        <option value="DAHDI">DAHDI</option>
                                    </select>
                                </div>
                                <div className="md:col-span-2">
                                    <input
                                        className="w-full border rounded px-2 py-1 text-sm"
                                        placeholder="Description"
                                        value={ext.description || ''}
                                        onChange={(e) => {
                                            const updated = { ...(config.extensions?.internal || {}) };
                                            updated[key] = { ...ext, description: e.target.value };
                                            updateNestedConfig('extensions', 'internal', updated);
                                        }}
	                                        title="Description"
	                                    />
	                                </div>
                                    <>
                                        <div className="md:col-span-2">
                                            <select
                                                className="w-full border rounded px-2 py-1 text-sm bg-background disabled:cursor-not-allowed disabled:opacity-50"
                                                value={ext.action_type || 'transfer'}
                                                onChange={(e) => {
                                                    const updated = { ...(config.extensions?.internal || {}) };
                                                    updated[key] = { ...ext, action_type: e.target.value };
                                                    updateNestedConfig('extensions', 'internal', updated);
                                                }}
                                                title="Action type used when transfer tool resolves this target"
                                                disabled={!showLiveAgentsExpert}
                                            >
                                                <option value="transfer">action_type: transfer</option>
                                                <option value="voicemail">action_type: voicemail</option>
                                                <option value="queue">action_type: queue</option>
                                                <option value="ringgroup">action_type: ringgroup</option>
                                            </select>
                                        </div>
	                                        <div className="md:col-span-2">
	                                            <input
	                                                className="w-full border rounded px-2 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-50"
	                                                placeholder="Aliases (comma-separated)"
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
                                        <div className="md:col-span-2">
                                            <FormSwitch
                                                label="Pass Caller Info"
                                                description="Include caller name/number and last transcript in transfer context."
                                                checked={ext.pass_caller_info ?? false}
                                                onChange={(e) => {
                                                    const updated = { ...(config.extensions?.internal || {}) };
                                                    updated[key] = { ...ext, pass_caller_info: e.target.checked };
                                                    updateNestedConfig('extensions', 'internal', updated);
                                                }}
                                                disabled={!showLiveAgentsExpert}
                                            />
                                        </div>
                                    </>
	                                <div className="md:col-span-3 flex justify-end items-center gap-3 min-w-0 overflow-hidden">
                                        <button
                                            type="button"
                                            className={`inline-flex items-center gap-2 px-2.5 py-1 rounded-full text-xs font-medium border ${pillClass} hover:bg-accent/40 transition-colors min-w-0 max-w-[150px] overflow-hidden`}
                                            title={title}
                                            onClick={() => checkLiveAgentStatus(rowId, key, ext)}
                                        >
                                            {loading ? (
                                                <Loader2 className="w-3 h-3 animate-spin shrink-0" />
                                            ) : (
                                                <span className={`w-2 h-2 rounded-full ${dotClass} shrink-0`} />
                                            )}
                                            <span className="truncate whitespace-nowrap">{label}</span>
                                        </button>
                                        <div className="shrink-0">
	                                        <FormSwitch
	                                            checked={ext.transfer ?? true}
	                                            onChange={(e) => {
	                                                const updated = { ...(config.extensions?.internal || {}) };
	                                                updated[key] = { ...ext, transfer: e.target.checked };
	                                                updateNestedConfig('extensions', 'internal', updated);
	                                            }}
	                                            className="mb-0 border-0 p-0 bg-transparent"
	                                            label=""
	                                            description=""
	                                        />
                                        </div>
                                        <div className="shrink-0">
	                                        <button
	                                            onClick={() => {
	                                                const updated = { ...(config.extensions?.internal || {}) };
	                                                delete updated[key];
                                                    deleteInternalExtRowId(key);
	                                                updateNestedConfig('extensions', 'internal', updated);
	                                            }}
	                                            className="p-2 text-destructive hover:bg-destructive/10 rounded"
	                                            title="Delete Extension"
	                                        >
	                                            <Trash2 className="w-4 h-4" />
	                                        </button>
                                        </div>
	                                </div>
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
