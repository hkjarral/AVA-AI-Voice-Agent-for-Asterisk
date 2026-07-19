import { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    AlertTriangle,
    CheckCircle2,
    Clipboard,
    Link2,
    Loader2,
    Pencil,
    Plus,
    RefreshCw,
    ServerCog,
    Trash2,
} from 'lucide-react';
import { toast } from 'sonner';
import { Modal } from '../ui/Modal';
import { FormInput, FormSelect, FormSwitch } from '../ui/FormComponents';
import HelpTooltip from '../ui/HelpTooltip';
import { describeApiError } from '../../utils/apiErrors';
import { copyTextToClipboard } from '../../utils/clipboard';

type Connection = {
    id: string;
    name: string;
    enabled: boolean;
    base_url: string;
    vicidial_host?: string | null;
    topology: string;
    username_env: string;
    password_env: string;
    timezone?: string;
    last_verification?: Verification;
    last_verified_at?: string | null;
};

type Mapping = {
    id: string;
    connection_id: string;
    name: string;
    enabled: boolean;
    direction: 'outbound' | 'inbound' | 'both';
    campaign_id?: string | null;
    closer_campaigns?: string[];
    user_start: string;
    number_of_lines: number;
    conf_exten: string;
    static_agent_user?: string | null;
    ai_agent: string;
    trusted_context: string;
    trusted_endpoint?: string | null;
    dispositions?: Record<string, string>;
    statuses?: Record<string, string>;
    destinations?: Record<string, TransferDestination>;
    dnc_scope: string;
    callback_type: string;
    agent_available?: boolean;
    connection?: Connection;
    last_verification?: Verification;
};

type Agent = { slug: string; display_name: string };

type Verification = {
    ready?: boolean;
    configuration_ready?: boolean;
    real_call?: {
        verified?: boolean;
        required_directions?: string[];
    };
};

type TransferDestination = {
    type: string;
    target: string;
    description: string;
    status?: string;
};

type ConnectionForm = {
    name: string;
    enabled: boolean;
    base_url: string;
    agent_api_url: string;
    non_agent_api_url: string;
    source: string;
    username_env: string;
    password_env: string;
    verify_ssl: boolean;
    timeout_ms: number | string;
    topology: string;
    vicidial_host: string;
    sip_port: number | string;
    rtp_start: number | string;
    rtp_end: number | string;
    timezone: string;
};

type MappingForm = {
    connection_id: string;
    name: string;
    enabled: boolean;
    direction: Mapping['direction'];
    campaign_id: string;
    closer_campaigns: string;
    user_start: string;
    number_of_lines: number | string;
    conf_exten: string;
    static_agent_user: string;
    ai_agent: string;
    trusted_context: string;
    trusted_endpoint: string;
    dispositions: Record<string, string>;
    statuses: Record<string, string>;
    destinations: Record<string, TransferDestination>;
    dnc_scope: string;
    callback_type: string;
};

type SetupGuidance = {
    vicidial_steps: string[];
    dialplan: string;
    freepbx_trunk: Record<string, unknown>;
    network: { notes: string[] };
    verification_order: string[];
};

type ActivityRange = 'today' | '7d' | '30d';

type ActivityMappingSummary = {
    mapping_id?: string | null;
    mapping_name: string;
    handled: number;
    finalized: number;
    needs_attention: number;
    last_call_at?: string | null;
};

type ActivityCall = {
    id: string;
    started_at?: string | null;
    direction: string;
    masked_number?: string | null;
    remote_agent?: string | null;
    ai_agent?: string | null;
    duration_seconds: number;
    outcome: string;
    disposition?: string | null;
    disposition_confirmed: boolean;
    finalized: boolean;
    needs_attention: boolean;
    mapping_id?: string | null;
};

type VicidialActivity = {
    summary: {
        handled: number;
        finalized: number;
        needs_attention: number;
        average_duration_seconds: number;
        last_call_at?: string | null;
    };
    dispositions: Array<{ status: string; count: number }>;
    by_mapping: ActivityMappingSummary[];
    recent_calls: ActivityCall[];
    scope_note: string;
};

const emptyConnection = {
    name: 'VICIdial',
    enabled: true,
    base_url: 'http://',
    agent_api_url: '',
    non_agent_api_url: '',
    source: 'aava',
    username_env: 'VICIDIAL_API_USER',
    password_env: 'VICIDIAL_API_PASS',
    verify_ssl: true,
    timeout_ms: 5000,
    topology: 'lan_vpn',
    vicidial_host: '',
    sip_port: 5060,
    rtp_start: 10000,
    rtp_end: 20000,
    timezone: 'UTC',
};

const emptyMapping = {
    connection_id: '',
    name: 'AVA Remote Agent',
    enabled: true,
    direction: 'both' as const,
    campaign_id: '',
    closer_campaigns: '',
    user_start: '9001',
    number_of_lines: 1,
    conf_exten: '8371',
    static_agent_user: '9001',
    ai_agent: 'demo_deepgram',
    trusted_context: 'from-vicidial-ra',
    trusted_endpoint: '',
    dispositions: { sale: 'SALE', not_interested: 'NI' } as Record<string, string>,
    statuses: {
        ai_hangup: 'AIHU',
        caller_hangup: 'AICU',
        ai_ingroup_transfer: 'AIXFR',
        ai_extension_transfer: 'AIEXT',
        ai_failure: 'AIFAIL',
        dnc: 'DNC',
        callback: 'CALLBK',
    } as Record<string, string>,
    destinations: {} as Record<string, TransferDestination>,
    dnc_scope: 'campaign',
    callback_type: 'ANYONE',
};

const statusLabel = (mapping: Mapping) => {
    const check = mapping.last_verification;
    if (!mapping.enabled) return { label: 'Disabled', tone: 'text-muted-foreground', ok: false };
    if (!mapping.agent_available)
        return { label: 'Agent unavailable', tone: 'text-destructive', ok: false };
    if (!check) return { label: 'Not verified', tone: 'text-amber-500', ok: false };
    if (!check.configuration_ready)
        return { label: 'Needs attention', tone: 'text-destructive', ok: false };
    if (!check.real_call?.verified) {
        const required = (check.real_call?.required_directions || []).join(' + ');
        return {
            label: `Configuration valid — ${required || 'real'} call test required`,
            tone: 'text-amber-500',
            ok: false,
        };
    }
    return { label: 'Ready', tone: 'text-emerald-500', ok: true };
};

const formatActivityTime = (value?: string | null) => {
    if (!value) return '—';
    const date = new Date(value);
    return Number.isNaN(date.getTime())
        ? '—'
        : new Intl.DateTimeFormat(undefined, {
              month: 'short',
              day: 'numeric',
              hour: 'numeric',
              minute: '2-digit',
          }).format(date);
};

const formatActivityDuration = (seconds: number) => {
    const rounded = Math.max(0, Math.round(Number(seconds) || 0));
    const minutes = Math.floor(rounded / 60);
    const remainder = rounded % 60;
    return minutes ? `${minutes}m ${remainder}s` : `${remainder}s`;
};

const rowInputClass =
    'min-w-0 rounded-md border border-input bg-background px-2.5 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';

const connectionHelp = {
    enabled:
        'Controls whether AAVA may admit and control calls through this VICIdial connection. Disable it to stop new VICIdial-owned calls without deleting the configuration.',
    name: 'A descriptive name shown only in AAVA. It does not need to match a VICIdial server name.',
    baseUrl:
        'Base HTTP or HTTPS URL of the VICIdial web server. AAVA derives the Agent and Non-Agent API endpoints from this address.',
    sipHost:
        'IP address or hostname that FreePBX/Asterisk uses for the VICIdial SIP registration and signaling path. This can differ from the web/API hostname.',
    sipPort: 'SIP signaling port on the VICIdial server. Most chan_sip installations use 5060.',
    topology:
        'Select the network path between AAVA and VICIdial. The setup guide changes its NAT, firewall, and media recommendations for this choice.',
    timezone:
        'IANA timezone used by the VICIdial server, such as America/Phoenix. AAVA converts scheduled callback times into this server-local timezone.',
    rtpStart:
        'First UDP media port used by the VICIdial Asterisk RTP range. Firewalls must permit the configured range between the PBXs.',
    rtpEnd: 'Last UDP media port used by the VICIdial Asterisk RTP range. It must be greater than or equal to the start port.',
    usernameEnv:
        'Name of the server environment variable containing the dedicated VICIdial API username. Enter the variable name, not the username or secret itself.',
    passwordEnv:
        'Name of the server environment variable containing the dedicated VICIdial API password. AAVA does not store the resolved password in this record.',
    source: 'Short source value sent with VICIdial API requests for audit and access-control rules. Keep it stable and allow it for the dedicated API user.',
    timeout:
        'Maximum time AAVA waits for one VICIdial API request before treating it as unavailable. The value is bounded by the backend.',
    verifyTls:
        'Validates the certificate hostname and trust chain for HTTPS API calls. Keep enabled in production; plain HTTP lab servers do not perform TLS.',
};

const mappingHelp = {
    enabled:
        'Controls whether this user-range and extension mapping may admit VICIdial Remote Agent calls and expose VICIdial call-control tools.',
    connection:
        'The VICIdial API/SIP connection this mapping uses for correlation, readiness checks, dispositions, callbacks, DNC, and transfers.',
    name: 'Operator-facing label for this mapping. It does not change VICIdial campaign or Remote Agent identifiers.',
    direction:
        'Limits which VICIdial calls this mapping accepts. Both requires a successful real outbound and inbound/closer call before Ready.',
    agent: 'Active AAVA Agent that supplies the prompt, provider or pipeline, voice, and tools for calls admitted by this mapping.',
    campaign:
        'Existing VICIdial outbound campaign ID used for Remote Agent login, outbound correlation, dispositions, and native callbacks.',
    closerCampaigns:
        'Comma-separated VICIdial inbound-group IDs accepted by this mapping. These are required for inbound/closer or blended calls.',
    userStart:
        'First dedicated VICIdial Remote Agent user. Additional lines increment this user numerically, so two lines starting at 9001 require users 9001 and 9002.',
    lines: 'Maximum Remote Agent lines represented by this mapping. Create every VICIdial user in the contiguous range; all lines share the same Remote Agent extension.',
    extension:
        'VICIdial Phone login/conf_exten that calls AAVA. Every line in the Remote Agent range shares this one SIP registration.',
    fallback:
        'Optional compatibility user for a one-line mapping only. Leave empty for multi-line mappings; AAVA normally resolves the live user through VICIdial APIs.',
    context:
        'Exact trusted Asterisk dialplan context that sets VICIdial ownership, call ID, mapping ID, and AI_AGENT before entering Stasis.',
    endpoint:
        'Optional PJSIP endpoint name used by the generated dialplan to reject calls arriving from any other endpoint.',
    dispositions:
        'Business choices the AI may set during a call. Each friendly key maps to an existing VICIdial status of at most six characters.',
    dispositionName:
        'Stable, lowercase Agent-facing choice such as sale or not_interested. This is the value used by the set_call_disposition tool.',
    dispositionStatus:
        'Existing VICIdial campaign or system status written when this business outcome is finalized.',
    lifecycle:
        'Statuses used automatically for hangup, transfer, failure, DNC, and callback outcomes. Every value must already exist in VICIdial and be no more than six characters.',
    dncScope:
        'Campaign DNC blocks the number only for this campaign; system-wide DNC blocks it across VICIdial. The DNC record is created before final hangup.',
    callbackOwnership:
        'ANYONE allows any eligible agent to receive the callback. USERONLY assigns it to the Remote Agent user that handled the current call.',
    destinations:
        'Allowlisted cold-transfer targets exposed to the AI. Warm, attended, conference, and arbitrary destinations are not supported.',
    destinationName: 'Stable Agent-facing key used by the transfer tool, such as sales or support.',
    destinationType:
        'Choose a VICIdial inbound group or a dialable extension. The transfer remains VICIdial-owned.',
    destinationTarget:
        'Exact VICIdial in-group ID or extension number to receive the cold transfer.',
    destinationDescription:
        'Human-readable description that helps the AI choose the correct approved destination.',
    destinationStatus:
        'Optional existing VICIdial status for this transfer. Leave empty to use the mapping lifecycle transfer status.',
};

const lifecycleHelp: Record<string, string> = {
    ai_hangup:
        'Status requested when AAVA completes the conversation and hangs up while the VICIdial call is still active.',
    caller_hangup:
        'Requested status when the customer disconnects first. VICIdial may retain its native terminal XFER status if cleanup already completed.',
    ai_ingroup_transfer: 'Status used when VICIdial accepts an in-group cold transfer.',
    ai_extension_transfer: 'Status used when VICIdial accepts an extension cold transfer.',
    ai_failure:
        'Failure status used when AAVA cannot complete required VICIdial control or reporting.',
    dnc: 'Final status used only after the requested campaign or system DNC record has been created or confirmed.',
    callback:
        'Final status used only after VICIdial creates and verifies the native scheduled callback record.',
};

const trunkHelp: Record<string, string> = {
    name: 'Suggested FreePBX trunk name. It is operator-facing and can be changed if the endpoint checks and dialplan are updated consistently.',
    username: 'VICIdial Phone extension used as the SIP registration username.',
    auth_username:
        'Authentication username for the VICIdial Phone. It normally matches the Phone extension.',
    secret: 'Use the Phone conf_secret from VICIdial. The Phone pass field is not the SIP registration secret.',
    authentication:
        'Outbound authentication means AAVA authenticates when registering or sending requests to VICIdial.',
    registration: 'Send registration lets VICIdial learn AAVA’s current contact address.',
    sip_server: 'VICIdial Asterisk address that receives the Remote Agent Phone registration.',
    sip_server_port: 'VICIdial SIP signaling port used by the registration.',
    context:
        'Dedicated inbound context applied to calls arriving on the trusted VICIdial endpoint.',
    contact_user: 'Contact user VICIdial calls for the shared Remote Agent Phone extension.',
    match_permit:
        'Source address allowed to identify this endpoint. Restrict it to the VICIdial signaling address.',
    codec: 'Validated baseline codec for the Remote Agent and AI media path.',
    dtmf: 'DTMF transport expected by the validated VICIdial/Asterisk profile.',
    direct_media: 'Keep disabled so the two PBXs remain in their owned signaling and media paths.',
    qualify:
        'OPTIONS monitoring should remain enabled when both sides respond reliably; disable only a reproduced failing direction.',
};

const uniqueKey = (record: Record<string, unknown>, prefix: string) => {
    let index = 1;
    while (record[`${prefix}_${index}`] !== undefined) index += 1;
    return `${prefix}_${index}`;
};

const renameKey = <T,>(
    record: Record<string, T>,
    oldKey: string,
    newKey: string
): Record<string, T> => {
    const clean = newKey
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9_-]+/g, '_');
    if (!clean || (clean !== oldKey && record[clean] !== undefined)) return record;
    return Object.fromEntries(
        Object.entries(record).map(([key, value]) => [key === oldKey ? clean : key, value])
    );
};

export const VicidialRemoteAgentsTab = () => {
    const [connections, setConnections] = useState<Connection[]>([]);
    const [mappings, setMappings] = useState<Mapping[]>([]);
    const [agents, setAgents] = useState<Agent[]>([]);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState('');
    const [connectionModal, setConnectionModal] = useState(false);
    const [mappingModal, setMappingModal] = useState(false);
    const [editingConnection, setEditingConnection] = useState<string | null>(null);
    const [editingMapping, setEditingMapping] = useState<string | null>(null);
    const [connectionForm, setConnectionForm] = useState<ConnectionForm>(emptyConnection);
    const [mappingForm, setMappingForm] = useState<MappingForm>(emptyMapping);
    const [guidance, setGuidance] = useState<SetupGuidance | null>(null);
    const [activityRange, setActivityRange] = useState<ActivityRange>('7d');
    const [activityMappingId, setActivityMappingId] = useState('');
    const [activity, setActivity] = useState<VicidialActivity | null>(null);
    const [activityLoading, setActivityLoading] = useState(true);
    const [activityError, setActivityError] = useState('');

    const loadActivity = useCallback(async () => {
        setActivityLoading(true);
        setActivityError('');
        try {
            const response = await axios.get('/api/outbound/vicidial/activity', {
                params: {
                    range: activityRange,
                    mapping_id: activityMappingId || undefined,
                    limit: 10,
                },
            });
            setActivity(response.data);
        } catch (error) {
            setActivityError(describeApiError(error, 'Unable to load Remote Agent activity'));
        } finally {
            setActivityLoading(false);
        }
    }, [activityMappingId, activityRange]);

    const refresh = async () => {
        setLoading(true);
        try {
            const [connectionsResponse, mappingsResponse, metaResponse] = await Promise.all([
                axios.get('/api/outbound/vicidial/connections'),
                axios.get('/api/outbound/vicidial/mappings'),
                axios.get('/api/outbound/meta'),
            ]);
            setConnections(connectionsResponse.data || []);
            setMappings(mappingsResponse.data || []);
            setAgents((metaResponse.data?.agents || []).filter((agent: Agent) => agent?.slug));
        } catch (error) {
            toast.error(describeApiError(error, 'Unable to load VICIdial setup'));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void refresh();
    }, []);

    useEffect(() => {
        void loadActivity();
    }, [loadActivity]);

    const connectionOptions = useMemo(
        () => connections.map(item => ({ value: item.id, label: item.name })),
        [connections]
    );
    const agentOptions = useMemo(
        () =>
            agents.map(item => ({
                value: item.slug,
                label: `${item.display_name} (${item.slug})`,
            })),
        [agents]
    );

    const openConnection = (connection?: Connection) => {
        setEditingConnection(connection?.id || null);
        setConnectionForm(
            connection ? { ...emptyConnection, ...connection } : { ...emptyConnection }
        );
        setConnectionModal(true);
    };

    const openMapping = (mapping?: Mapping) => {
        setEditingMapping(mapping?.id || null);
        setMappingForm(
            mapping
                ? {
                      ...emptyMapping,
                      ...mapping,
                      campaign_id: mapping.campaign_id || '',
                      closer_campaigns: (mapping.closer_campaigns || []).join(', '),
                      static_agent_user: mapping.static_agent_user || '',
                      trusted_endpoint: mapping.trusted_endpoint || '',
                      dispositions: { ...(mapping.dispositions || {}) },
                      statuses: { ...emptyMapping.statuses, ...(mapping.statuses || {}) },
                      destinations: { ...(mapping.destinations || {}) },
                  }
                : {
                      ...emptyMapping,
                      connection_id: connections[0]?.id || '',
                      ai_agent:
                          agents.find(agent => agent.slug === 'demo_deepgram')?.slug ||
                          agents[0]?.slug ||
                          '',
                  }
        );
        setMappingModal(true);
    };

    const saveConnection = async () => {
        setBusy('save-connection');
        try {
            const payload = {
                ...connectionForm,
                timeout_ms: Number(connectionForm.timeout_ms),
                sip_port: Number(connectionForm.sip_port),
                rtp_start: Number(connectionForm.rtp_start),
                rtp_end: Number(connectionForm.rtp_end),
            };
            if (editingConnection)
                await axios.put(`/api/outbound/vicidial/connections/${editingConnection}`, payload);
            else await axios.post('/api/outbound/vicidial/connections', payload);
            toast.success('VICIdial connection saved');
            setConnectionModal(false);
            await refresh();
        } catch (error) {
            toast.error(describeApiError(error, 'Unable to save connection'));
        } finally {
            setBusy('');
        }
    };

    const saveMapping = async () => {
        setBusy('save-mapping');
        try {
            const payload = {
                ...mappingForm,
                number_of_lines: Number(mappingForm.number_of_lines),
                closer_campaigns: String(mappingForm.closer_campaigns || '')
                    .split(',')
                    .map((v: string) => v.trim())
                    .filter(Boolean),
                dispositions: { ...(mappingForm.dispositions || {}) },
                statuses: { ...(mappingForm.statuses || {}) },
                destinations: { ...(mappingForm.destinations || {}) },
            };
            if (editingMapping)
                await axios.put(`/api/outbound/vicidial/mappings/${editingMapping}`, payload);
            else await axios.post('/api/outbound/vicidial/mappings', payload);
            toast.success('Remote Agent mapping saved');
            setMappingModal(false);
            await refresh();
        } catch (error) {
            toast.error(describeApiError(error, 'Unable to save mapping'));
        } finally {
            setBusy('');
        }
    };

    const verify = async (kind: 'connections' | 'mappings', id: string) => {
        setBusy(`verify-${id}`);
        try {
            const response = await axios.post(`/api/outbound/vicidial/${kind}/${id}/verify`);
            if (response.data?.ready) toast.success('API connection verified');
            else if (kind === 'mappings' && response.data?.configuration_ready)
                toast.success('Configuration checks passed; complete a real call test');
            else toast.warning('Verification found items that need attention');
            await refresh();
        } catch (error) {
            toast.error(describeApiError(error, 'Verification failed'));
        } finally {
            setBusy('');
        }
    };

    const remove = async (kind: 'connections' | 'mappings', id: string) => {
        if (
            !window.confirm(
                `Delete this ${kind === 'connections' ? 'connection and its mappings' : 'mapping'}?`
            )
        )
            return;
        try {
            await axios.delete(`/api/outbound/vicidial/${kind}/${id}`);
            toast.success('Deleted');
            await refresh();
        } catch (error) {
            toast.error(describeApiError(error, 'Delete failed'));
        }
    };

    const openGuidance = async (id: string) => {
        setBusy(`guidance-${id}`);
        try {
            const response = await axios.get(`/api/outbound/vicidial/mappings/${id}/guidance`);
            setGuidance(response.data);
        } catch (error) {
            toast.error(describeApiError(error, 'Unable to generate setup guidance'));
        } finally {
            setBusy('');
        }
    };

    if (loading)
        return (
            <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading VICIdial integration…
            </div>
        );

    return (
        <div className="space-y-6">
            <div className="rounded-lg border border-border bg-card p-4">
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                        <div className="flex items-center gap-2">
                            <h2 className="text-lg font-semibold">VICIdial Remote Agents</h2>
                            <HelpTooltip
                                ariaLabel="Help for VICIdial Remote Agents"
                                content="VICIdial owns campaign dialing, customer channels, compliance, dispositions, and reports. AAVA supplies the AI conversation on the trusted Remote Agent leg."
                            />
                        </div>
                        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
                            Connect an existing VICIdial campaign to an AAVA Agent. VICIdial keeps
                            ownership of the customer call, campaign state, transfers, callbacks,
                            DNC, and final disposition.
                        </p>
                    </div>
                    <button
                        title="Reload connections, mappings, Agent availability, and saved verification results"
                        onClick={() => void Promise.all([refresh(), loadActivity()])}
                        className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-muted"
                    >
                        <RefreshCw className="h-4 w-4" />
                        Refresh
                    </button>
                </div>
                <div className="mt-4 grid gap-3 md:grid-cols-4">
                    {[
                        '1. Connect APIs',
                        '2. Map Remote Agent',
                        '3. Apply PBX setup',
                        '4. Verify real calls',
                    ].map((label, index) => (
                        <div
                            key={label}
                            className={`rounded-md border p-3 text-sm ${index === 0 && connections.length ? 'border-emerald-500/40 bg-emerald-500/5' : 'border-border'}`}
                        >
                            {label}
                        </div>
                    ))}
                </div>
                <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-md border bg-muted/20 px-3 py-2 text-xs">
                    <div className="flex flex-wrap gap-x-4 gap-y-1">
                        <span className="font-medium">AAVA-handled activity</span>
                        {activityLoading && !activity ? (
                            <span className="text-muted-foreground">Loading…</span>
                        ) : activityError ? (
                            <span className="text-destructive">Activity unavailable</span>
                        ) : (
                            <>
                                <span>{activity?.summary.handled ?? 0} handled</span>
                                <span>{activity?.summary.finalized ?? 0} finalized</span>
                                <span
                                    className={
                                        activity?.summary.needs_attention
                                            ? 'text-amber-500'
                                            : 'text-muted-foreground'
                                    }
                                >
                                    {activity?.summary.needs_attention ?? 0} need attention
                                </span>
                            </>
                        )}
                    </div>
                    <a href="#vicidial-activity" className="text-primary hover:underline">
                        View activity
                    </a>
                </div>
            </div>

            <section className="space-y-3">
                <div className="flex items-center justify-between">
                    <div>
                        <div className="flex items-center gap-2">
                            <h3 className="font-semibold">1. API connections</h3>
                            <HelpTooltip
                                ariaLabel="Help for API connections"
                                content="Defines how AAVA reaches the VICIdial Agent and Non-Agent APIs and which SIP/RTP host information appears in the generated PBX guide. Credentials remain server-side environment variables."
                            />
                        </div>
                        <p className="text-xs text-muted-foreground">
                            Credentials remain environment-variable references and are never stored
                            here.
                        </p>
                    </div>
                    <button
                        title="Add a VICIdial API, SIP, network, and timezone connection"
                        onClick={() => openConnection()}
                        className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground"
                    >
                        <Plus className="h-4 w-4" />
                        Add connection
                    </button>
                </div>
                {connections.length === 0 ? (
                    <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
                        Add the VICIdial server before creating a Remote Agent mapping.
                    </div>
                ) : (
                    connections.map(connection => (
                        <div
                            key={connection.id}
                            className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-4"
                        >
                            <div>
                                <div className="flex items-center gap-2 font-medium">
                                    <ServerCog className="h-4 w-4" />
                                    {connection.name}
                                    {connection.last_verification?.ready && (
                                        <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                                    )}
                                </div>
                                <div className="mt-1 font-mono text-xs text-muted-foreground">
                                    {connection.base_url} · {connection.topology}
                                </div>
                            </div>
                            <div className="flex gap-2">
                                <button
                                    title="Check both API versions, credentials, campaign visibility, and logged-in Remote Agents without running mutating functions"
                                    onClick={() => void verify('connections', connection.id)}
                                    disabled={busy === `verify-${connection.id}`}
                                    className="rounded-md border px-3 py-2 text-sm hover:bg-muted"
                                >
                                    Verify API
                                </button>
                                <button
                                    title="Edit this VICIdial connection"
                                    aria-label="Edit connection"
                                    onClick={() => openConnection(connection)}
                                    className="rounded-md border p-2 hover:bg-muted"
                                >
                                    <Pencil className="h-4 w-4" />
                                </button>
                                <button
                                    title="Delete this connection and all mappings that use it"
                                    aria-label="Delete connection"
                                    onClick={() => void remove('connections', connection.id)}
                                    className="rounded-md border p-2 text-destructive hover:bg-muted"
                                >
                                    <Trash2 className="h-4 w-4" />
                                </button>
                            </div>
                        </div>
                    ))
                )}
            </section>

            <section className="space-y-3">
                <div className="flex items-center justify-between">
                    <div>
                        <div className="flex items-center gap-2">
                            <h3 className="font-semibold">2. Remote Agent mappings</h3>
                            <HelpTooltip
                                ariaLabel="Help for Remote Agent mappings"
                                content="Binds an existing VICIdial campaign, contiguous Remote Agent user range, and shared Phone extension to one active AAVA Agent and an allowlisted call-control policy."
                            />
                        </div>
                        <p className="text-xs text-muted-foreground">
                            Each mapping binds a VICIdial user range and extension to one active
                            AAVA Agent.
                        </p>
                    </div>
                    <button
                        title={
                            connections.length
                                ? 'Add a Remote Agent mapping'
                                : 'Add and verify a VICIdial connection first'
                        }
                        disabled={!connections.length}
                        onClick={() => openMapping()}
                        className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50"
                    >
                        <Plus className="h-4 w-4" />
                        Add mapping
                    </button>
                </div>
                {mappings.length === 0 ? (
                    <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
                        No Remote Agent mappings yet.
                    </div>
                ) : (
                    mappings.map(mapping => {
                        const status = statusLabel(mapping);
                        const mappingActivity = activity?.by_mapping.find(
                            summary => summary.mapping_id === mapping.id
                        );
                        return (
                            <div key={mapping.id} className="rounded-lg border bg-card p-4">
                                <div className="flex flex-wrap items-start justify-between gap-3">
                                    <div>
                                        <div className="font-medium">{mapping.name}</div>
                                        <div className="mt-1 text-xs text-muted-foreground">
                                            Users {mapping.user_start}
                                            {mapping.number_of_lines > 1
                                                ? `–${Number(mapping.user_start) + mapping.number_of_lines - 1}`
                                                : ''}{' '}
                                            · Extension {mapping.conf_exten} · {mapping.direction} →{' '}
                                            <span className="font-mono text-foreground">
                                                {mapping.ai_agent}
                                            </span>
                                        </div>
                                        <div
                                            className={`mt-2 flex items-center gap-1.5 text-xs ${status.tone}`}
                                        >
                                            {status.ok ? (
                                                <CheckCircle2 className="h-3.5 w-3.5" />
                                            ) : (
                                                <AlertTriangle className="h-3.5 w-3.5" />
                                            )}
                                            {status.label}
                                            <HelpTooltip
                                                ariaLabel={`Help for ${mapping.name} readiness`}
                                                content="Ready requires valid API/configuration checks plus a correlated real call with confirmed VICIdial terminal control in every configured direction. SIP registration by itself is not sufficient."
                                            />
                                        </div>
                                        <div className="mt-2 text-xs text-muted-foreground">
                                            {mappingActivity ? (
                                                <>
                                                    {mappingActivity.handled} AAVA-handled ·{' '}
                                                    {mappingActivity.finalized} finalized
                                                    {mappingActivity.needs_attention > 0 && (
                                                        <span className="text-amber-500">
                                                            {' '}
                                                            · {mappingActivity.needs_attention} need
                                                            attention
                                                        </span>
                                                    )}
                                                </>
                                            ) : activityLoading ? (
                                                'Loading activity…'
                                            ) : activityError ? (
                                                'Activity unavailable'
                                            ) : (
                                                'No AAVA-handled calls in this range'
                                            )}
                                        </div>
                                    </div>
                                    <div className="flex flex-wrap gap-2">
                                        <button
                                            title="Validate API access, campaign, AAVA Agent, every mapped VICIdial user, logged-in visibility, and retained real-call evidence"
                                            onClick={() => void verify('mappings', mapping.id)}
                                            className="rounded-md border px-3 py-2 text-sm hover:bg-muted"
                                        >
                                            Run checks
                                        </button>
                                        <button
                                            title="Generate the VICIdial, FreePBX trunk, trusted dialplan, NAT, and acceptance checklist for this mapping"
                                            onClick={() => void openGuidance(mapping.id)}
                                            className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-muted"
                                        >
                                            <Link2 className="h-4 w-4" />
                                            Setup guide
                                        </button>
                                        <button
                                            title="Edit this Remote Agent mapping and its call-control policy"
                                            aria-label="Edit mapping"
                                            onClick={() => openMapping(mapping)}
                                            className="rounded-md border p-2 hover:bg-muted"
                                        >
                                            <Pencil className="h-4 w-4" />
                                        </button>
                                        <button
                                            title="Delete this mapping without changing VICIdial campaign records"
                                            aria-label="Delete mapping"
                                            onClick={() => void remove('mappings', mapping.id)}
                                            className="rounded-md border p-2 text-destructive hover:bg-muted"
                                        >
                                            <Trash2 className="h-4 w-4" />
                                        </button>
                                    </div>
                                </div>
                            </div>
                        );
                    })
                )}
            </section>

            <section
                id="vicidial-activity"
                className="scroll-mt-4 space-y-4 rounded-lg border bg-card p-4"
            >
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                        <div className="flex items-center gap-2">
                            <h3 className="font-semibold">Remote Agent activity</h3>
                            <HelpTooltip
                                ariaLabel="Help for Remote Agent activity"
                                content="Read-only metrics from AAVA Call History. This shows calls that reached an AAVA Remote Agent, not every VICIdial dial attempt or carrier outcome."
                            />
                        </div>
                        <p className="mt-1 max-w-3xl text-xs text-muted-foreground">
                            {activity?.scope_note ||
                                'Only calls delivered to AAVA are counted. VICIdial attempts that never reached an AAVA Remote Agent are not included.'}
                        </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                        <label className="sr-only" htmlFor="vicidial-activity-mapping">
                            Activity mapping
                        </label>
                        <select
                            id="vicidial-activity-mapping"
                            aria-label="Activity mapping"
                            value={activityMappingId}
                            onChange={event => setActivityMappingId(event.target.value)}
                            className="rounded-md border border-input bg-background px-3 py-2 text-sm"
                        >
                            <option value="">All mappings</option>
                            {mappings.map(mapping => (
                                <option key={mapping.id} value={mapping.id}>
                                    {mapping.name}
                                </option>
                            ))}
                        </select>
                        <label className="sr-only" htmlFor="vicidial-activity-range">
                            Activity range
                        </label>
                        <select
                            id="vicidial-activity-range"
                            aria-label="Activity range"
                            value={activityRange}
                            onChange={event =>
                                setActivityRange(event.target.value as ActivityRange)
                            }
                            className="rounded-md border border-input bg-background px-3 py-2 text-sm"
                        >
                            <option value="today">Today</option>
                            <option value="7d">Last 7 days</option>
                            <option value="30d">Last 30 days</option>
                        </select>
                        <button
                            type="button"
                            title="Reload AAVA-handled VICIdial activity"
                            aria-label="Refresh Remote Agent activity"
                            onClick={() => void loadActivity()}
                            className="rounded-md border p-2 hover:bg-muted"
                        >
                            <RefreshCw
                                className={`h-4 w-4 ${activityLoading ? 'animate-spin' : ''}`}
                            />
                        </button>
                    </div>
                </div>

                {activityError ? (
                    <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
                        <span>{activityError}</span>
                        <button
                            type="button"
                            onClick={() => void loadActivity()}
                            className="rounded-md border px-3 py-1.5 text-xs"
                        >
                            Retry
                        </button>
                    </div>
                ) : activityLoading && !activity ? (
                    <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin" /> Loading activity…
                    </div>
                ) : (
                    <>
                        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                            {[
                                ['Handled by AAVA', activity?.summary.handled ?? 0],
                                ['Finalized in VICIdial', activity?.summary.finalized ?? 0],
                                ['Needs attention', activity?.summary.needs_attention ?? 0],
                                [
                                    'Average duration',
                                    formatActivityDuration(
                                        activity?.summary.average_duration_seconds ?? 0
                                    ),
                                ],
                                [
                                    'Last handled call',
                                    formatActivityTime(activity?.summary.last_call_at),
                                ],
                            ].map(([label, value]) => (
                                <div key={label} className="rounded-md border p-3">
                                    <div className="text-xs text-muted-foreground">{label}</div>
                                    <div className="mt-1 text-lg font-semibold">{value}</div>
                                </div>
                            ))}
                        </div>

                        <div>
                            <div className="text-sm font-medium">Confirmed dispositions</div>
                            <div className="mt-2 flex flex-wrap gap-2">
                                {activity?.dispositions.length ? (
                                    activity.dispositions.map(item => (
                                        <span
                                            key={item.status}
                                            className="rounded-full border bg-muted/40 px-2.5 py-1 text-xs"
                                        >
                                            {item.status} · {item.count}
                                        </span>
                                    ))
                                ) : (
                                    <span className="text-xs text-muted-foreground">
                                        No confirmed VICIdial dispositions in this range.
                                    </span>
                                )}
                            </div>
                        </div>

                        <div>
                            <div className="mb-2 flex items-center justify-between gap-3">
                                <div className="text-sm font-medium">Recent AAVA-handled calls</div>
                                <a href="/history" className="text-xs text-primary hover:underline">
                                    Open Call History
                                </a>
                            </div>
                            {activity?.recent_calls.length ? (
                                <div className="overflow-x-auto rounded-md border">
                                    <table className="w-full min-w-[860px] text-left text-sm">
                                        <thead className="bg-muted/40 text-xs text-muted-foreground">
                                            <tr>
                                                <th className="px-3 py-2 font-medium">Time</th>
                                                <th className="px-3 py-2 font-medium">Direction</th>
                                                <th className="px-3 py-2 font-medium">Number</th>
                                                <th className="px-3 py-2 font-medium">
                                                    Remote Agent
                                                </th>
                                                <th className="px-3 py-2 font-medium">
                                                    AAVA Agent
                                                </th>
                                                <th className="px-3 py-2 font-medium">Duration</th>
                                                <th className="px-3 py-2 font-medium">Outcome</th>
                                                <th className="px-3 py-2 font-medium">
                                                    Disposition
                                                </th>
                                                <th className="px-3 py-2 font-medium"></th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y">
                                            {activity.recent_calls.map(call => (
                                                <tr key={call.id}>
                                                    <td className="whitespace-nowrap px-3 py-2">
                                                        {formatActivityTime(call.started_at)}
                                                    </td>
                                                    <td className="capitalize px-3 py-2">
                                                        {call.direction}
                                                    </td>
                                                    <td className="font-mono px-3 py-2">
                                                        {call.masked_number || '—'}
                                                    </td>
                                                    <td className="px-3 py-2">
                                                        {call.remote_agent || '—'}
                                                    </td>
                                                    <td className="font-mono px-3 py-2">
                                                        {call.ai_agent || '—'}
                                                    </td>
                                                    <td className="whitespace-nowrap px-3 py-2">
                                                        {formatActivityDuration(
                                                            call.duration_seconds
                                                        )}
                                                    </td>
                                                    <td className="capitalize px-3 py-2">
                                                        {call.outcome}
                                                    </td>
                                                    <td className="px-3 py-2">
                                                        <span
                                                            className={
                                                                call.needs_attention
                                                                    ? 'text-amber-500'
                                                                    : 'text-emerald-500'
                                                            }
                                                        >
                                                            {call.disposition ||
                                                                (call.finalized
                                                                    ? 'Finalized'
                                                                    : 'Pending')}
                                                            {!call.disposition_confirmed &&
                                                                call.disposition &&
                                                                ' (requested)'}
                                                        </span>
                                                    </td>
                                                    <td className="px-3 py-2 text-right">
                                                        <a
                                                            href={`/history?id=${encodeURIComponent(call.id)}`}
                                                            className="text-primary hover:underline"
                                                        >
                                                            Details
                                                        </a>
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            ) : (
                                <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
                                    No VICIdial calls reached AAVA in this range.
                                </div>
                            )}
                        </div>
                    </>
                )}
            </section>

            <Modal
                isOpen={connectionModal}
                onClose={() => setConnectionModal(false)}
                title={editingConnection ? 'Edit VICIdial connection' : 'Add VICIdial connection'}
                size="lg"
                footer={
                    <>
                        <button
                            title="Close without saving changes"
                            onClick={() => setConnectionModal(false)}
                            className="rounded-md border px-4 py-2 text-sm"
                        >
                            Cancel
                        </button>
                        <button
                            title="Save the connection; use Verify API afterward to test it"
                            onClick={() => void saveConnection()}
                            disabled={busy === 'save-connection'}
                            className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground"
                        >
                            Save connection
                        </button>
                    </>
                }
            >
                <FormSwitch
                    label="Connection enabled"
                    tooltip={connectionHelp.enabled}
                    checked={connectionForm.enabled}
                    onChange={e =>
                        setConnectionForm({ ...connectionForm, enabled: e.target.checked })
                    }
                    description="Disabled connections never admit or control VICIdial calls."
                />
                <div className="grid gap-x-4 md:grid-cols-2">
                    <FormInput
                        label="Connection name"
                        tooltip={connectionHelp.name}
                        value={connectionForm.name}
                        onChange={e =>
                            setConnectionForm({ ...connectionForm, name: e.target.value })
                        }
                    />
                    <FormInput
                        label="VICIdial base URL"
                        tooltip={connectionHelp.baseUrl}
                        value={connectionForm.base_url}
                        onChange={e =>
                            setConnectionForm({ ...connectionForm, base_url: e.target.value })
                        }
                        placeholder="http://192.168.10.100"
                    />
                    <FormInput
                        label="VICIdial SIP host"
                        tooltip={connectionHelp.sipHost}
                        value={connectionForm.vicidial_host || ''}
                        onChange={e =>
                            setConnectionForm({ ...connectionForm, vicidial_host: e.target.value })
                        }
                    />
                    <FormInput
                        label="SIP port"
                        tooltip={connectionHelp.sipPort}
                        type="number"
                        value={connectionForm.sip_port}
                        onChange={e =>
                            setConnectionForm({ ...connectionForm, sip_port: e.target.value })
                        }
                    />
                    <FormSelect
                        label="Network topology"
                        tooltip={connectionHelp.topology}
                        value={connectionForm.topology}
                        onChange={e =>
                            setConnectionForm({ ...connectionForm, topology: e.target.value })
                        }
                        options={[
                            { value: 'lan_vpn', label: 'LAN / VPN' },
                            { value: 'ava_behind_nat', label: 'AAVA behind NAT' },
                            { value: 'public_sbc', label: 'Public / SBC' },
                        ]}
                    />
                    <FormInput
                        label="VICIdial timezone"
                        tooltip={connectionHelp.timezone}
                        value={connectionForm.timezone || 'UTC'}
                        onChange={e =>
                            setConnectionForm({ ...connectionForm, timezone: e.target.value })
                        }
                        placeholder="America/Phoenix"
                    />
                    <FormInput
                        label="RTP start port"
                        tooltip={connectionHelp.rtpStart}
                        type="number"
                        value={connectionForm.rtp_start}
                        onChange={e =>
                            setConnectionForm({ ...connectionForm, rtp_start: e.target.value })
                        }
                    />
                    <FormInput
                        label="RTP end port"
                        tooltip={connectionHelp.rtpEnd}
                        type="number"
                        value={connectionForm.rtp_end}
                        onChange={e =>
                            setConnectionForm({ ...connectionForm, rtp_end: e.target.value })
                        }
                    />
                    <FormInput
                        label="API username environment variable"
                        tooltip={connectionHelp.usernameEnv}
                        value={connectionForm.username_env}
                        onChange={e =>
                            setConnectionForm({ ...connectionForm, username_env: e.target.value })
                        }
                    />
                    <FormInput
                        label="API password environment variable"
                        tooltip={connectionHelp.passwordEnv}
                        value={connectionForm.password_env}
                        onChange={e =>
                            setConnectionForm({ ...connectionForm, password_env: e.target.value })
                        }
                    />
                    <FormInput
                        label="Source label"
                        tooltip={connectionHelp.source}
                        value={connectionForm.source}
                        onChange={e =>
                            setConnectionForm({ ...connectionForm, source: e.target.value })
                        }
                    />
                    <FormInput
                        label="Timeout (ms)"
                        tooltip={connectionHelp.timeout}
                        type="number"
                        value={connectionForm.timeout_ms}
                        onChange={e =>
                            setConnectionForm({ ...connectionForm, timeout_ms: e.target.value })
                        }
                    />
                </div>
                <FormSwitch
                    label="Verify TLS certificates"
                    tooltip={connectionHelp.verifyTls}
                    checked={connectionForm.verify_ssl}
                    onChange={e =>
                        setConnectionForm({ ...connectionForm, verify_ssl: e.target.checked })
                    }
                    description="Keep enabled for HTTPS with a trusted certificate; HTTP lab servers do not use TLS."
                />
            </Modal>

            <Modal
                isOpen={mappingModal}
                onClose={() => setMappingModal(false)}
                title={editingMapping ? 'Edit Remote Agent mapping' : 'Add Remote Agent mapping'}
                size="xl"
                allowFullscreen
                footer={
                    <>
                        <button
                            title="Close without saving changes"
                            onClick={() => setMappingModal(false)}
                            className="rounded-md border px-4 py-2 text-sm"
                        >
                            Cancel
                        </button>
                        <button
                            title="Save the mapping; run checks and complete real-call verification afterward"
                            onClick={() => void saveMapping()}
                            disabled={busy === 'save-mapping'}
                            className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground"
                        >
                            Save mapping
                        </button>
                    </>
                }
            >
                <FormSwitch
                    label="Mapping enabled"
                    tooltip={mappingHelp.enabled}
                    checked={mappingForm.enabled}
                    onChange={e => setMappingForm({ ...mappingForm, enabled: e.target.checked })}
                    description="Disabled mappings reject new Remote Agent calls and expose no VICIdial tools."
                />
                <div className="grid gap-x-4 md:grid-cols-2">
                    <FormSelect
                        label="VICIdial connection"
                        tooltip={mappingHelp.connection}
                        value={mappingForm.connection_id}
                        onChange={e =>
                            setMappingForm({ ...mappingForm, connection_id: e.target.value })
                        }
                        options={connectionOptions}
                    />
                    <FormInput
                        label="Mapping name"
                        tooltip={mappingHelp.name}
                        value={mappingForm.name}
                        onChange={e => setMappingForm({ ...mappingForm, name: e.target.value })}
                    />
                    <FormSelect
                        label="Call direction"
                        tooltip={mappingHelp.direction}
                        value={mappingForm.direction}
                        onChange={e =>
                            setMappingForm({ ...mappingForm, direction: e.target.value })
                        }
                        options={[
                            { value: 'both', label: 'Inbound and outbound' },
                            { value: 'outbound', label: 'Outbound only' },
                            { value: 'inbound', label: 'Inbound / closer only' },
                        ]}
                    />
                    <FormSelect
                        label="AAVA Agent"
                        tooltip={mappingHelp.agent}
                        value={mappingForm.ai_agent}
                        onChange={e => setMappingForm({ ...mappingForm, ai_agent: e.target.value })}
                        options={agentOptions}
                    />
                    <FormInput
                        label="VICIdial campaign ID"
                        tooltip={mappingHelp.campaign}
                        value={mappingForm.campaign_id}
                        onChange={e =>
                            setMappingForm({ ...mappingForm, campaign_id: e.target.value })
                        }
                    />
                    <FormInput
                        label="Closer campaigns"
                        tooltip={mappingHelp.closerCampaigns}
                        value={mappingForm.closer_campaigns}
                        onChange={e =>
                            setMappingForm({ ...mappingForm, closer_campaigns: e.target.value })
                        }
                        placeholder="SALESLINE, SUPPORT"
                    />
                    <FormInput
                        label="Starting Remote Agent user"
                        tooltip={mappingHelp.userStart}
                        value={mappingForm.user_start}
                        onChange={e =>
                            setMappingForm({ ...mappingForm, user_start: e.target.value })
                        }
                    />
                    <FormInput
                        label="Number of lines"
                        tooltip={mappingHelp.lines}
                        type="number"
                        min={1}
                        max={100}
                        value={mappingForm.number_of_lines}
                        onChange={e =>
                            setMappingForm({ ...mappingForm, number_of_lines: e.target.value })
                        }
                    />
                    <FormInput
                        label="Remote Agent extension"
                        tooltip={mappingHelp.extension}
                        value={mappingForm.conf_exten}
                        onChange={e =>
                            setMappingForm({ ...mappingForm, conf_exten: e.target.value })
                        }
                    />
                    <FormInput
                        label="One-line fallback user"
                        tooltip={mappingHelp.fallback}
                        value={mappingForm.static_agent_user}
                        onChange={e =>
                            setMappingForm({ ...mappingForm, static_agent_user: e.target.value })
                        }
                    />
                    <FormInput
                        label="Trusted AAVA dialplan context"
                        tooltip={mappingHelp.context}
                        value={mappingForm.trusted_context}
                        onChange={e =>
                            setMappingForm({ ...mappingForm, trusted_context: e.target.value })
                        }
                    />
                    <FormInput
                        label="Trusted endpoint (optional)"
                        tooltip={mappingHelp.endpoint}
                        value={mappingForm.trusted_endpoint}
                        onChange={e =>
                            setMappingForm({ ...mappingForm, trusted_endpoint: e.target.value })
                        }
                    />
                </div>

                <div className="space-y-5 rounded-md border bg-muted/20 p-4">
                    <div>
                        <div className="flex items-center gap-2">
                            <h4 className="text-sm font-semibold">
                                Disposition and transfer policy
                            </h4>
                            <HelpTooltip
                                ariaLabel="Help for disposition and transfer policy"
                                content="This allowlist is the complete VICIdial control surface exposed to the selected AAVA Agent. Unlisted outcomes and destinations cannot be invented during a call."
                            />
                        </div>
                        <p className="mt-1 text-xs text-muted-foreground">
                            Only options listed here are exposed to the Agent. Every status must
                            already exist in VICIdial and be at most six characters.
                        </p>
                    </div>

                    <div>
                        <div className="mb-2 flex items-center justify-between">
                            <div>
                                <div className="flex items-center gap-2">
                                    <h5 className="text-sm font-medium">Allowed dispositions</h5>
                                    <HelpTooltip
                                        ariaLabel="Help for allowed dispositions"
                                        content={mappingHelp.dispositions}
                                    />
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    Friendly Agent choice → VICIdial status.
                                </p>
                            </div>
                            <button
                                title="Add another allowlisted business disposition"
                                type="button"
                                onClick={() => {
                                    const key = uniqueKey(
                                        mappingForm.dispositions || {},
                                        'disposition'
                                    );
                                    setMappingForm({
                                        ...mappingForm,
                                        dispositions: {
                                            ...(mappingForm.dispositions || {}),
                                            [key]: '',
                                        },
                                    });
                                }}
                                className="inline-flex items-center gap-1 rounded-md border px-2 py-1.5 text-xs hover:bg-muted"
                            >
                                <Plus className="h-3.5 w-3.5" />
                                Add
                            </button>
                        </div>
                        <div className="mb-1 hidden gap-2 sm:grid sm:grid-cols-[1fr_10rem_auto]">
                            <div className="flex items-center gap-1 text-xs text-muted-foreground">
                                Agent choice
                                <HelpTooltip
                                    ariaLabel="Help for disposition name"
                                    content={mappingHelp.dispositionName}
                                />
                            </div>
                            <div className="flex items-center gap-1 text-xs text-muted-foreground">
                                VICIdial status
                                <HelpTooltip
                                    ariaLabel="Help for disposition status"
                                    content={mappingHelp.dispositionStatus}
                                />
                            </div>
                            <span className="sr-only">Actions</span>
                        </div>
                        <div className="space-y-2">
                            {Object.entries(mappingForm.dispositions || {}).map(([key, value]) => (
                                <div key={key} className="grid gap-2 sm:grid-cols-[1fr_10rem_auto]">
                                    <input
                                        title={mappingHelp.dispositionName}
                                        aria-label="Disposition name"
                                        value={key}
                                        onChange={e =>
                                            setMappingForm({
                                                ...mappingForm,
                                                dispositions: renameKey(
                                                    mappingForm.dispositions || {},
                                                    key,
                                                    e.target.value
                                                ),
                                            })
                                        }
                                        className={rowInputClass}
                                        placeholder="sale"
                                    />
                                    <input
                                        title={mappingHelp.dispositionStatus}
                                        aria-label={`${key} VICIdial status`}
                                        value={String(value)}
                                        maxLength={6}
                                        onChange={e =>
                                            setMappingForm({
                                                ...mappingForm,
                                                dispositions: {
                                                    ...(mappingForm.dispositions || {}),
                                                    [key]: e.target.value.toUpperCase(),
                                                },
                                            })
                                        }
                                        className={`${rowInputClass} font-mono`}
                                        placeholder="SALE"
                                    />
                                    <button
                                        title={`Remove the ${key} disposition`}
                                        type="button"
                                        aria-label={`Remove ${key}`}
                                        onClick={() => {
                                            const next = { ...(mappingForm.dispositions || {}) };
                                            delete next[key];
                                            setMappingForm({ ...mappingForm, dispositions: next });
                                        }}
                                        className="rounded-md border p-2 text-destructive hover:bg-muted"
                                    >
                                        <Trash2 className="h-4 w-4" />
                                    </button>
                                </div>
                            ))}
                        </div>
                    </div>

                    <div>
                        <div className="mb-2 flex items-center gap-2">
                            <h5 className="text-sm font-medium">Lifecycle statuses</h5>
                            <HelpTooltip
                                ariaLabel="Help for lifecycle statuses"
                                content={mappingHelp.lifecycle}
                            />
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                            {Object.entries(mappingForm.statuses || {}).map(([key, value]) => (
                                <label key={key} className="block">
                                    <span className="mb-1 flex items-center gap-1 text-xs text-muted-foreground">
                                        {key.replaceAll('_', ' ')}
                                        <HelpTooltip
                                            ariaLabel={`Help for ${key.replaceAll('_', ' ')}`}
                                            content={lifecycleHelp[key] || mappingHelp.lifecycle}
                                        />
                                    </span>
                                    <input
                                        title={lifecycleHelp[key] || mappingHelp.lifecycle}
                                        aria-label={`${key.replaceAll('_', ' ')} status`}
                                        value={String(value)}
                                        maxLength={6}
                                        onChange={e =>
                                            setMappingForm({
                                                ...mappingForm,
                                                statuses: {
                                                    ...(mappingForm.statuses || {}),
                                                    [key]: e.target.value.toUpperCase(),
                                                },
                                            })
                                        }
                                        className={`${rowInputClass} w-full font-mono`}
                                    />
                                </label>
                            ))}
                        </div>
                        <div className="mt-3 grid gap-x-4 sm:grid-cols-2">
                            <FormSelect
                                label="DNC scope"
                                tooltip={mappingHelp.dncScope}
                                value={mappingForm.dnc_scope}
                                onChange={e =>
                                    setMappingForm({ ...mappingForm, dnc_scope: e.target.value })
                                }
                                options={[
                                    { value: 'campaign', label: 'Current campaign only' },
                                    { value: 'system', label: 'System-wide DNC' },
                                ]}
                            />
                            <FormSelect
                                label="Callback ownership"
                                tooltip={mappingHelp.callbackOwnership}
                                value={mappingForm.callback_type}
                                onChange={e =>
                                    setMappingForm({
                                        ...mappingForm,
                                        callback_type: e.target.value,
                                    })
                                }
                                options={[
                                    { value: 'ANYONE', label: 'Any agent' },
                                    { value: 'USERONLY', label: 'This Remote Agent user' },
                                ]}
                            />
                        </div>
                    </div>

                    <div>
                        <div className="mb-2 flex items-center justify-between">
                            <div>
                                <div className="flex items-center gap-2">
                                    <h5 className="text-sm font-medium">
                                        Cold transfer destinations
                                    </h5>
                                    <HelpTooltip
                                        ariaLabel="Help for cold transfer destinations"
                                        content={mappingHelp.destinations}
                                    />
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    Only pre-approved in-groups or extensions are available to the
                                    Agent.
                                </p>
                            </div>
                            <button
                                title="Add another approved cold-transfer destination"
                                type="button"
                                onClick={() => {
                                    const key = uniqueKey(
                                        mappingForm.destinations || {},
                                        'destination'
                                    );
                                    setMappingForm({
                                        ...mappingForm,
                                        destinations: {
                                            ...(mappingForm.destinations || {}),
                                            [key]: { type: 'ingroup', target: '', description: '' },
                                        },
                                    });
                                }}
                                className="inline-flex items-center gap-1 rounded-md border px-2 py-1.5 text-xs hover:bg-muted"
                            >
                                <Plus className="h-3.5 w-3.5" />
                                Add
                            </button>
                        </div>
                        <div className="mb-1 hidden gap-2 px-3 lg:grid lg:grid-cols-[1fr_9rem_1fr_1fr_7rem_auto]">
                            <div className="flex items-center gap-1 text-xs text-muted-foreground">
                                Name
                                <HelpTooltip
                                    ariaLabel="Help for destination name"
                                    content={mappingHelp.destinationName}
                                />
                            </div>
                            <div className="flex items-center gap-1 text-xs text-muted-foreground">
                                Type
                                <HelpTooltip
                                    ariaLabel="Help for destination type"
                                    content={mappingHelp.destinationType}
                                />
                            </div>
                            <div className="flex items-center gap-1 text-xs text-muted-foreground">
                                Target
                                <HelpTooltip
                                    ariaLabel="Help for destination target"
                                    content={mappingHelp.destinationTarget}
                                />
                            </div>
                            <div className="flex items-center gap-1 text-xs text-muted-foreground">
                                Description
                                <HelpTooltip
                                    ariaLabel="Help for destination description"
                                    content={mappingHelp.destinationDescription}
                                />
                            </div>
                            <div className="flex items-center gap-1 text-xs text-muted-foreground">
                                Status
                                <HelpTooltip
                                    ariaLabel="Help for destination status"
                                    content={mappingHelp.destinationStatus}
                                />
                            </div>
                            <span className="sr-only">Actions</span>
                        </div>
                        <div className="space-y-3">
                            {Object.entries(mappingForm.destinations || {}).map(([key, raw]) => {
                                const destination = raw as TransferDestination;
                                return (
                                    <div
                                        key={key}
                                        className="grid gap-2 rounded-md border bg-background/50 p-3 md:grid-cols-2 lg:grid-cols-[1fr_9rem_1fr_1fr_7rem_auto]"
                                    >
                                        <input
                                            title={mappingHelp.destinationName}
                                            aria-label="Destination name"
                                            value={key}
                                            onChange={e =>
                                                setMappingForm({
                                                    ...mappingForm,
                                                    destinations: renameKey(
                                                        mappingForm.destinations || {},
                                                        key,
                                                        e.target.value
                                                    ),
                                                })
                                            }
                                            className={rowInputClass}
                                            placeholder="sales"
                                        />
                                        <select
                                            title={mappingHelp.destinationType}
                                            aria-label={`${key} type`}
                                            value={destination.type || 'ingroup'}
                                            onChange={e =>
                                                setMappingForm({
                                                    ...mappingForm,
                                                    destinations: {
                                                        ...(mappingForm.destinations || {}),
                                                        [key]: {
                                                            ...destination,
                                                            type: e.target.value,
                                                        },
                                                    },
                                                })
                                            }
                                            className={rowInputClass}
                                        >
                                            <option value="ingroup">In-group</option>
                                            <option value="extension">Extension</option>
                                        </select>
                                        <input
                                            title={mappingHelp.destinationTarget}
                                            aria-label={`${key} target`}
                                            value={destination.target || ''}
                                            onChange={e =>
                                                setMappingForm({
                                                    ...mappingForm,
                                                    destinations: {
                                                        ...(mappingForm.destinations || {}),
                                                        [key]: {
                                                            ...destination,
                                                            target: e.target.value,
                                                        },
                                                    },
                                                })
                                            }
                                            className={rowInputClass}
                                            placeholder="SALESLINE"
                                        />
                                        <input
                                            title={mappingHelp.destinationDescription}
                                            aria-label={`${key} description`}
                                            value={destination.description || ''}
                                            onChange={e =>
                                                setMappingForm({
                                                    ...mappingForm,
                                                    destinations: {
                                                        ...(mappingForm.destinations || {}),
                                                        [key]: {
                                                            ...destination,
                                                            description: e.target.value,
                                                        },
                                                    },
                                                })
                                            }
                                            className={rowInputClass}
                                            placeholder="Sales team"
                                        />
                                        <input
                                            title={mappingHelp.destinationStatus}
                                            aria-label={`${key} status`}
                                            value={destination.status || ''}
                                            maxLength={6}
                                            onChange={e =>
                                                setMappingForm({
                                                    ...mappingForm,
                                                    destinations: {
                                                        ...(mappingForm.destinations || {}),
                                                        [key]: {
                                                            ...destination,
                                                            status: e.target.value.toUpperCase(),
                                                        },
                                                    },
                                                })
                                            }
                                            className={`${rowInputClass} font-mono`}
                                            placeholder="Default"
                                        />
                                        <button
                                            title={`Remove the ${key} transfer destination`}
                                            type="button"
                                            aria-label={`Remove ${key}`}
                                            onClick={() => {
                                                const next = {
                                                    ...(mappingForm.destinations || {}),
                                                };
                                                delete next[key];
                                                setMappingForm({
                                                    ...mappingForm,
                                                    destinations: next,
                                                });
                                            }}
                                            className="rounded-md border p-2 text-destructive hover:bg-muted"
                                        >
                                            <Trash2 className="h-4 w-4" />
                                        </button>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                </div>
            </Modal>

            <Modal
                isOpen={Boolean(guidance)}
                onClose={() => setGuidance(null)}
                title="VICIdial + AAVA setup guide"
                size="xl"
                allowFullscreen
            >
                {guidance && (
                    <div className="space-y-5 text-sm">
                        <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-amber-700 dark:text-amber-300">
                            Apply and verify each stage in order. A mapping is not production-ready
                            until a real call in every configured direction completes with two-way
                            audio and the expected final VICIdial status.
                        </div>
                        <div>
                            <div className="mb-2 flex items-center gap-2">
                                <h4 className="font-semibold">VICIdial</h4>
                                <HelpTooltip
                                    ariaLabel="Help for VICIdial setup steps"
                                    content="Create and verify these objects in the VICIdial Admin UI. AAVA generates guidance but never writes production VICIdial tables directly."
                                />
                            </div>
                            <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
                                {guidance.vicidial_steps.map((step: string) => (
                                    <li key={step}>{step}</li>
                                ))}
                            </ol>
                        </div>
                        <div>
                            <div className="mb-2 flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                    <h4 className="font-semibold">AAVA trusted dialplan</h4>
                                    <HelpTooltip
                                        ariaLabel="Help for AAVA trusted dialplan"
                                        content="Install this exact context on the AAVA/FreePBX host. It limits admission to the trusted endpoint and sets ownership, external call ID, mapping ID, and AI_AGENT before Stasis."
                                    />
                                </div>
                                <button
                                    title="Copy the complete generated dialplan to the clipboard"
                                    onClick={async () => {
                                        await copyTextToClipboard(guidance.dialplan);
                                        toast.success('Dialplan copied');
                                    }}
                                    className="inline-flex items-center gap-2 rounded-md border px-2 py-1 text-xs"
                                >
                                    <Clipboard className="h-3.5 w-3.5" />
                                    Copy
                                </button>
                            </div>
                            <pre className="overflow-x-auto rounded-md border bg-background p-4 text-xs text-foreground">
                                {guidance.dialplan}
                            </pre>
                        </div>
                        <div>
                            <div className="mb-2 flex items-center gap-2">
                                <h4 className="font-semibold">FreePBX / AAVA trunk</h4>
                                <HelpTooltip
                                    ariaLabel="Help for FreePBX trunk"
                                    content="Use these values to create the outbound-authenticated PJSIP registration from AAVA to the dedicated VICIdial Remote Agent Phone."
                                />
                            </div>
                            <div className="grid gap-2 md:grid-cols-2">
                                {Object.entries(guidance.freepbx_trunk).map(([key, value]) => (
                                    <div key={key} className="rounded-md border p-2">
                                        <div className="flex items-center gap-1 text-xs text-muted-foreground">
                                            {key.replaceAll('_', ' ')}
                                            <HelpTooltip
                                                ariaLabel={`Help for trunk ${key.replaceAll('_', ' ')}`}
                                                content={
                                                    trunkHelp[key] ||
                                                    'Generated FreePBX trunk value for this VICIdial mapping.'
                                                }
                                            />
                                        </div>
                                        <div className="break-all font-mono text-xs">
                                            {String(value)}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                        <div>
                            <div className="mb-2 flex items-center gap-2">
                                <h4 className="font-semibold">Network and NAT</h4>
                                <HelpTooltip
                                    ariaLabel="Help for network and NAT"
                                    content="Recommendations are generated from the selected topology. Registration success does not prove correct SDP, RTP, firewall, or symmetric-media behavior."
                                />
                            </div>
                            <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
                                {guidance.network.notes.map((note: string) => (
                                    <li key={note}>{note}</li>
                                ))}
                            </ul>
                        </div>
                        <div>
                            <div className="mb-2 flex items-center gap-2">
                                <h4 className="font-semibold">Verification order</h4>
                                <HelpTooltip
                                    ariaLabel="Help for verification order"
                                    content="Run checks in this sequence so API, SIP, correlation, media, and lifecycle failures remain distinguishable. Do not skip directly from registration to Ready."
                                />
                            </div>
                            <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
                                {guidance.verification_order.map((step: string) => (
                                    <li key={step}>{step}</li>
                                ))}
                            </ol>
                        </div>
                    </div>
                )}
            </Modal>
        </div>
    );
};
