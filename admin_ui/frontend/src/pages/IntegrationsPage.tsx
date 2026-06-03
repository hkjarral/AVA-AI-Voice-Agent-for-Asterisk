import { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import yaml from 'js-yaml';
import { toast } from 'sonner';
import { AlertTriangle, CheckCircle2, Loader2, Plus, Save, Trash2 } from 'lucide-react';
import { ConfigSection } from '../components/ui/ConfigSection';
import { FormInput, FormLabel, FormSelect, FormSwitch } from '../components/ui/FormComponents';
import { YamlErrorBanner, YamlErrorInfo } from '../components/ui/YamlErrorBanner';
import { useAuth } from '../auth/AuthContext';
import { usePendingChanges } from '../hooks/usePendingChanges';
import { sanitizeConfigForSave } from '../utils/configSanitizers';

const DEFAULT_VICIDIAL = {
    enabled: false,
    deployment_mode: 'remote_aava_asterisk',
    api_url: '',
    source: 'aava',
    user: '${VICIDIAL_API_USER}',
    pass: '${VICIDIAL_API_PASS}',
    timeout_ms: 5000,
    verify_ssl: true,
    fallback_to_ari_on_hangup_failure: false,
    default_agent_user: '',
    status_codes: {
        ai_hangup: 'AIHU',
        ai_ingroup_transfer: 'AIXFR',
        ai_extension_transfer: 'AIEXT',
    },
    default_live_agent_destination: 'default_ingroup',
    destinations: {
        default_ingroup: {
            type: 'ingroup',
            ingroup_choices: 'DEFAULTINGROUP',
            description: 'Default ViciDial ingroup',
        },
    },
};

const REMOTE_AAVA_DIALPLAN_SNIPPET = `; AAVA Asterisk server receiving cross-connect call from ViciDial
same => n,Set(__AI_CONTEXT=sales)
same => n,Set(__VICIDIAL_RA_CALL_ID=\${PJSIP_HEADER(read,X-VICIDIAL-CALL-ID)})
same => n,Set(__VICIDIAL_RA_AGENT_USER=\${PJSIP_HEADER(read,X-VICIDIAL-AGENT-USER)})
same => n,Set(__VICIDIAL_LEAD_ID=\${PJSIP_HEADER(read,X-VICIDIAL-LEAD-ID)})
same => n,Set(__VICIDIAL_CAMPAIGN_ID=\${PJSIP_HEADER(read,X-VICIDIAL-CAMPAIGN-ID)})
same => n,Set(__VICIDIAL_CALLER_NAME=\${PJSIP_HEADER(read,X-VICIDIAL-CALLER-NAME)})
same => n,Stasis(asterisk-ai-voice-agent)`;

const SAME_BOX_DIALPLAN_SNIPPET = `; ViciDial Asterisk server running AAVA Stasis locally
same => n,Set(__AI_CONTEXT=sales)
same => n,Set(__VICIDIAL_RA_CALL_ID=\${CALLERID(name)})
same => n,Set(__VICIDIAL_RA_AGENT_USER=1028)
same => n,Stasis(asterisk-ai-voice-agent)

exten => h,1,AGI(agi://127.0.0.1:4577/call_log--HVcauses--PRI-----NODEBUG-----\${HANGUPCAUSE}-----\${DIALSTATUS}-----\${DIALEDTIME}-----\${ANSWEREDTIME}-----\${HANGUPCAUSE(\${HANGUPCAUSE_KEYS()},tech)})`;

type DestinationDraft = {
    key: string;
    type: string;
    ingroup_choices?: string;
    phone_number?: string;
    description?: string;
};

const IntegrationsPage = () => {
    const { token } = useAuth();
    const { setPendingChanges } = usePendingChanges();
    const [config, setConfig] = useState<any>({});
    const configRef = useRef<any>({});
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [testing, setTesting] = useState(false);
    const [yamlError, setYamlError] = useState<YamlErrorInfo | null>(null);
    const [editingDestination, setEditingDestination] = useState<string | null>(null);
    const [destinationDraft, setDestinationDraft] = useState<DestinationDraft>({ key: '', type: 'ingroup' });

    const vicidial = useMemo(() => {
        return {
            ...DEFAULT_VICIDIAL,
            ...(config?.integrations?.vicidial || {}),
            status_codes: {
                ...DEFAULT_VICIDIAL.status_codes,
                ...(config?.integrations?.vicidial?.status_codes || {}),
            },
            destinations: {
                ...(config?.integrations?.vicidial?.destinations || DEFAULT_VICIDIAL.destinations),
            },
        };
    }, [config]);

    useEffect(() => {
        configRef.current = config;
    }, [config]);

    useEffect(() => {
        fetchConfig();
    }, []);

    const fetchConfig = async () => {
        try {
            const res = await axios.get('/api/config/yaml');
            if (res.data.yaml_error) {
                setYamlError(res.data.yaml_error);
                setConfig({});
            } else {
                setConfig((yaml.load(res.data.content) as any) || {});
                setYamlError(null);
            }
        } catch (err: any) {
            toast.error('Failed to load configuration', { description: err?.message });
        } finally {
            setLoading(false);
        }
    };

    const updateVicidial = (path: string, value: any) => {
        setConfig((prev: any) => {
            const current = {
                ...DEFAULT_VICIDIAL,
                ...(prev?.integrations?.vicidial || {}),
                status_codes: {
                    ...DEFAULT_VICIDIAL.status_codes,
                    ...(prev?.integrations?.vicidial?.status_codes || {}),
                },
                destinations: {
                    ...(prev?.integrations?.vicidial?.destinations || DEFAULT_VICIDIAL.destinations),
                },
            };
            const next = { ...current };
            if (path.startsWith('status_codes.')) {
                const key = path.split('.')[1];
                next.status_codes = { ...current.status_codes, [key]: value };
            } else {
                (next as any)[path] = value;
            }
            return {
                ...prev,
                integrations: {
                    ...(prev?.integrations || {}),
                    vicidial: next,
                },
            };
        });
    };

    const save = async () => {
        setSaving(true);
        try {
            const sanitized = sanitizeConfigForSave(configRef.current);
            await axios.post('/api/config/yaml', { content: yaml.dump(sanitized) }, {
                headers: { Authorization: `Bearer ${token}` },
                timeout: 30000,
            });
            setPendingChanges('restart');
            toast.success('Integrations configuration saved');
        } catch (err: any) {
            toast.error('Failed to save configuration', { description: err?.response?.data?.detail || err?.message });
        } finally {
            setSaving(false);
        }
    };

    const testConnection = async () => {
        setTesting(true);
        try {
            const res = await axios.post('/api/integrations/vicidial/test', { config: vicidial }, {
                headers: { Authorization: `Bearer ${token}` },
                timeout: 10000,
            });
            toast.success('ViciDial API reachable', { description: res.data?.message || 'Credentials and source were accepted.' });
        } catch (err: any) {
            toast.error('ViciDial test failed', { description: err?.response?.data?.detail || err?.message });
        } finally {
            setTesting(false);
        }
    };

    const startAddDestination = () => {
        setEditingDestination('new');
        setDestinationDraft({ key: '', type: 'ingroup', ingroup_choices: '', phone_number: '', description: '' });
    };

    const startEditDestination = (key: string, dest: any) => {
        setEditingDestination(key);
        setDestinationDraft({ key, type: dest?.type || 'ingroup', ...dest });
    };

    const saveDestination = () => {
        const key = destinationDraft.key.trim();
        if (!key) {
            toast.error('Destination key is required');
            return;
        }
        const destinations = { ...(vicidial.destinations || {}) };
        if (editingDestination && editingDestination !== 'new' && editingDestination !== key) {
            delete destinations[editingDestination];
        }
        const dest: any = {
            type: destinationDraft.type,
            description: destinationDraft.description || '',
        };
        if (destinationDraft.type === 'ingroup') {
            dest.ingroup_choices = destinationDraft.ingroup_choices || '';
        } else {
            dest.phone_number = destinationDraft.phone_number || '';
        }
        updateVicidial('destinations', { ...destinations, [key]: dest });
        setEditingDestination(null);
    };

    const deleteDestination = (key: string) => {
        const destinations = { ...(vicidial.destinations || {}) };
        delete destinations[key];
        updateVicidial('destinations', destinations);
        if (vicidial.default_live_agent_destination === key) {
            updateVicidial('default_live_agent_destination', '');
        }
    };

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading integrations...</div>;
    if (yamlError) {
        return (
            <div className="space-y-4 p-6">
                <YamlErrorBanner error={yamlError} />
                <div className="rounded-md border border-red-500/30 bg-red-500/10 p-4 text-red-700 dark:text-red-400">
                    Integrations editing is disabled while the YAML config has errors.
                </div>
            </div>
        );
    }

    const destinationOptions = [
        { value: '', label: 'None' },
        ...Object.keys(vicidial.destinations || {}).sort().map((key) => ({ value: key, label: key })),
    ];
    const isSameBox = vicidial.deployment_mode === 'same_box';
    const dialplanSnippet = isSameBox ? SAME_BOX_DIALPLAN_SNIPPET : REMOTE_AAVA_DIALPLAN_SNIPPET;

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Integrations</h1>
                    <p className="text-muted-foreground">External platforms AAVA connects to at runtime.</p>
                </div>
                <button
                    onClick={save}
                    disabled={saving}
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                >
                    {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                    Save
                </button>
            </div>

            <ConfigSection title="ViciDial Remote Agent" description="Receive ViciDial Remote Agent calls and control them through Agent API ra_call_control.">
                <div className="rounded-lg border border-border p-4 bg-card/50">
                    <FormSwitch
                        label="Enable ViciDial Remote Agent"
                        description="Use explicit ViciDial channel variables to route hangup and transfer actions through ViciDial."
                        checked={Boolean(vicidial.enabled)}
                        onChange={(e) => updateVicidial('enabled', e.target.checked)}
                    />

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <FormSelect
                            label="Deployment Mode"
                            value={vicidial.deployment_mode || 'remote_aava_asterisk'}
                            onChange={(e) => updateVicidial('deployment_mode', e.target.value)}
                            options={[
                                { value: 'remote_aava_asterisk', label: 'Remote AAVA Asterisk (recommended)' },
                                { value: 'same_box', label: 'Same ViciDial Asterisk box' },
                            ]}
                        />
                        <FormInput label="Agent API URL" value={vicidial.api_url || ''} onChange={(e) => updateVicidial('api_url', e.target.value)} placeholder="https://vicidial.example.com/agc/api.php" />
                        <FormInput label="Source" tooltip="Must be allowed for the API user in ViciDial Admin -> API Users." value={vicidial.source || 'aava'} onChange={(e) => updateVicidial('source', e.target.value)} />
                        <FormInput label="API User Env Reference" value={vicidial.user || '${VICIDIAL_API_USER}'} onChange={(e) => updateVicidial('user', e.target.value)} />
                        <FormInput label="API Password Env Reference" value={vicidial.pass || '${VICIDIAL_API_PASS}'} onChange={(e) => updateVicidial('pass', e.target.value)} />
                        <FormInput label="Timeout (ms)" type="number" value={vicidial.timeout_ms || 5000} onChange={(e) => updateVicidial('timeout_ms', Number(e.target.value || 5000))} />
                        <FormInput label="Lab Default Agent User" tooltip="Production dialplan should set VICIDIAL_RA_AGENT_USER per call. This fallback warns on every call." value={vicidial.default_agent_user || ''} onChange={(e) => updateVicidial('default_agent_user', e.target.value)} />
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <FormSwitch label="Verify SSL" checked={vicidial.verify_ssl !== false} onChange={(e) => updateVicidial('verify_ssl', e.target.checked)} />
                        <FormSwitch
                            label="ARI Fallback For Hangup"
                            description="Emergency escape hatch only. Transfer failures never fall back to ARI."
                            checked={Boolean(vicidial.fallback_to_ari_on_hangup_failure)}
                            onChange={(e) => updateVicidial('fallback_to_ari_on_hangup_failure', e.target.checked)}
                        />
                    </div>

                    <button
                        onClick={testConnection}
                        disabled={testing}
                        className="inline-flex items-center gap-2 px-3 py-2 rounded-md bg-secondary hover:bg-secondary/80 disabled:opacity-50"
                    >
                        {testing ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
                        Test Connection
                    </button>
                </div>
            </ConfigSection>

            <ConfigSection title="Destinations" description="Destinations used by live_agent_transfer and blind_transfer during ViciDial sessions.">
                <div className="rounded-lg border border-border p-4 bg-card/50 space-y-4">
                    <FormSelect
                        label="Default Live Agent Destination"
                        value={vicidial.default_live_agent_destination || ''}
                        onChange={(e) => updateVicidial('default_live_agent_destination', e.target.value)}
                        options={destinationOptions}
                    />
                    <div className="flex justify-between items-center">
                        <FormLabel>Configured Destinations</FormLabel>
                        <button onClick={startAddDestination} className="text-xs inline-flex items-center gap-1 bg-secondary px-2 py-1 rounded hover:bg-secondary/80">
                            <Plus className="w-3 h-3" /> Add Destination
                        </button>
                    </div>
                    <div className="grid grid-cols-1 gap-2">
                        {Object.entries(vicidial.destinations || {}).map(([key, dest]: [string, any]) => (
                            <div key={key} className="flex items-center justify-between p-3 bg-accent/30 rounded border border-border/50">
                                <div>
                                    <div className="font-medium text-sm">{key}</div>
                                    <div className="text-xs text-muted-foreground">
                                        {dest?.type} • {dest?.type === 'ingroup' ? dest?.ingroup_choices : dest?.phone_number} • {dest?.description || ''}
                                    </div>
                                </div>
                                <div className="flex gap-2">
                                    <button onClick={() => startEditDestination(key, dest)} className="text-xs px-2 py-1 rounded bg-secondary hover:bg-secondary/80">Edit</button>
                                    <button onClick={() => deleteDestination(key)} className="p-1.5 hover:bg-destructive/10 rounded text-destructive"><Trash2 className="w-4 h-4" /></button>
                                </div>
                            </div>
                        ))}
                    </div>

                    {editingDestination && (
                        <div className="rounded-lg border border-border p-4 bg-background/60">
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <FormInput label="Destination Key" value={destinationDraft.key} onChange={(e) => setDestinationDraft({ ...destinationDraft, key: e.target.value })} />
                                <FormSelect label="Type" value={destinationDraft.type} onChange={(e) => setDestinationDraft({ ...destinationDraft, type: e.target.value })} options={[{ value: 'ingroup', label: 'Ingroup' }, { value: 'extension', label: 'Extension' }]} />
                                {destinationDraft.type === 'ingroup' ? (
                                    <FormInput label="Ingroup Choices" value={destinationDraft.ingroup_choices || ''} onChange={(e) => setDestinationDraft({ ...destinationDraft, ingroup_choices: e.target.value })} placeholder="DEFAULTINGROUP" />
                                ) : (
                                    <FormInput label="Phone Number" value={destinationDraft.phone_number || ''} onChange={(e) => setDestinationDraft({ ...destinationDraft, phone_number: e.target.value })} />
                                )}
                                <FormInput label="Description" value={destinationDraft.description || ''} onChange={(e) => setDestinationDraft({ ...destinationDraft, description: e.target.value })} />
                            </div>
                            <div className="flex gap-2">
                                <button onClick={saveDestination} className="px-3 py-2 rounded bg-primary text-primary-foreground hover:bg-primary/90">Save Destination</button>
                                <button onClick={() => setEditingDestination(null)} className="px-3 py-2 rounded bg-secondary hover:bg-secondary/80">Cancel</button>
                            </div>
                        </div>
                    )}
                </div>
            </ConfigSection>

            <ConfigSection title="Status Codes" description="ViciDial statuses are limited to 6 characters.">
                <div className="rounded-lg border border-border p-4 bg-card/50 grid grid-cols-1 md:grid-cols-3 gap-4">
                    <FormInput label="AI Hangup" maxLength={6} value={vicidial.status_codes.ai_hangup || 'AIHU'} onChange={(e) => updateVicidial('status_codes.ai_hangup', e.target.value)} />
                    <FormInput label="AI Ingroup Transfer" maxLength={6} value={vicidial.status_codes.ai_ingroup_transfer || 'AIXFR'} onChange={(e) => updateVicidial('status_codes.ai_ingroup_transfer', e.target.value)} />
                    <FormInput label="AI Extension Transfer" maxLength={6} value={vicidial.status_codes.ai_extension_transfer || 'AIEXT'} onChange={(e) => updateVicidial('status_codes.ai_extension_transfer', e.target.value)} />
                </div>
            </ConfigSection>

            <ConfigSection title="Dialplan Requirements">
                <div className="rounded-lg border border-amber-300/40 bg-amber-500/5 p-4">
                    <div className="flex gap-2 text-amber-700 dark:text-amber-400 text-sm">
                        <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
                        <p>
                            {isSameBox
                                ? 'Same-box mode runs AAVA Stasis on the ViciDial Asterisk process. Keep ViciDial hangup logging in every ViciDial context.'
                                : 'Remote mode is recommended for production and high-volume testing. Map cross-connect headers or IAX vars into AAVA channel vars on the AAVA Asterisk server.'}
                        </p>
                    </div>
                    <pre className="mt-4 overflow-x-auto rounded bg-muted p-3 text-xs"><code>{dialplanSnippet}</code></pre>
                    <p className="mt-3 text-xs text-muted-foreground">
                        {isSameBox
                            ? 'Same-box mode should keep the full ViciDial h extension in every ViciDial context that participates in this call path.'
                            : 'Header names shown for remote mode are placeholders until your ViciDial-side forwarding script defines the exact names. The AAVA requirement is the final channel vars: VICIDIAL_RA_CALL_ID and VICIDIAL_RA_AGENT_USER.'}
                    </p>
                </div>
            </ConfigSection>
        </div>
    );
};

export default IntegrationsPage;
