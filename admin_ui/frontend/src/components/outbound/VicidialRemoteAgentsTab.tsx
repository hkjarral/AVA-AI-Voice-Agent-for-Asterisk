import { useEffect, useMemo, useState } from 'react';
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
    last_verification?: any;
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
    destinations?: Record<string, any>;
    dnc_scope: string;
    callback_type: string;
    agent_available?: boolean;
    connection?: Connection;
    last_verification?: any;
};

type Agent = { slug: string; display_name: string };

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
    destinations: {} as Record<string, { type: string; target: string; description: string; status?: string }>,
    dnc_scope: 'campaign',
    callback_type: 'ANYONE',
};

const statusLabel = (mapping: Mapping) => {
    const check = mapping.last_verification;
    if (!mapping.enabled) return { label: 'Disabled', tone: 'text-muted-foreground', ok: false };
    if (!mapping.agent_available) return { label: 'Agent unavailable', tone: 'text-destructive', ok: false };
    if (!check) return { label: 'Not verified', tone: 'text-amber-500', ok: false };
    if (!check.configuration_ready) return { label: 'Needs attention', tone: 'text-destructive', ok: false };
    if (!check.real_call?.verified) {
        const required = (check.real_call?.required_directions || []).join(' + ');
        return { label: `Configuration valid — ${required || 'real'} call test required`, tone: 'text-amber-500', ok: false };
    }
    return { label: 'Ready', tone: 'text-emerald-500', ok: true };
};

const rowInputClass = 'min-w-0 rounded-md border border-input bg-background px-2.5 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';

const uniqueKey = (record: Record<string, unknown>, prefix: string) => {
    let index = 1;
    while (record[`${prefix}_${index}`] !== undefined) index += 1;
    return `${prefix}_${index}`;
};

const renameKey = <T,>(record: Record<string, T>, oldKey: string, newKey: string): Record<string, T> => {
    const clean = newKey.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '_');
    if (!clean || (clean !== oldKey && record[clean] !== undefined)) return record;
    return Object.fromEntries(Object.entries(record).map(([key, value]) => [key === oldKey ? clean : key, value]));
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
    const [connectionForm, setConnectionForm] = useState<any>(emptyConnection);
    const [mappingForm, setMappingForm] = useState<any>(emptyMapping);
    const [guidance, setGuidance] = useState<any | null>(null);

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
            setAgents((metaResponse.data?.agents || []).filter((agent: any) => agent?.slug));
        } catch (error) {
            toast.error(describeApiError(error, 'Unable to load VICIdial setup'));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { void refresh(); }, []);

    const connectionOptions = useMemo(() => connections.map(item => ({ value: item.id, label: item.name })), [connections]);
    const agentOptions = useMemo(() => agents.map(item => ({ value: item.slug, label: `${item.display_name} (${item.slug})` })), [agents]);

    const openConnection = (connection?: Connection) => {
        setEditingConnection(connection?.id || null);
        setConnectionForm(connection ? { ...emptyConnection, ...connection } : { ...emptyConnection });
        setConnectionModal(true);
    };

    const openMapping = (mapping?: Mapping) => {
        setEditingMapping(mapping?.id || null);
        setMappingForm(mapping ? {
            ...emptyMapping,
            ...mapping,
            closer_campaigns: (mapping.closer_campaigns || []).join(', '),
            dispositions: { ...(mapping.dispositions || {}) },
            statuses: { ...emptyMapping.statuses, ...(mapping.statuses || {}) },
            destinations: { ...(mapping.destinations || {}) },
        } : {
            ...emptyMapping,
            connection_id: connections[0]?.id || '',
            ai_agent: agents.find(agent => agent.slug === 'demo_deepgram')?.slug || agents[0]?.slug || '',
        });
        setMappingModal(true);
    };

    const saveConnection = async () => {
        setBusy('save-connection');
        try {
            const payload = { ...connectionForm, timeout_ms: Number(connectionForm.timeout_ms), sip_port: Number(connectionForm.sip_port), rtp_start: Number(connectionForm.rtp_start), rtp_end: Number(connectionForm.rtp_end) };
            if (editingConnection) await axios.put(`/api/outbound/vicidial/connections/${editingConnection}`, payload);
            else await axios.post('/api/outbound/vicidial/connections', payload);
            toast.success('VICIdial connection saved');
            setConnectionModal(false);
            await refresh();
        } catch (error) {
            toast.error(describeApiError(error, 'Unable to save connection'));
        } finally { setBusy(''); }
    };

    const saveMapping = async () => {
        setBusy('save-mapping');
        try {
            const payload = {
                ...mappingForm,
                number_of_lines: Number(mappingForm.number_of_lines),
                closer_campaigns: String(mappingForm.closer_campaigns || '').split(',').map((v: string) => v.trim()).filter(Boolean),
                dispositions: { ...(mappingForm.dispositions || {}) },
                statuses: { ...(mappingForm.statuses || {}) },
                destinations: { ...(mappingForm.destinations || {}) },
            };
            if (editingMapping) await axios.put(`/api/outbound/vicidial/mappings/${editingMapping}`, payload);
            else await axios.post('/api/outbound/vicidial/mappings', payload);
            toast.success('Remote Agent mapping saved');
            setMappingModal(false);
            await refresh();
        } catch (error) {
            toast.error(describeApiError(error, 'Unable to save mapping'));
        } finally { setBusy(''); }
    };

    const verify = async (kind: 'connections' | 'mappings', id: string) => {
        setBusy(`verify-${id}`);
        try {
            const response = await axios.post(`/api/outbound/vicidial/${kind}/${id}/verify`);
            if (response.data?.ready) toast.success('API connection verified');
            else if (kind === 'mappings' && response.data?.configuration_ready) toast.success('Configuration checks passed; complete a real call test');
            else toast.warning('Verification found items that need attention');
            await refresh();
        } catch (error) {
            toast.error(describeApiError(error, 'Verification failed'));
        } finally { setBusy(''); }
    };

    const remove = async (kind: 'connections' | 'mappings', id: string) => {
        if (!window.confirm(`Delete this ${kind === 'connections' ? 'connection and its mappings' : 'mapping'}?`)) return;
        try {
            await axios.delete(`/api/outbound/vicidial/${kind}/${id}`);
            toast.success('Deleted');
            await refresh();
        } catch (error) { toast.error(describeApiError(error, 'Delete failed')); }
    };

    const openGuidance = async (id: string) => {
        setBusy(`guidance-${id}`);
        try {
            const response = await axios.get(`/api/outbound/vicidial/mappings/${id}/guidance`);
            setGuidance(response.data);
        } catch (error) { toast.error(describeApiError(error, 'Unable to generate setup guidance')); }
        finally { setBusy(''); }
    };

    if (loading) return <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading VICIdial integration…</div>;

    return (
        <div className="space-y-6">
            <div className="rounded-lg border border-border bg-card p-4">
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                        <h2 className="text-lg font-semibold">VICIdial Remote Agents</h2>
                        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">Connect an existing VICIdial campaign to an AAVA Agent. VICIdial keeps ownership of the customer call, campaign state, transfers, callbacks, DNC, and final disposition.</p>
                    </div>
                    <button onClick={() => void refresh()} className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-muted"><RefreshCw className="h-4 w-4" />Refresh</button>
                </div>
                <div className="mt-4 grid gap-3 md:grid-cols-4">
                    {['1. Connect APIs', '2. Map Remote Agent', '3. Apply PBX setup', '4. Verify real calls'].map((label, index) => (
                        <div key={label} className={`rounded-md border p-3 text-sm ${index === 0 && connections.length ? 'border-emerald-500/40 bg-emerald-500/5' : 'border-border'}`}>{label}</div>
                    ))}
                </div>
            </div>

            <section className="space-y-3">
                <div className="flex items-center justify-between"><div><h3 className="font-semibold">1. API connections</h3><p className="text-xs text-muted-foreground">Credentials remain environment-variable references and are never stored here.</p></div><button onClick={() => openConnection()} className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground"><Plus className="h-4 w-4" />Add connection</button></div>
                {connections.length === 0 ? <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">Add the VICIdial server before creating a Remote Agent mapping.</div> : connections.map(connection => (
                    <div key={connection.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-4">
                        <div><div className="flex items-center gap-2 font-medium"><ServerCog className="h-4 w-4" />{connection.name}{connection.last_verification?.ready && <CheckCircle2 className="h-4 w-4 text-emerald-500" />}</div><div className="mt-1 font-mono text-xs text-muted-foreground">{connection.base_url} · {connection.topology}</div></div>
                        <div className="flex gap-2"><button onClick={() => void verify('connections', connection.id)} disabled={busy === `verify-${connection.id}`} className="rounded-md border px-3 py-2 text-sm hover:bg-muted">Verify API</button><button aria-label="Edit connection" onClick={() => openConnection(connection)} className="rounded-md border p-2 hover:bg-muted"><Pencil className="h-4 w-4" /></button><button aria-label="Delete connection" onClick={() => void remove('connections', connection.id)} className="rounded-md border p-2 text-destructive hover:bg-muted"><Trash2 className="h-4 w-4" /></button></div>
                    </div>
                ))}
            </section>

            <section className="space-y-3">
                <div className="flex items-center justify-between"><div><h3 className="font-semibold">2. Remote Agent mappings</h3><p className="text-xs text-muted-foreground">Each mapping binds a VICIdial user range and extension to one active AAVA Agent.</p></div><button disabled={!connections.length} onClick={() => openMapping()} className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50"><Plus className="h-4 w-4" />Add mapping</button></div>
                {mappings.length === 0 ? <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">No Remote Agent mappings yet.</div> : mappings.map(mapping => {
                    const status = statusLabel(mapping);
                    return <div key={mapping.id} className="rounded-lg border bg-card p-4">
                        <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="font-medium">{mapping.name}</div><div className="mt-1 text-xs text-muted-foreground">Users {mapping.user_start}{mapping.number_of_lines > 1 ? `–${Number(mapping.user_start) + mapping.number_of_lines - 1}` : ''} · Extension {mapping.conf_exten} · {mapping.direction} → <span className="font-mono text-foreground">{mapping.ai_agent}</span></div><div className={`mt-2 flex items-center gap-1.5 text-xs ${status.tone}`}>{status.ok ? <CheckCircle2 className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}{status.label}</div></div>
                        <div className="flex flex-wrap gap-2"><button onClick={() => void verify('mappings', mapping.id)} className="rounded-md border px-3 py-2 text-sm hover:bg-muted">Run checks</button><button onClick={() => void openGuidance(mapping.id)} className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-muted"><Link2 className="h-4 w-4" />Setup guide</button><button aria-label="Edit mapping" onClick={() => openMapping(mapping)} className="rounded-md border p-2 hover:bg-muted"><Pencil className="h-4 w-4" /></button><button aria-label="Delete mapping" onClick={() => void remove('mappings', mapping.id)} className="rounded-md border p-2 text-destructive hover:bg-muted"><Trash2 className="h-4 w-4" /></button></div></div>
                    </div>;
                })}
            </section>

            <Modal isOpen={connectionModal} onClose={() => setConnectionModal(false)} title={editingConnection ? 'Edit VICIdial connection' : 'Add VICIdial connection'} size="lg" footer={<><button onClick={() => setConnectionModal(false)} className="rounded-md border px-4 py-2 text-sm">Cancel</button><button onClick={() => void saveConnection()} disabled={busy === 'save-connection'} className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground">Save connection</button></>}>
                <FormSwitch label="Connection enabled" checked={connectionForm.enabled} onChange={e => setConnectionForm({ ...connectionForm, enabled: e.target.checked })} description="Disabled connections never admit or control VICIdial calls." />
                <div className="grid gap-x-4 md:grid-cols-2"><FormInput label="Connection name" value={connectionForm.name} onChange={e => setConnectionForm({ ...connectionForm, name: e.target.value })} /><FormInput label="VICIdial base URL" value={connectionForm.base_url} onChange={e => setConnectionForm({ ...connectionForm, base_url: e.target.value })} placeholder="http://192.168.10.100" /><FormInput label="VICIdial SIP host" value={connectionForm.vicidial_host || ''} onChange={e => setConnectionForm({ ...connectionForm, vicidial_host: e.target.value })} /><FormInput label="SIP port" type="number" value={connectionForm.sip_port} onChange={e => setConnectionForm({ ...connectionForm, sip_port: e.target.value })} /><FormSelect label="Network topology" value={connectionForm.topology} onChange={e => setConnectionForm({ ...connectionForm, topology: e.target.value })} options={[{ value: 'lan_vpn', label: 'LAN / VPN' }, { value: 'ava_behind_nat', label: 'AAVA behind NAT' }, { value: 'public_sbc', label: 'Public / SBC' }]} /><FormInput label="VICIdial timezone" value={connectionForm.timezone || 'UTC'} onChange={e => setConnectionForm({ ...connectionForm, timezone: e.target.value })} placeholder="America/Phoenix" /><FormInput label="RTP start port" type="number" value={connectionForm.rtp_start} onChange={e => setConnectionForm({ ...connectionForm, rtp_start: e.target.value })} /><FormInput label="RTP end port" type="number" value={connectionForm.rtp_end} onChange={e => setConnectionForm({ ...connectionForm, rtp_end: e.target.value })} /><FormInput label="API username environment variable" value={connectionForm.username_env} onChange={e => setConnectionForm({ ...connectionForm, username_env: e.target.value })} /><FormInput label="API password environment variable" value={connectionForm.password_env} onChange={e => setConnectionForm({ ...connectionForm, password_env: e.target.value })} /><FormInput label="Source label" value={connectionForm.source} onChange={e => setConnectionForm({ ...connectionForm, source: e.target.value })} /><FormInput label="Timeout (ms)" type="number" value={connectionForm.timeout_ms} onChange={e => setConnectionForm({ ...connectionForm, timeout_ms: e.target.value })} /></div><FormSwitch label="Verify TLS certificates" checked={connectionForm.verify_ssl} onChange={e => setConnectionForm({ ...connectionForm, verify_ssl: e.target.checked })} description="Keep enabled for HTTPS with a trusted certificate; HTTP lab servers do not use TLS." />
            </Modal>

            <Modal isOpen={mappingModal} onClose={() => setMappingModal(false)} title={editingMapping ? 'Edit Remote Agent mapping' : 'Add Remote Agent mapping'} size="xl" allowFullscreen footer={<><button onClick={() => setMappingModal(false)} className="rounded-md border px-4 py-2 text-sm">Cancel</button><button onClick={() => void saveMapping()} disabled={busy === 'save-mapping'} className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground">Save mapping</button></>}>
                <FormSwitch label="Mapping enabled" checked={mappingForm.enabled} onChange={e => setMappingForm({ ...mappingForm, enabled: e.target.checked })} description="Disabled mappings reject new Remote Agent calls and expose no VICIdial tools." />
                <div className="grid gap-x-4 md:grid-cols-2"><FormSelect label="VICIdial connection" value={mappingForm.connection_id} onChange={e => setMappingForm({ ...mappingForm, connection_id: e.target.value })} options={connectionOptions} /><FormInput label="Mapping name" value={mappingForm.name} onChange={e => setMappingForm({ ...mappingForm, name: e.target.value })} /><FormSelect label="Call direction" value={mappingForm.direction} onChange={e => setMappingForm({ ...mappingForm, direction: e.target.value })} options={[{ value: 'both', label: 'Inbound and outbound' }, { value: 'outbound', label: 'Outbound only' }, { value: 'inbound', label: 'Inbound / closer only' }]} /><FormSelect label="AAVA Agent" value={mappingForm.ai_agent} onChange={e => setMappingForm({ ...mappingForm, ai_agent: e.target.value })} options={agentOptions} /><FormInput label="VICIdial campaign ID" value={mappingForm.campaign_id} onChange={e => setMappingForm({ ...mappingForm, campaign_id: e.target.value })} /><FormInput label="Closer campaigns" value={mappingForm.closer_campaigns} onChange={e => setMappingForm({ ...mappingForm, closer_campaigns: e.target.value })} placeholder="SALESLINE, SUPPORT" /><FormInput label="Starting Remote Agent user" value={mappingForm.user_start} onChange={e => setMappingForm({ ...mappingForm, user_start: e.target.value })} /><FormInput label="Number of lines" type="number" min={1} max={100} value={mappingForm.number_of_lines} onChange={e => setMappingForm({ ...mappingForm, number_of_lines: e.target.value })} /><FormInput label="Remote Agent extension" value={mappingForm.conf_exten} onChange={e => setMappingForm({ ...mappingForm, conf_exten: e.target.value })} /><FormInput label="One-line fallback user" value={mappingForm.static_agent_user} onChange={e => setMappingForm({ ...mappingForm, static_agent_user: e.target.value })} /><FormInput label="Trusted AAVA dialplan context" value={mappingForm.trusted_context} onChange={e => setMappingForm({ ...mappingForm, trusted_context: e.target.value })} /><FormInput label="Trusted endpoint (optional)" value={mappingForm.trusted_endpoint} onChange={e => setMappingForm({ ...mappingForm, trusted_endpoint: e.target.value })} /></div>

                <div className="space-y-5 rounded-md border bg-muted/20 p-4">
                    <div><h4 className="text-sm font-semibold">Disposition and transfer policy</h4><p className="mt-1 text-xs text-muted-foreground">Only options listed here are exposed to the Agent. Every status must already exist in VICIdial and be at most six characters.</p></div>

                    <div>
                        <div className="mb-2 flex items-center justify-between"><div><h5 className="text-sm font-medium">Allowed dispositions</h5><p className="text-xs text-muted-foreground">Friendly Agent choice → VICIdial status.</p></div><button type="button" onClick={() => { const key = uniqueKey(mappingForm.dispositions || {}, 'disposition'); setMappingForm({ ...mappingForm, dispositions: { ...(mappingForm.dispositions || {}), [key]: '' } }); }} className="inline-flex items-center gap-1 rounded-md border px-2 py-1.5 text-xs hover:bg-muted"><Plus className="h-3.5 w-3.5" />Add</button></div>
                        <div className="space-y-2">{Object.entries(mappingForm.dispositions || {}).map(([key, value]) => <div key={key} className="grid gap-2 sm:grid-cols-[1fr_10rem_auto]"><input aria-label="Disposition name" value={key} onChange={e => setMappingForm({ ...mappingForm, dispositions: renameKey(mappingForm.dispositions || {}, key, e.target.value) })} className={rowInputClass} placeholder="sale" /><input aria-label={`${key} VICIdial status`} value={String(value)} maxLength={6} onChange={e => setMappingForm({ ...mappingForm, dispositions: { ...(mappingForm.dispositions || {}), [key]: e.target.value.toUpperCase() } })} className={`${rowInputClass} font-mono`} placeholder="SALE" /><button type="button" aria-label={`Remove ${key}`} onClick={() => { const next = { ...(mappingForm.dispositions || {}) }; delete next[key]; setMappingForm({ ...mappingForm, dispositions: next }); }} className="rounded-md border p-2 text-destructive hover:bg-muted"><Trash2 className="h-4 w-4" /></button></div>)}</div>
                    </div>

                    <div>
                        <h5 className="mb-2 text-sm font-medium">Lifecycle statuses</h5>
                        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{Object.entries(mappingForm.statuses || {}).map(([key, value]) => <label key={key} className="block"><span className="mb-1 block text-xs text-muted-foreground">{key.replaceAll('_', ' ')}</span><input value={String(value)} maxLength={6} onChange={e => setMappingForm({ ...mappingForm, statuses: { ...(mappingForm.statuses || {}), [key]: e.target.value.toUpperCase() } })} className={`${rowInputClass} w-full font-mono`} /></label>)}</div>
                        <div className="mt-3 grid gap-x-4 sm:grid-cols-2"><FormSelect label="DNC scope" value={mappingForm.dnc_scope} onChange={e => setMappingForm({ ...mappingForm, dnc_scope: e.target.value })} options={[{ value: 'campaign', label: 'Current campaign only' }, { value: 'system', label: 'System-wide DNC' }]} /><FormSelect label="Callback ownership" value={mappingForm.callback_type} onChange={e => setMappingForm({ ...mappingForm, callback_type: e.target.value })} options={[{ value: 'ANYONE', label: 'Any agent' }, { value: 'USERONLY', label: 'This Remote Agent user' }]} /></div>
                    </div>

                    <div>
                        <div className="mb-2 flex items-center justify-between"><div><h5 className="text-sm font-medium">Cold transfer destinations</h5><p className="text-xs text-muted-foreground">Only pre-approved in-groups or extensions are available to the Agent.</p></div><button type="button" onClick={() => { const key = uniqueKey(mappingForm.destinations || {}, 'destination'); setMappingForm({ ...mappingForm, destinations: { ...(mappingForm.destinations || {}), [key]: { type: 'ingroup', target: '', description: '' } } }); }} className="inline-flex items-center gap-1 rounded-md border px-2 py-1.5 text-xs hover:bg-muted"><Plus className="h-3.5 w-3.5" />Add</button></div>
                        <div className="space-y-3">{Object.entries(mappingForm.destinations || {}).map(([key, raw]) => { const destination = raw as any; return <div key={key} className="grid gap-2 rounded-md border bg-background/50 p-3 md:grid-cols-2 lg:grid-cols-[1fr_9rem_1fr_1fr_7rem_auto]"><input aria-label="Destination name" value={key} onChange={e => setMappingForm({ ...mappingForm, destinations: renameKey(mappingForm.destinations || {}, key, e.target.value) })} className={rowInputClass} placeholder="sales" /><select aria-label={`${key} type`} value={destination.type || 'ingroup'} onChange={e => setMappingForm({ ...mappingForm, destinations: { ...(mappingForm.destinations || {}), [key]: { ...destination, type: e.target.value } } })} className={rowInputClass}><option value="ingroup">In-group</option><option value="extension">Extension</option></select><input aria-label={`${key} target`} value={destination.target || ''} onChange={e => setMappingForm({ ...mappingForm, destinations: { ...(mappingForm.destinations || {}), [key]: { ...destination, target: e.target.value } } })} className={rowInputClass} placeholder="SALESLINE" /><input aria-label={`${key} description`} value={destination.description || ''} onChange={e => setMappingForm({ ...mappingForm, destinations: { ...(mappingForm.destinations || {}), [key]: { ...destination, description: e.target.value } } })} className={rowInputClass} placeholder="Sales team" /><input aria-label={`${key} status`} value={destination.status || ''} maxLength={6} onChange={e => setMappingForm({ ...mappingForm, destinations: { ...(mappingForm.destinations || {}), [key]: { ...destination, status: e.target.value.toUpperCase() } } })} className={`${rowInputClass} font-mono`} placeholder="Default" /><button type="button" aria-label={`Remove ${key}`} onClick={() => { const next = { ...(mappingForm.destinations || {}) }; delete next[key]; setMappingForm({ ...mappingForm, destinations: next }); }} className="rounded-md border p-2 text-destructive hover:bg-muted"><Trash2 className="h-4 w-4" /></button></div>; })}</div>
                    </div>
                </div>
            </Modal>

            <Modal isOpen={Boolean(guidance)} onClose={() => setGuidance(null)} title="VICIdial + AAVA setup guide" size="xl" allowFullscreen>
                {guidance && <div className="space-y-5 text-sm"><div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-amber-700 dark:text-amber-300">Apply and verify each stage in order. A mapping is not production-ready until a real inbound and outbound call completes with two-way audio and the expected final VICIdial status.</div><div><h4 className="mb-2 font-semibold">VICIdial</h4><ol className="list-decimal space-y-1 pl-5 text-muted-foreground">{guidance.vicidial_steps.map((step: string) => <li key={step}>{step}</li>)}</ol></div><div><div className="mb-2 flex items-center justify-between"><h4 className="font-semibold">AAVA trusted dialplan</h4><button onClick={async () => { await copyTextToClipboard(guidance.dialplan); toast.success('Dialplan copied'); }} className="inline-flex items-center gap-2 rounded-md border px-2 py-1 text-xs"><Clipboard className="h-3.5 w-3.5" />Copy</button></div><pre className="overflow-x-auto rounded-md border bg-background p-4 text-xs text-foreground">{guidance.dialplan}</pre></div><div><h4 className="mb-2 font-semibold">FreePBX / AAVA trunk</h4><div className="grid gap-2 md:grid-cols-2">{Object.entries(guidance.freepbx_trunk).map(([key, value]) => <div key={key} className="rounded-md border p-2"><div className="text-xs text-muted-foreground">{key.replaceAll('_', ' ')}</div><div className="break-all font-mono text-xs">{String(value)}</div></div>)}</div></div><div><h4 className="mb-2 font-semibold">Network and NAT</h4><ul className="list-disc space-y-1 pl-5 text-muted-foreground">{guidance.network.notes.map((note: string) => <li key={note}>{note}</li>)}</ul></div><div><h4 className="mb-2 font-semibold">Verification order</h4><ol className="list-decimal space-y-1 pl-5 text-muted-foreground">{guidance.verification_order.map((step: string) => <li key={step}>{step}</li>)}</ol></div></div>}
            </Modal>
        </div>
    );
};
