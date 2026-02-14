import { useState, useEffect } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useConfirmDialog } from '../hooks/useConfirmDialog';
import yaml from 'js-yaml';
import { sanitizeConfigForSave } from '../utils/configSanitizers';
import { Plus, Settings, Trash2, MessageSquare, AlertCircle, RefreshCw, Loader2 } from 'lucide-react';
import { YamlErrorBanner } from '../components/ui/YamlErrorBanner';
import { ConfigSection } from '../components/ui/ConfigSection';
import { ConfigCard } from '../components/ui/ConfigCard';
import { Modal } from '../components/ui/Modal';
import ContextForm from '../components/config/ContextForm';

const ContextsPage = () => {
    const { confirm } = useConfirmDialog();
    const [config, setConfig] = useState<any>({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [yamlError, setYamlError] = useState<{
        type?: string;
        message?: string;
        line?: number;
        column?: number;
        problem?: string;
        snippet?: string;
    } | null>(null);
    const [availableTools, setAvailableTools] = useState<string[]>([]);
    const [toolEnabledMap, setToolEnabledMap] = useState<Record<string, boolean>>({});
    const [toolCatalogByName, setToolCatalogByName] = useState<Record<string, any>>({});
    const [editingContext, setEditingContext] = useState<string | null>(null);
    const [contextForm, setContextForm] = useState<any>({});
    const [isNewContext, setIsNewContext] = useState(false);
    const [pendingApply, setPendingApply] = useState(false);
    const [applyMethod, setApplyMethod] = useState<'hot_reload' | 'restart'>('restart');
    const [restartingEngine, setRestartingEngine] = useState(false);

    useEffect(() => {
        fetchConfig();
    }, []);

    const fetchConfig = async () => {
        try {
            const res = await axios.get('/api/config/yaml');
            // Check if there's a YAML error in the response (content still provided for Raw YAML editing)
            if (res.data.yaml_error) {
                setYamlError(res.data.yaml_error);
                setConfig({});
                setError(null);
            } else {
                const parsed = yaml.load(res.data.content) as any;
                setConfig(parsed || {});
                await fetchMcpTools(parsed || {});
                await fetchToolCatalog();
                setError(null);
                setYamlError(null);
            }
        } catch (err) {
            console.error('Failed to load config', err);
            const status = (err as any)?.response?.status;
            
            if (status === 401) {
                setError('Not authenticated. Please refresh and log in again.');
                setYamlError(null);
            } else {
                setError('Failed to load configuration. Check backend logs and try again.');
                setYamlError(null);
            }
        } finally {
            setLoading(false);
        }
    };

    const fetchToolCatalog = async () => {
        try {
            const res = await axios.get('/api/tools/catalog');
            const tools = (res.data && Array.isArray(res.data.tools)) ? res.data.tools : [];
            const next: Record<string, any> = {};
            tools.forEach((t: any) => {
                if (t && typeof t === 'object' && typeof t.name === 'string' && t.name.trim()) {
                    next[t.name.trim()] = t;
                }
            });
            setToolCatalogByName(next);
        } catch (err) {
            // Non-blocking: context editing should still work even if catalog fails to load.
            setToolCatalogByName({});
        }
    };

    const fetchMcpTools = async (parsedConfig: any) => {
        try {
            const res = await axios.get('/api/mcp/status');
            const routes = res.data?.tool_routes || {};
            const mcpTools = Object.keys(routes).filter((t) => typeof t === 'string' && t.startsWith('mcp_'));

            // Built-in (Python) in-call tools that may not appear under `tools:` in YAML unless
            // explicitly configured/disabled. Keep this list broad so users can enable tools
            // per-context from the UI.
            const builtinInCallTools = [
                'transfer',
                'attended_transfer',
                'cancel_transfer',
                'live_agent_transfer',
                'hangup_call',
                'leave_voicemail',
                'send_email_summary',
                'request_transcript',
                'check_extension_status',
            ];

            const toolsBlock = parsedConfig?.tools || {};
            const yamlToolEntries = Object.entries(toolsBlock)
                .filter(([k, v]) => {
                    if (typeof k !== 'string' || !k) return false;
                    if (!v || typeof v !== 'object' || Array.isArray(v)) return false;
                    // Tool configs are dict-like and typically include an `enabled` flag.
                    // Exclude tool-system settings like ai_identity/extensions/default_action_timeout.
                    if (!Object.prototype.hasOwnProperty.call(v, 'enabled')) return false;
                    // Exclude HTTP phase tools (pre_call/post_call) - these are shown in separate sections
                    const kind = (v as any)?.kind;
                    if (kind === 'generic_http_lookup' || kind === 'generic_webhook') return false;
                    return true;
                })
                .map(([k, v]) => ({ name: k, enabled: (v as any)?.enabled !== false }));
            const toolsFromYaml = yamlToolEntries.map((t) => t.name);

            const merged = Array.from(new Set([
                ...builtinInCallTools,
                ...toolsFromYaml,
                ...mcpTools
            ])).sort();
            setAvailableTools(merged);
            const nextMap: Record<string, boolean> = {};
            yamlToolEntries.forEach((t) => { nextMap[t.name] = t.enabled; });
            mcpTools.forEach((t) => { nextMap[t] = true; });
            // Assume built-in tools are enabled unless configured otherwise in YAML.
            builtinInCallTools.forEach((t) => { if (nextMap[t] == null) nextMap[t] = true; });
            setToolEnabledMap(nextMap);
        } catch (err) {
            // Non-fatal: MCP may be disabled or ai-engine down. Fall back to YAML tools.
            const toolsBlock = parsedConfig?.tools || {};
            const yamlToolEntries = Object.entries(toolsBlock)
                .filter(([k, v]) => {
                    if (typeof k !== 'string' || !k) return false;
                    if (!v || typeof v !== 'object' || Array.isArray(v)) return false;
                    if (!Object.prototype.hasOwnProperty.call(v, 'enabled')) return false;
                    // Exclude HTTP phase tools - they have separate UI sections
                    const kind = (v as any)?.kind;
                    if (kind === 'generic_http_lookup' || kind === 'generic_webhook') return false;
                    return true;
                })
                .map(([k, v]) => ({ name: k, enabled: (v as any)?.enabled !== false }));
            const toolsFromYaml = yamlToolEntries.map((t) => t.name);
            const builtinInCallTools = [
                'transfer',
                'attended_transfer',
                'cancel_transfer',
                'live_agent_transfer',
                'hangup_call',
                'leave_voicemail',
                'send_email_summary',
                'request_transcript',
                'check_extension_status',
            ];
            setAvailableTools(Array.from(new Set([...builtinInCallTools, ...toolsFromYaml])).slice().sort());
            const nextMap: Record<string, boolean> = {};
            yamlToolEntries.forEach((t) => { nextMap[t.name] = t.enabled; });
            builtinInCallTools.forEach((t) => { if (nextMap[t] == null) nextMap[t] = true; });
            setToolEnabledMap(nextMap);
        }
    };

    const saveConfig = async (newConfig: any) => {
        try {
            const sanitized = sanitizeConfigForSave(newConfig);
            const res = await axios.post('/api/config/yaml', { content: yaml.dump(sanitized) });
            setConfig(sanitized);
            const method = (res.data?.recommended_apply_method || 'restart') as 'hot_reload' | 'restart';
            setApplyMethod(method);
            setPendingApply(true);
        } catch (err) {
            console.error('Failed to save config', err);
            toast.error('Failed to save configuration');
        }
    };

    const handleApplyChanges = async (force: boolean = false) => {
        setRestartingEngine(true);
        try {
            const endpoint = applyMethod === 'hot_reload'
                ? '/api/system/containers/ai_engine/reload'
                : `/api/system/containers/ai_engine/restart?force=${force}`;
            const response = await axios.post(endpoint);
            const status = response.data?.status ?? (response.status === 200 ? 'success' : undefined);

            if (status === 'warning') {
                const confirmForce = await confirm({
                    title: 'Force Restart?',
                    description: `${response.data.message} Do you want to force restart anyway? This may disconnect active calls.`,
                    confirmText: 'Force Restart',
                    variant: 'destructive'
                });
                if (confirmForce) {
                    return handleApplyChanges(true);
                }
                return;
            }

            if (status === 'degraded') {
                setPendingApply(false);
                toast.warning('AI Engine restarted but may not be fully healthy', { description: response.data.output || 'Please verify manually' });
                fetchConfig();
                return;
            }

            if (status === 'partial' || response.data?.restart_required === true) {
                // Hot reload succeeded but indicated some changes require a restart (e.g. providers added/removed,
                // MCP reload deferred due to active calls).
                setApplyMethod('restart');
                setPendingApply(true);
                toast.warning(response.data.message || 'Hot reload applied partially; restart AI Engine to fully apply changes.');
                return;
            }

            if (status === 'success') {
                setPendingApply(false);
                toast.success(applyMethod === 'hot_reload'
                    ? 'AI Engine hot reloaded! Changes apply to new calls.'
                    : 'AI Engine restarted! Changes are now active.');
                // Refresh config/tool availability after apply (best-effort)
                fetchConfig();
                return;
            }

            // Be conservative: if the apply endpoint returned 200 but an unexpected payload, assume the action
            // completed so the UI doesn't get stuck showing "Apply Changes" forever.
            if (response.status === 200) {
                setPendingApply(false);
                toast.success('AI Engine updated. Please verify with a test call and logs.');
                fetchConfig();
                return;
            }
        } catch (error: any) {
            const action = applyMethod === 'hot_reload' ? 'hot reload' : 'restart';
            toast.error(`Failed to ${action} AI Engine`, { description: error.response?.data?.detail || error.message });
        } finally {
            setRestartingEngine(false);
        }
    };

    const handleEditContext = (name: string) => {
        setEditingContext(name);
        setContextForm({ name, ...config.contexts?.[name] });
        setIsNewContext(false);
    };

    const handleAddContext = () => {
        const transferToolName = availableTools.includes('blind_transfer')
            ? 'blind_transfer'
            : (availableTools.includes('transfer') ? 'transfer' : '');
        const defaultTools = [transferToolName, 'hangup_call'].filter((t) => !!t && availableTools.includes(t));
        const preferredDefaultProfile = 'telephony_ulaw_8k';
        const newContextProfile = (availableProfiles && availableProfiles.includes(preferredDefaultProfile))
            ? preferredDefaultProfile
            : '';
        setEditingContext('new_context');
        setContextForm({
            name: '',
            greeting: 'Hi {caller_name}, how can I help you today?',
            prompt: 'You are a helpful voice assistant.',
            // Default to telephony_ulaw_8k so new contexts work on first call,
            // even when profiles.default is telephony_responsive.
            profile: newContextProfile,
            provider: '',
            tools: defaultTools
        });
        setIsNewContext(true);
    };

    const handleDeleteContext = async (name: string) => {
        const confirmed = await confirm({
            title: 'Delete Context?',
            description: `Are you sure you want to delete context "${name}"?`,
            confirmText: 'Delete',
            variant: 'destructive'
        });
        if (!confirmed) return;
        const newContexts = { ...config.contexts };
        delete newContexts[name];
        await saveConfig({ ...config, contexts: newContexts });
    };

    const handleSaveContext = async () => {
        if (!contextForm.name) return;

        // Validation: Check provider
        if (contextForm.provider) {
            const provider = config.providers?.[contextForm.provider];
            if (!provider) {
                toast.error(`Provider '${contextForm.provider}' does not exist.`);
                return;
            }
            if (provider.enabled === false) {
                toast.error(`Provider '${contextForm.provider}' is disabled. Please enable it or select another provider.`);
                return;
            }
        }

        // P1 Validation: Check pipeline exists
        if (contextForm.pipeline) {
            const pipeline = config.pipelines?.[contextForm.pipeline];
            if (!pipeline) {
                toast.error(`Pipeline '${contextForm.pipeline}' does not exist. Please select a valid pipeline or leave blank to use the active pipeline.`);
                return;
            }
        }

        const newConfig = { ...config };
        if (!newConfig.contexts) newConfig.contexts = {};

        const { name, ...contextData } = contextForm;
        const cleanedContextData = { ...contextData };
        // Avoid persisting empty-string overrides into YAML (prefer omission for "use default")
        ['profile', 'provider', 'pipeline'].forEach((k) => {
            if ((cleanedContextData as any)[k] === '') {
                delete (cleanedContextData as any)[k];
            }
        });

        if (isNewContext && newConfig.contexts[name]) {
            toast.error('Context already exists');
            return;
        }

        newConfig.contexts[name] = cleanedContextData;
        await saveConfig(newConfig);
        setEditingContext(null);
    };

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading configuration...</div>;
    if (yamlError) {
        return (
            <div className="space-y-4 p-6">
                <YamlErrorBanner error={yamlError} />
                <div className="flex items-center justify-between rounded-md border border-red-500/30 bg-red-500/10 p-4 text-red-700 dark:text-red-400">
                    <div className="flex items-center">
                        <AlertCircle className="mr-2 h-5 w-5" />
                        Context editing is disabled while `config/ai-agent.yaml` has YAML errors. Fix the YAML and reload.
                    </div>
                    <button
                        onClick={() => window.location.reload()}
                        className="flex items-center text-xs px-3 py-1.5 rounded transition-colors bg-red-500 text-white hover:bg-red-600 font-medium"
                    >
                        Reload
                    </button>
                </div>
            </div>
        );
    }

    const profilesBlock = config.profiles || {};
    const defaultProfileName = (typeof profilesBlock.default === 'string' && profilesBlock.default) ? profilesBlock.default : '';
    const availableProfiles = Object.entries(profilesBlock)
        .filter(([k, v]) => k !== 'default' && !!v && typeof v === 'object' && !Array.isArray(v))
        .map(([k]) => k)
        .sort();

    const displayToolName = (tool: string) => {
        if (tool === 'transfer') return 'blind_transfer';
        return tool;
    };

    return (
        <div className="space-y-6">
            {pendingApply && (
                <div className="bg-orange-500/15 border border-orange-500/30 text-yellow-700 dark:text-yellow-400 p-4 rounded-md flex items-center justify-between">
                    <div className="flex items-center">
                        <AlertCircle className="w-5 h-5 mr-2" />
                        {applyMethod === 'hot_reload' ? 'Changes saved. Apply to make them active.' : 'Changes saved. Restart required to make them active.'}
                    </div>
                    <button
                        onClick={async () => {
                            const msg = applyMethod === 'hot_reload'
                                ? 'Apply changes via hot reload now? Active calls should continue, new calls use updated config.'
                                : 'Restart AI Engine now? This may disconnect active calls.';
                            const confirmed = await confirm({
                                title: applyMethod === 'hot_reload' ? 'Apply Changes?' : 'Restart AI Engine?',
                                description: msg,
                                confirmText: applyMethod === 'hot_reload' ? 'Apply' : 'Restart',
                                variant: 'default'
                            });
                            if (confirmed) {
                                await handleApplyChanges(false);
                            }
                        }}
                        disabled={restartingEngine}
                        className="flex items-center text-xs px-3 py-1.5 rounded transition-colors bg-orange-500 text-white hover:bg-orange-600 font-medium disabled:opacity-50"
                    >
                        {restartingEngine ? (
                            <Loader2 className="w-3 h-3 mr-1.5 animate-spin" />
                        ) : (
                            <RefreshCw className="w-3 h-3 mr-1.5" />
                        )}
                        {restartingEngine ? 'Applying...' : applyMethod === 'hot_reload' ? 'Apply Changes' : 'Restart AI Engine'}
                    </button>
                </div>
            )}

            {error && (
                <div className="bg-red-500/15 border border-red-500/30 text-red-700 dark:text-red-400 p-4 rounded-md flex items-center justify-between">
                    <div className="flex items-center">
                        <AlertCircle className="w-5 h-5 mr-2" />
                        {error}
                    </div>
                    <button
                        onClick={() => window.location.reload()}
                        className="flex items-center text-xs px-3 py-1.5 rounded transition-colors bg-red-500 text-white hover:bg-red-600 font-medium"
                    >
                        Reload
                    </button>
                </div>
            )}

            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Contexts</h1>
                    <p className="text-muted-foreground mt-1">
                        Define AI personalities and behaviors for different use cases.
                    </p>
                </div>
                <button
                    onClick={handleAddContext}
                    className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                >
                    <Plus className="w-4 h-4 mr-2" />
                    Add Context
                </button>
            </div>

            <ConfigSection title="Defined Contexts" description="Manage conversation contexts and their settings.">
                <div className="grid grid-cols-1 gap-4">
                    {Object.entries(config.contexts || {}).map(([name, contextData]: [string, any]) => (
                        <ConfigCard key={name} className="group relative hover:border-primary/50 transition-colors">
                            <div className="flex justify-between items-start">
                                <div className="flex items-center gap-3 mb-4">
                                    <div className="p-2 bg-secondary rounded-md">
                                        <MessageSquare className="w-5 h-5 text-primary" />
                                    </div>
                                    <div>
                                        <h4 className="font-semibold text-lg">{name}</h4>
                                        <div className="flex gap-2 mt-1">
                                            <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 text-muted-foreground bg-secondary/50">
                                                {contextData.profile || defaultProfileName || 'default'}
                                            </span>
                                            {contextData.pipeline && (
                                                <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 text-muted-foreground bg-secondary/50">
                                                    {contextData.pipeline}
                                                </span>
                                            )}
                                            {contextData.provider && (
                                                <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 text-muted-foreground bg-secondary/50">
                                                    {contextData.provider}
                                                </span>
                                            )}
                                        </div>
                                    </div>
                                </div>
                                <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                    <button
                                        onClick={() => handleEditContext(name)}
                                        className="p-2 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground"
                                    >
                                        <Settings className="w-4 h-4" />
                                    </button>
                                    <button
                                        onClick={() => handleDeleteContext(name)}
                                        className="p-2 hover:bg-destructive/10 rounded-md text-destructive"
                                    >
                                        <Trash2 className="w-4 h-4" />
                                    </button>
                                </div>
                            </div>

                            <div className="space-y-3 text-sm">
                                <div className="bg-secondary/30 p-3 rounded-md">
                                    <span className="font-medium text-xs uppercase tracking-wider text-muted-foreground block mb-1">Greeting</span>
                                    <p className="text-foreground/90 italic">"{contextData.greeting}"</p>
                                </div>

                                {/* Show all tool types with colored phase badges */}
                                {(() => {
                                    const allTools = [
                                        ...(contextData.pre_call_tools || []).map((t: string) => ({ name: t, phase: 'pre', color: 'bg-blue-500/20 text-blue-600 dark:text-blue-400' })),
                                        ...(contextData.tools || []).map((t: string) => ({ name: t, phase: 'in', color: 'bg-green-500/20 text-green-600 dark:text-green-400' })),
                                        ...(contextData.in_call_http_tools || []).map((t: string) => ({ name: t, phase: 'in', color: 'bg-green-500/20 text-green-600 dark:text-green-400' })),
                                        ...(contextData.post_call_tools || []).map((t: string) => ({ name: t, phase: 'post', color: 'bg-orange-500/20 text-orange-600 dark:text-orange-400' })),
                                    ];
                                    return allTools.length > 0 ? (
                                        <div>
                                            <span className="font-medium text-xs uppercase tracking-wider text-muted-foreground block mb-2">Enabled Tools</span>
                                            <div className="flex flex-wrap gap-1.5">
                                                {allTools.map((tool, idx) => (
                                                    <span key={`${tool.phase}-${tool.name}-${idx}`} className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs bg-accent text-accent-foreground font-medium border border-accent-foreground/10">
                                                        <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${tool.color}`}>
                                                            {tool.phase}
                                                        </span>
                                                        {displayToolName(tool.name)}
                                                    </span>
                                                ))}
                                            </div>
                                        </div>
                                    ) : null;
                                })()}
                            </div>
                        </ConfigCard>
                    ))}
                    {Object.keys(config.contexts || {}).length === 0 && (
                        <div className="col-span-full p-8 border border-dashed rounded-lg text-center text-muted-foreground">
                            No contexts configured. Click "Add Context" to create one.
                        </div>
                    )}
                </div>
            </ConfigSection>

            <Modal
                isOpen={!!editingContext}
                onClose={() => setEditingContext(null)}
                title={isNewContext ? 'Add Context' : 'Edit Context'}
                size="lg"
                footer={
                    <>
                        <button
                            onClick={() => setEditingContext(null)}
                            className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2"
                        >
                            Cancel
                        </button>
                        <button
                            onClick={handleSaveContext}
                            className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                        >
                            Save Changes
                        </button>
                    </>
                }
            >
                <ContextForm
                    config={contextForm}
                    providers={config.providers}
                    pipelines={config.pipelines}
                    availableTools={availableTools}
                    toolEnabledMap={toolEnabledMap}
                    toolCatalogByName={toolCatalogByName}
                    availableProfiles={availableProfiles}
                    defaultProfileName={defaultProfileName}
                    httpTools={{...config.tools, ...config.in_call_tools}}
                    onChange={setContextForm}
                    isNew={isNewContext}
                />
            </Modal>
        </div>
    );
};

export default ContextsPage;
