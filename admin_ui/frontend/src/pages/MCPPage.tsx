import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import yaml from 'js-yaml';
import { Plus, Save, Play, RefreshCw, AlertCircle, Settings2, Trash2 } from 'lucide-react';
import { ConfigSection } from '../components/ui/ConfigSection';
import { ConfigCard } from '../components/ui/ConfigCard';
import { Modal } from '../components/ui/Modal';
import { FormInput, FormLabel } from '../components/ui/FormComponents';
import { sanitizeConfigForSave } from '../utils/configSanitizers';

type MCPStatus = {
    enabled: boolean;
    servers: Record<string, any>;
    tool_routes?: Record<string, any>;
};

type ServerForm = {
    id: string;
    enabled: boolean;
    transport: string;
    commandExec: string;
    commandArgs: string;
    cwd?: string;
    defaults: {
        timeout_ms: number;
        slow_response_threshold_ms: number;
        slow_response_message: string;
    };
    restart: {
        enabled: boolean;
        max_restarts: number;
        backoff_ms: number;
    };
    env: Array<{ key: string; value: string; redacted?: boolean }>;
    tools: Array<{
        name: string;
        expose_as?: string;
        description?: string;
        speech_field?: string;
        speech_template?: string;
        timeout_ms?: number;
        slow_response_threshold_ms?: number;
        slow_response_message?: string;
    }>;
};

const _parseArgLine = (raw: string): string[] => {
    const s = (raw || '').trim();
    if (!s) return [];
    return s.split(/\s+/g).filter(Boolean);
};

const MCPPage = () => {
    const [config, setConfig] = useState<any>({});
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [reloadingEngine, setReloadingEngine] = useState(false);
    const [status, setStatus] = useState<MCPStatus | null>(null);
    const [statusLoading, setStatusLoading] = useState(false);
    const [editing, setEditing] = useState(false);
    const [serverForm, setServerForm] = useState<ServerForm | null>(null);
    const [testRunning, setTestRunning] = useState<Record<string, boolean>>({});

    useEffect(() => {
        fetchAll();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const fetchAll = async () => {
        setLoading(true);
        try {
            const res = await axios.get('/api/config/yaml');
            const parsed = yaml.load(res.data.content) as any;
            setConfig(parsed || {});
        } catch (err) {
            console.error('Failed to load config', err);
        } finally {
            setLoading(false);
        }
        await fetchStatus();
    };

    const fetchStatus = async () => {
        setStatusLoading(true);
        try {
            const res = await axios.get('/api/mcp/status');
            setStatus(res.data);
        } catch (err) {
            console.warn('Failed to load MCP status (ai-engine may be down)', err);
            setStatus(null);
        } finally {
            setStatusLoading(false);
        }
    };

    const mcpConfig = config.mcp || {};
    const servers: Record<string, any> = mcpConfig.servers || {};

    const handleSave = async () => {
        setSaving(true);
        try {
            const sanitized = sanitizeConfigForSave(config);
            await axios.post('/api/config/yaml', { content: yaml.dump(sanitized) });
            try {
                setReloadingEngine(true);
                const res = await axios.post('/api/system/containers/ai_engine/reload');
                if (res.data?.restart_required) {
                    alert('MCP configuration saved, but a full AI Engine restart is required for some changes.');
                } else {
                    alert('MCP configuration saved and AI Engine reloaded.');
                }
            } catch (err: any) {
                alert(`MCP configuration saved. AI Engine reload failed: ${err.response?.data?.detail || err.message}`);
            } finally {
                setReloadingEngine(false);
                await fetchStatus();
            }
        } catch (err) {
            console.error('Failed to save config', err);
            alert('Failed to save configuration');
        } finally {
            setSaving(false);
        }
    };

    const updateMcp = (patch: any) => {
        setConfig({ ...config, mcp: { ...(config.mcp || {}), ...patch } });
    };

    const openAddServer = () => {
        setServerForm({
            id: '',
            enabled: true,
            transport: 'stdio',
            commandExec: '',
            commandArgs: '',
            cwd: '',
            defaults: {
                timeout_ms: 10000,
                slow_response_threshold_ms: 0,
                slow_response_message: 'Let me look that up for you, one moment...',
            },
            restart: { enabled: true, max_restarts: 5, backoff_ms: 1000 },
            env: [],
            tools: [],
        });
        setEditing(true);
    };

    const openEditServer = (id: string) => {
        const s = servers[id] || {};
        const cmd = Array.isArray(s.command) ? s.command : [];
        setServerForm({
            id,
            enabled: s.enabled !== false,
            transport: s.transport || 'stdio',
            commandExec: cmd[0] || '',
            commandArgs: (cmd.slice(1) || []).join(' '),
            cwd: s.cwd || '',
            defaults: {
                timeout_ms: s.defaults?.timeout_ms ?? 10000,
                slow_response_threshold_ms: s.defaults?.slow_response_threshold_ms ?? 0,
                slow_response_message: s.defaults?.slow_response_message ?? 'Let me look that up for you, one moment...',
            },
            restart: {
                enabled: s.restart?.enabled ?? true,
                max_restarts: s.restart?.max_restarts ?? 5,
                backoff_ms: s.restart?.backoff_ms ?? 1000,
            },
            env: Object.entries(s.env || {}).map(([k, v]: any) => {
                const value = String(v);
                const isRef = /^\$\{[A-Za-z0-9_]+\}$/.test(value);
                return isRef ? { key: String(k), value } : { key: String(k), value: '', redacted: true };
            }),
            tools: Array.isArray(s.tools) ? s.tools : [],
        });
        setEditing(true);
    };

    const saveServerForm = () => {
        if (!serverForm) return;
        const id = (serverForm.id || '').trim();
        if (!id) {
            alert('Server ID is required');
            return;
        }
        if (!/^[a-zA-Z0-9_]+$/.test(id)) {
            alert('Server ID must be provider-safe (letters, numbers, underscores)');
            return;
        }
        const cmd = [serverForm.commandExec.trim(), ..._parseArgLine(serverForm.commandArgs)].filter(Boolean);
        if (cmd.length === 0) {
            alert('Command is required');
            return;
        }

        const envObj: Record<string, string> = {};
        for (const row of serverForm.env) {
            const k = (row.key || '').trim();
            if (!k) continue;
            const v = String(row.value || '').trim();
            if (row.redacted && !v) {
                const existing = servers[id]?.env?.[k];
                if (typeof existing === 'string') envObj[k] = existing;
                continue;
            }
            envObj[k] = v;
        }
        const unsafeEnv = Object.entries(envObj).filter(([_k, v]) => v && !/^\$\{[A-Za-z0-9_]+\}$/.test(v));
        if (unsafeEnv.length > 0) {
            const names = unsafeEnv.map(([k]) => k).join(', ');
            if (!confirm(`Some env values are not placeholders like \${VAR} (keys: ${names}). This may expose secrets in YAML/UI. Continue?`)) {
                return;
            }
        }

        const toolList = (serverForm.tools || [])
            .map((t) => ({ ...t, name: String(t.name || '').trim(), expose_as: t.expose_as ? String(t.expose_as).trim() : undefined }))
            .filter((t) => !!t.name);
        const seenToolNames = new Set<string>();
        for (const t of toolList) {
            if (seenToolNames.has(t.name)) {
                alert(`Duplicate tool override name: ${t.name}`);
                return;
            }
            seenToolNames.add(t.name);
            if (t.expose_as && !/^[a-zA-Z0-9_]+$/.test(t.expose_as)) {
                alert(`Invalid expose_as '${t.expose_as}' (letters, numbers, underscores)`);
                return;
            }
        }

        const nextServers = { ...servers };
        nextServers[id] = {
            enabled: !!serverForm.enabled,
            transport: serverForm.transport || 'stdio',
            command: cmd,
            cwd: (serverForm.cwd || '').trim() || undefined,
            env: envObj,
            defaults: serverForm.defaults,
            restart: serverForm.restart,
            tools: toolList,
        };

        updateMcp({ enabled: mcpConfig.enabled ?? false, servers: nextServers });
        setEditing(false);
        setServerForm(null);
    };

    const deleteServer = (id: string) => {
        if (!confirm(`Delete MCP server '${id}' from config?`)) return;
        const nextServers = { ...servers };
        delete nextServers[id];
        updateMcp({ enabled: mcpConfig.enabled ?? false, servers: nextServers });
    };

    const testServer = async (id: string) => {
        setTestRunning(prev => ({ ...prev, [id]: true }));
        try {
            const res = await axios.post(`/api/mcp/servers/${id}/test`);
            if (res.data.ok) {
                alert(`MCP server '${id}' OK. Tools: ${(res.data.tools || []).join(', ')}`);
            } else {
                alert(`MCP server '${id}' failed: ${res.data.error || 'unknown error'}`);
            }
        } catch (err: any) {
            alert(`MCP test failed: ${err.response?.data?.detail || err.message}`);
        } finally {
            setTestRunning(prev => ({ ...prev, [id]: false }));
            await fetchStatus();
        }
    };

    const serverEntries = useMemo(() => Object.entries(servers || {}), [servers]);

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading configuration...</div>;

    return (
        <div className="space-y-6">
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">MCP Servers</h1>
                    <p className="text-muted-foreground mt-1">
                        Configure MCP-backed tools (Model Context Protocol). Changes require an AI Engine reload.
                    </p>
                </div>
                <div className="flex gap-2">
                    <button
                        onClick={fetchStatus}
                        disabled={statusLoading}
                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2"
                    >
                        <RefreshCw className={`w-4 h-4 mr-2 ${statusLoading ? 'animate-spin' : ''}`} />
                        Refresh Status
                    </button>
                    <button
                        onClick={handleSave}
                        disabled={saving}
                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                    >
                        <Save className="w-4 h-4 mr-2" />
                        {saving ? 'Saving...' : (reloadingEngine ? 'Reloading...' : 'Save & Reload')}
                    </button>
                </div>
            </div>

            <div className="bg-yellow-500/10 border border-yellow-500/20 text-yellow-600 dark:text-yellow-500 p-4 rounded-md flex items-center justify-between">
                <div className="flex items-center">
                    <AlertCircle className="w-5 h-5 mr-2" />
                    AI Engine reload applies MCP config changes when there are no active calls. “Test” runs in the AI Engine container context.
                </div>
            </div>

            <ConfigSection title="Global MCP Settings" description="Enable or disable MCP tooling globally.">
                <ConfigCard>
                    <div className="flex items-center justify-between">
                        <div>
                            <p className="font-medium">Enable MCP</p>
                            <p className="text-sm text-muted-foreground">When enabled, MCP servers can register tools into the agent tool registry.</p>
                        </div>
                        <label className="relative inline-flex items-center cursor-pointer">
                            <input
                                type="checkbox"
                                className="sr-only peer"
                                checked={!!mcpConfig.enabled}
                                onChange={(e) => updateMcp({ enabled: e.target.checked })}
                            />
                            <div className="w-11 h-6 bg-gray-600 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-primary rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:start-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                        </label>
                    </div>
                </ConfigCard>
            </ConfigSection>

            <ConfigSection
                title="Configured MCP Servers"
                description="Servers defined in YAML. Add servers and tool overrides; use Contexts to enable MCP tools per context."
            >
                <div className="flex justify-end">
                    <button
                        onClick={openAddServer}
                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                    >
                        <Plus className="w-4 h-4 mr-2" />
                        Add Server
                    </button>
                </div>

                <div className="grid grid-cols-1 gap-4 mt-4">
                    {serverEntries.map(([id, s]) => {
                        const st = status?.servers?.[id];
                        const up = st?.up;
                        const discoveredCount = (st?.discovered_tools || []).length;
                        const registeredCount = (st?.registered_tools || []).length;
                        const cmd = Array.isArray(s.command) ? s.command.join(' ') : '';
                        return (
                            <ConfigCard key={id} className="group">
                                <div className="flex items-start justify-between gap-4">
                                    <div className="min-w-0">
                                        <div className="flex items-center gap-2">
                                            <div className={`w-2.5 h-2.5 rounded-full ${up ? 'bg-green-500' : 'bg-gray-400'}`} />
                                            <h3 className="font-semibold text-lg">{id}</h3>
                                            {s.enabled === false && (
                                                <span className="text-xs px-2 py-0.5 rounded border text-muted-foreground">disabled</span>
                                            )}
                                        </div>
                                        <p className="text-sm text-muted-foreground mt-1 break-all">
                                            Command: <span className="font-mono text-xs">{cmd || '(not set)'}</span>
                                        </p>
                                        <div className="flex gap-2 mt-2 text-xs text-muted-foreground">
                                            <span>Discovered: {discoveredCount}</span>
                                            <span>Registered: {registeredCount}</span>
                                            {st?.last_error && <span className="text-destructive">Error: {String(st.last_error)}</span>}
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <button
                                            onClick={() => testServer(id)}
                                            disabled={!!testRunning[id]}
                                            className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-3 py-2"
                                            title="Test (runs in AI Engine container)"
                                        >
                                            <Play className="w-4 h-4 mr-2" />
                                            {testRunning[id] ? 'Testing...' : 'Test'}
                                        </button>
                                        <button
                                            onClick={() => openEditServer(id)}
                                            className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-3 py-2"
                                        >
                                            <Settings2 className="w-4 h-4 mr-2" />
                                            Edit
                                        </button>
                                        <button
                                            onClick={() => deleteServer(id)}
                                            className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring border border-input bg-background shadow-sm hover:bg-destructive/10 hover:text-destructive h-9 px-3 py-2"
                                            title="Delete server from config"
                                        >
                                            <Trash2 className="w-4 h-4" />
                                        </button>
                                    </div>
                                </div>
                            </ConfigCard>
                        );
                    })}
                    {serverEntries.length === 0 && (
                        <div className="p-8 border border-dashed rounded-lg text-center text-muted-foreground">
                            No MCP servers configured. Click “Add Server” to create one.
                        </div>
                    )}
                </div>
            </ConfigSection>

            <Modal
                isOpen={editing}
                onClose={() => { setEditing(false); setServerForm(null); }}
                title={serverForm?.id ? `Edit MCP Server: ${serverForm.id}` : 'Add MCP Server'}
                size="lg"
                footer={
                    <>
                        <button
                            onClick={() => { setEditing(false); setServerForm(null); }}
                            className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2"
                        >
                            Cancel
                        </button>
                        <button
                            onClick={saveServerForm}
                            className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                        >
                            Save Server
                        </button>
                    </>
                }
            >
                {serverForm && (
                    <div className="space-y-6">
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="font-medium">Enabled</p>
                                <p className="text-sm text-muted-foreground">Disabled servers are ignored by the AI Engine.</p>
                            </div>
                            <label className="relative inline-flex items-center cursor-pointer">
                                <input
                                    type="checkbox"
                                    className="sr-only peer"
                                    checked={!!serverForm.enabled}
                                    onChange={(e) => setServerForm({ ...serverForm, enabled: e.target.checked })}
                                />
                                <div className="w-11 h-6 bg-gray-600 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-primary rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:start-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                            </label>
                        </div>

                        <FormInput
                            label="Server ID"
                            value={serverForm.id}
                            onChange={(e) => setServerForm({ ...serverForm, id: e.target.value })}
                            placeholder="e.g., weather"
                            tooltip="Provider-safe identifier (letters, numbers, underscores)."
                        />

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <FormInput
                                label="Command Executable"
                                value={serverForm.commandExec}
                                onChange={(e) => setServerForm({ ...serverForm, commandExec: e.target.value })}
                                placeholder="python3"
                                tooltip="First argv element (executable)."
                            />
                            <FormInput
                                label="Command Arguments"
                                value={serverForm.commandArgs}
                                onChange={(e) => setServerForm({ ...serverForm, commandArgs: e.target.value })}
                                placeholder="-m my_mcp_server"
                                tooltip="Space-separated argv (basic split). Prefer simple args; complex quoting should be avoided."
                            />
                        </div>

                        <FormInput
                            label="Working Directory (optional)"
                            value={serverForm.cwd || ''}
                            onChange={(e) => setServerForm({ ...serverForm, cwd: e.target.value })}
                            placeholder="/app/mcp_servers/weather"
                        />

                        <div className="space-y-3">
                            <FormLabel tooltip="Default execution behavior for tools on this server.">
                                Defaults
                            </FormLabel>
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                <FormInput
                                    label="Timeout (ms)"
                                    value={String(serverForm.defaults.timeout_ms)}
                                    onChange={(e) => setServerForm({ ...serverForm, defaults: { ...serverForm.defaults, timeout_ms: Number(e.target.value || 0) } })}
                                    placeholder="10000"
                                />
                                <FormInput
                                    label="Slow Threshold (ms)"
                                    value={String(serverForm.defaults.slow_response_threshold_ms)}
                                    onChange={(e) => setServerForm({ ...serverForm, defaults: { ...serverForm.defaults, slow_response_threshold_ms: Number(e.target.value || 0) } })}
                                    placeholder="0"
                                />
                                <FormInput
                                    label="Slow Message"
                                    value={serverForm.defaults.slow_response_message}
                                    onChange={(e) => setServerForm({ ...serverForm, defaults: { ...serverForm.defaults, slow_response_message: e.target.value } })}
                                    placeholder="Let me look that up for you, one moment..."
                                />
                            </div>
                        </div>

                        <div className="space-y-3">
                            <FormLabel tooltip="Auto-restart behavior if the MCP server process exits.">
                                Restart Policy
                            </FormLabel>
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                <div className="flex items-center justify-between p-3 rounded-md border border-border bg-card/50">
                                    <div>
                                        <p className="text-sm font-medium">Enabled</p>
                                        <p className="text-xs text-muted-foreground">Auto-restart on crash</p>
                                    </div>
                                    <input
                                        type="checkbox"
                                        className="h-4 w-4"
                                        checked={!!serverForm.restart.enabled}
                                        onChange={(e) => setServerForm({ ...serverForm, restart: { ...serverForm.restart, enabled: e.target.checked } })}
                                    />
                                </div>
                                <FormInput
                                    label="Max Restarts"
                                    value={String(serverForm.restart.max_restarts)}
                                    onChange={(e) => setServerForm({ ...serverForm, restart: { ...serverForm.restart, max_restarts: Number(e.target.value || 0) } })}
                                    placeholder="5"
                                />
                                <FormInput
                                    label="Backoff (ms)"
                                    value={String(serverForm.restart.backoff_ms)}
                                    onChange={(e) => setServerForm({ ...serverForm, restart: { ...serverForm.restart, backoff_ms: Number(e.target.value || 0) } })}
                                    placeholder="1000"
                                />
                            </div>
                        </div>

                        <div className="space-y-3">
                            <FormLabel tooltip="Environment variables passed to the MCP server process. Prefer placeholders like ${VAR}. Non-placeholder values are redacted when editing.">
                                Environment (optional)
                            </FormLabel>
                            <div className="space-y-2">
                                {serverForm.env.map((row, idx) => (
                                    <div key={idx} className="grid grid-cols-5 gap-2 items-center">
                                        <input
                                            className="col-span-2 p-2 rounded-md border border-input bg-transparent text-sm"
                                            placeholder="KEY"
                                            value={row.key}
                                            onChange={(e) => {
                                                const next = [...serverForm.env];
                                                next[idx] = { ...row, key: e.target.value };
                                                setServerForm({ ...serverForm, env: next });
                                            }}
                                        />
                                        <input
                                            className="col-span-2 p-2 rounded-md border border-input bg-transparent text-sm"
                                            placeholder={row.redacted ? "<redacted>" : "${ENV_VAR}"}
                                            value={row.value}
                                            onChange={(e) => {
                                                const next = [...serverForm.env];
                                                next[idx] = { ...row, value: e.target.value, redacted: false };
                                                setServerForm({ ...serverForm, env: next });
                                            }}
                                        />
                                        <button
                                            className="p-2 rounded-md border border-input hover:bg-destructive/10 hover:text-destructive"
                                            onClick={() => {
                                                const next = serverForm.env.filter((_, i) => i !== idx);
                                                setServerForm({ ...serverForm, env: next });
                                            }}
                                            title="Remove"
                                        >
                                            <Trash2 className="w-4 h-4" />
                                        </button>
                                    </div>
                                ))}
                                <button
                                    className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring border border-input bg-background shadow-sm hover:bg-accent hover:text-foreground h-9 px-4 py-2"
                                    onClick={() => setServerForm({ ...serverForm, env: [...serverForm.env, { key: '', value: '' }] })}
                                >
                                    <Plus className="w-4 h-4 mr-2" />
                                    Add Env Var
                                </button>
                            </div>
                        </div>

                        <div className="space-y-3">
                            <FormLabel tooltip="Optional allowlist and overrides. If empty, all discovered tools are registered automatically.">
                                Tool Overrides (optional)
                            </FormLabel>
                            <div className="space-y-2">
                                {serverForm.tools.map((t, idx) => (
                                    <div key={idx} className="p-3 rounded-md border border-border bg-card/50 space-y-3">
                                        <div className="flex items-center justify-between">
                                            <p className="text-sm font-medium">Tool #{idx + 1}</p>
                                            <button
                                                className="p-2 rounded-md border border-input hover:bg-destructive/10 hover:text-destructive"
                                                onClick={() => {
                                                    const next = serverForm.tools.filter((_, i) => i !== idx);
                                                    setServerForm({ ...serverForm, tools: next });
                                                }}
                                                title="Remove tool override"
                                            >
                                                <Trash2 className="w-4 h-4" />
                                            </button>
                                        </div>
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                            <FormInput
                                                label="Tool Name"
                                                value={t.name || ''}
                                                onChange={(e) => {
                                                    const next = [...serverForm.tools];
                                                    next[idx] = { ...t, name: e.target.value };
                                                    setServerForm({ ...serverForm, tools: next });
                                                }}
                                                placeholder="get_weather_by_city"
                                            />
                                            <FormInput
                                                label="Expose As (optional)"
                                                value={t.expose_as || ''}
                                                onChange={(e) => {
                                                    const next = [...serverForm.tools];
                                                    next[idx] = { ...t, expose_as: e.target.value };
                                                    setServerForm({ ...serverForm, tools: next });
                                                }}
                                                placeholder="mcp_weather_get_city"
                                                tooltip="Provider-safe name; if omitted, AI Engine auto-generates one."
                                            />
                                        </div>
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                            <FormInput
                                                label="Speech Field (optional)"
                                                value={t.speech_field || ''}
                                                onChange={(e) => {
                                                    const next = [...serverForm.tools];
                                                    next[idx] = { ...t, speech_field: e.target.value };
                                                    setServerForm({ ...serverForm, tools: next });
                                                }}
                                                placeholder="atis_text"
                                            />
                                            <FormInput
                                                label="Speech Template (optional)"
                                                value={t.speech_template || ''}
                                                onChange={(e) => {
                                                    const next = [...serverForm.tools];
                                                    next[idx] = { ...t, speech_template: e.target.value };
                                                    setServerForm({ ...serverForm, tools: next });
                                                }}
                                                placeholder="The ATIS for {icao} is {atis_text}"
                                            />
                                        </div>
                                    </div>
                                ))}
                                <button
                                    className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring border border-input bg-background shadow-sm hover:bg-accent hover:text-foreground h-9 px-4 py-2"
                                    onClick={() => setServerForm({ ...serverForm, tools: [...serverForm.tools, { name: '' }] })}
                                >
                                    <Plus className="w-4 h-4 mr-2" />
                                    Add Tool Override
                                </button>
                            </div>
                        </div>
                    </div>
                )}
            </Modal>
        </div>
    );
};

export default MCPPage;
