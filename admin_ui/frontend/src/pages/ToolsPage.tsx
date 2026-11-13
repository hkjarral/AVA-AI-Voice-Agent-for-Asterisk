import { useState, useEffect, useMemo, useRef, useLayoutEffect } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useConfirmDialog } from '../hooks/useConfirmDialog';
import yaml from 'js-yaml';
import { ArrowLeft, Save, AlertCircle, RefreshCw, Loader2, Search, BookOpen, ChevronDown, ChevronRight } from 'lucide-react';
import { YamlErrorBanner, YamlErrorInfo } from '../components/ui/YamlErrorBanner';
import { ConfigSection } from '../components/ui/ConfigSection';
import { ConfigCard } from '../components/ui/ConfigCard';
import { ToolList } from '../components/config/ToolList';
import { ToolSection, toolSections, toolDefaults } from '../components/config/toolSections';
import ToolForm from '../components/config/ToolForm';
import HTTPToolForm from '../components/config/HTTPToolForm';
import { useAuth } from '../auth/AuthContext';
import { sanitizeConfigForSave } from '../utils/configSanitizers';
import { getCachedConfig, loadConfigYaml } from '../utils/configCache';
import { useAsyncScope } from '../hooks/useAsyncScope';
import { useRestartRequired } from '../hooks/useRestartRequired';

const UNKNOWN_APPLY_STATUS = 'Saved. Apply status is unknown. Refresh status before retrying.';
const UNKNOWN_SAVE_STATUS = 'Save outcome is unknown. Check saved configuration before retrying.';

type ToolPhase = 'in_call' | 'pre_call' | 'post_call' | 'catalog';

type ToolParam = {
    name: string;
    type: string;
    description: string;
    required?: boolean;
};

type ToolDef = {
    name: string;
    description: string;
    category?: string;
    phase?: string;
    is_global?: boolean;
    source?: 'builtin' | 'http' | 'mcp' | 'unknown' | string;
    parameters?: ToolParam[];
};

const ToolsPage = () => {
    const { confirm } = useConfirmDialog();
    const { token } = useAuth();
    const { isMounted, beginRead, cancelRead } = useAsyncScope();
    const currentToken = useRef(token);
    useLayoutEffect(() => {
        currentToken.current = token;
        // A changed token invalidates reads, not accepted server-side writes.
        setCheckingApplyStatus(false);
        return () => cancelRead('tool-apply-status');
    }, [token, cancelRead]);
    const [config, setConfigState] = useState<any>(() => getCachedConfig()?.config ?? {});
    const [savedSignature, setSavedSignature] = useState(() => JSON.stringify(getCachedConfig()?.config ?? {}));
    const [loadError, setLoadError] = useState<string | null>(null);
    const [selectedSection, setSelectedSection] = useState<ToolSection | null>(() => {
        const requested = new URLSearchParams(window.location.search).get('section');
        return toolSections.some(tool => tool.key === requested) ? requested as ToolSection : null;
    });
    const [query, setQuery] = useState('');
    const [httpEditing, setHttpEditing] = useState(false);
    const headingRef = useRef<HTMLHeadingElement>(null);
    const selectionTriggerRef = useRef<HTMLElement | null>(null);
    const configRef = useRef<any>(getCachedConfig()?.config ?? {});
    // Commit synchronously to the ref too: blur-committed inputs and immediate
    // template saves must see the same draft as the page Save action.
    const setConfig = (update: any) => {
        const next = typeof update === 'function' ? update(configRef.current) : update;
        configRef.current = next;
        setConfigState(next);
    };
    const dirty = JSON.stringify(config) !== savedSignature;
    const selectedTool = selectedSection === 'defaults' ? toolDefaults : toolSections.find(tool => tool.key === selectedSection);
    const selectSection = (section: ToolSection) => {
        selectionTriggerRef.current = document.activeElement as HTMLElement;
        setSelectedSection(section);
    };
    useEffect(() => {
        if (selectedSection) headingRef.current?.focus();
        else selectionTriggerRef.current?.focus();
    }, [selectedSection]);
    useEffect(() => {
        const warn = (event: BeforeUnloadEvent) => {
            if (!dirty && !httpEditing) return;
            event.preventDefault();
            event.returnValue = '';
        };
        window.addEventListener('beforeunload', warn);
        return () => window.removeEventListener('beforeunload', warn);
    }, [dirty, httpEditing]);
    const [loading, setLoading] = useState(() => getCachedConfig() == null);
    const [yamlError, setYamlError] = useState<YamlErrorInfo | null>(() => getCachedConfig()?.yamlError ?? null);
    const [saving, setSaving] = useState(false);
    const [applyStatus, setApplyStatus] = useState<string | null>(null);
    const [applyUncertain, setApplyUncertain] = useState(false);
    const [checkingApplyStatus, setCheckingApplyStatus] = useState(false);
    const { restartRequired, applyRequired, recommendedApplyMethod, stateStale, refetch, refreshAndRead } = useRestartRequired();
    const [restartingEngine, setRestartingEngine] = useState(false);
    const [activePhase, setActivePhase] = useState<ToolPhase>('in_call');
    const [toolCatalog, setToolCatalog] = useState<ToolDef[]>([]);
    const [toolCatalogError, setToolCatalogError] = useState<string | null>(null);
    const [toolCatalogLoading, setToolCatalogLoading] = useState(false);
    const [toolCatalogQuery, setToolCatalogQuery] = useState('');
    const [toolCatalogExpanded, setToolCatalogExpanded] = useState<Record<string, boolean>>({});

    const hangupUsage = useMemo(() => {
        const providers = (config && typeof config === 'object') ? (config as any).providers : null;
        const googleLiveMarkersEnabledRaw = providers?.google_live?.hangup_markers_enabled;
        const googleLiveMarkersEnabled =
            googleLiveMarkersEnabledRaw === true ? true : googleLiveMarkersEnabledRaw === false ? false : null;

        const pipelines = (config && typeof config === 'object') ? (config as any).pipelines : null;
        const pipelineEndCallOverrides: string[] = [];
        const pipelineModeOverrides: { name: string; mode: string }[] = [];
        const pipelineGuardrailOverrides: { name: string; enabled: boolean }[] = [];

        if (pipelines && typeof pipelines === 'object' && !Array.isArray(pipelines)) {
            Object.entries(pipelines).forEach(([name, pipeline]) => {
                const llmOpts = (pipeline as any)?.options?.llm;
                const end = llmOpts?.hangup_call_guardrail_markers?.end_call;
                if (Array.isArray(end) && end.length > 0) {
                    pipelineEndCallOverrides.push(name);
                }
                const mode = String(llmOpts?.hangup_call_guardrail_mode || '').trim();
                if (mode) {
                    pipelineModeOverrides.push({ name, mode });
                }
                const enabled = llmOpts?.hangup_call_guardrail;
                if (enabled === true || enabled === false) {
                    pipelineGuardrailOverrides.push({ name, enabled });
                }
            });
        }

        return {
            googleLiveMarkersEnabled,
            pipelineEndCallOverrides,
            pipelineModeOverrides,
            pipelineGuardrailOverrides,
        };
    }, [config]);

    useEffect(() => {
        fetchConfig();
        fetchToolCatalog();
    }, []);

    useEffect(() => {
        configRef.current = config;
    }, [config]);

    const fetchConfig = async (force = false) => {
        try {
            const r = await loadConfigYaml(force);
            setConfig(r.config);
            setSavedSignature(JSON.stringify(r.config));
            setLoadError(null);
            setYamlError(r.yamlError);
        } catch (err) {
            console.error('Failed to load config', err);
            setLoadError('Unable to load the configuration. Retry before editing or saving tools.');
        } finally {
            setLoading(false);
        }
    };

    const fetchToolCatalog = async () => {
        setToolCatalogLoading(true);
        try {
            const res = await axios.get('/api/tools/catalog');
            const tools = (res.data && Array.isArray(res.data.tools)) ? res.data.tools : [];
            setToolCatalog(tools);
            setToolCatalogError(null);
        } catch (err: any) {
            console.error('Failed to load tool catalog', err);
            setToolCatalog([]);
            setToolCatalogError(err?.response?.data?.detail || err?.message || 'Failed to load tool catalog');
        } finally {
            setToolCatalogLoading(false);
        }
    };

    const persistConfigNow = async (nextConfig: any, successToast?: string) => {
        setSaving(true);
        try {
            const sanitized = sanitizeConfigForSave(nextConfig);
            await axios.post('/api/config/yaml', { content: yaml.dump(sanitized) }, {
                headers: { Authorization: `Bearer ${token}` },
                timeout: 30000  // 30 second timeout
            });
            setSavedSignature(JSON.stringify(nextConfig));
            await refetch();
            if (successToast) toast.success(successToast);
        } catch (err: any) {
            console.error('Failed to save config', err);
            const detail = err.response?.data?.detail || err.message || 'Unknown error';
            toast.error('Failed to save configuration', { description: detail });
            throw err;
        } finally {
            setSaving(false);
        }
    };

    // A successful HTTP fallback is not engine truth. Require reachable, valid,
    // hashed state before allowing an uncertain operation to be retried.
    const refreshApplyStatus = async () => {
        const request = beginRead('tool-apply-status');
        if (!request) return;
        const current = () => request.isCurrent() && currentToken.current === token;
        setCheckingApplyStatus(true);
        try {
            const data = await refreshAndRead({
                headers: { Authorization: `Bearer ${token}` }, signal: request.signal, timeout: 15000,
            });
            if (!current()) return;
            if (data?.engine_reachable !== true || data.disk_config_valid !== true ||
                typeof data.running_config_hash !== 'string' || !data.running_config_hash ||
                typeof data.disk_config_hash !== 'string' || !data.disk_config_hash ||
                typeof data.restart_required !== 'boolean' || typeof data.apply_required !== 'boolean') {
                throw new Error('Current configuration status unavailable');
            }
            let message: string;
            if (data.restart_required) message = 'Current saved configuration requires an AI Engine restart.';
            else if (data.apply_required) message = 'Current saved configuration still needs Apply.';
            else if (data.running_config_hash === data.disk_config_hash) {
                message = 'Current saved configuration is active. Unsaved edits still need Save & Apply.';
            } else throw new Error('Configuration hashes have not converged');
            setApplyStatus(message);
            setApplyUncertain(false);
        } catch {
            // Preserve the unknown outcome and the operator's draft.
            if (current()) setApplyUncertain(true);
        } finally {
            if (current()) setCheckingApplyStatus(false);
            request.finish();
        }
    };

    const handleSave = async () => {
        if (saving || restartingEngine || applyUncertain || checkingApplyStatus) return;
        cancelRead('tool-apply-status');
        const ownsView = () => isMounted() && currentToken.current === token;
        setSaving(true);
        setApplyStatus(null);
        let configSaved = false;
        let requestStarted = false;
        try {
            const snapshot = configRef.current;
            const sanitized = sanitizeConfigForSave(snapshot);
            const content = yaml.dump(sanitized);
            requestStarted = true;
            const saved = await axios.post('/api/config/yaml', { content }, {
                headers: { Authorization: `Bearer ${token}` }, timeout: 30000,
            });
            configSaved = true;
            if (!ownsView()) return;
            setSavedSignature(JSON.stringify(snapshot));
            if (saved.data?.recommended_apply_method === 'hot_reload') {
                const applied = await axios.post('/api/system/containers/ai_engine/reload', {}, {
                    headers: { Authorization: `Bearer ${token}` }, timeout: 30000,
                });
                if (!ownsView()) return;
                if (applied.data?.restart_required) {
                    setApplyStatus('Saved and partially applied. Restart the AI Engine to finish applying changes.');
                    toast.warning('Restart still required', { description: applied.data?.message });
                } else {
                    const generation = applied.data?.tool_generation;
                    const message = `Applied${generation ? ` as tool generation ${generation}` : ''}. New calls use it; active calls remain unchanged.`;
                    setApplyStatus(message);
                    toast.success('Tools saved and applied', { description: message });
                }
            } else {
                setApplyStatus(saved.data?.recommended_apply_method === 'none'
                    ? 'Saved. No apply action is required.'
                    : 'Saved. A restart is required for these changes.');
                toast.success('Tools configuration saved');
            }
            await refetch();
        } catch (err: any) {
            if (!ownsView()) return;
            const detail = err.response?.data?.detail || err.message || 'Unknown error';
            if (!requestStarted) {
                setApplyStatus('Configuration was not saved. Correct validation errors and retry.');
                toast.error('Configuration was not saved', { description: detail });
                return;
            }
            setApplyStatus(configSaved ? UNKNOWN_APPLY_STATUS : UNKNOWN_SAVE_STATUS);
            setApplyUncertain(true);
            toast.warning('Save & Apply outcome could not be confirmed', { description: detail });
            if (configSaved) await refreshApplyStatus();
        } finally {
            if (isMounted()) {
                setSaving(false);
                if (!ownsView() && requestStarted) {
                    setApplyStatus(configSaved ? UNKNOWN_APPLY_STATUS : UNKNOWN_SAVE_STATUS);
                    setApplyUncertain(true);
                }
            }
        }
    };

    const handleRestartAIEngine = async (force: boolean = false) => {
        const ownsView = () => isMounted() && currentToken.current === token;
        setRestartingEngine(true);
        try {
            const response = await axios.post(`/api/system/containers/ai_engine/restart?force=${force}`, {}, {
                headers: { Authorization: `Bearer ${token}` }
            });

            if (!ownsView()) return;
            if (response.data.status === 'warning') {
                const confirmForce = await confirm({
                    title: 'Force Restart?',
                    description: `${response.data.message}\n\nDo you want to force restart anyway? This may disconnect active calls.`,
                    confirmText: 'Force Restart',
                    variant: 'destructive'
                });
                if (confirmForce && ownsView()) {
                    setRestartingEngine(false);
                    return handleRestartAIEngine(true);
                }
                return;
            }

            if (response.data.status === 'degraded') {
                toast.warning('AI Engine restarted but may not be fully healthy', { description: response.data.output || 'Please verify manually' });
                return;
            }

            await refetch();
            if (ownsView()) toast.success('AI Engine restarted! Changes are now active.');
        } catch (error: any) {
            if (ownsView()) toast.error('Failed to restart AI Engine', { description: error.response?.data?.detail || error.message });
        } finally {
            if (isMounted()) {
                setRestartingEngine(false);
                if (!ownsView()) {
                    setApplyStatus(UNKNOWN_APPLY_STATUS);
                    setApplyUncertain(true);
                }
            }
        }
    };

    const handleApplyAIEngine = async () => {
        const ownsView = () => isMounted() && currentToken.current === token;
        if (applyUncertain || checkingApplyStatus) return;
        if (recommendedApplyMethod !== 'hot_reload') {
            await handleRestartAIEngine(false);
            return;
        }
        setRestartingEngine(true);
        try {
            const response = await axios.post('/api/system/containers/ai_engine/reload', {}, {
                headers: { Authorization: `Bearer ${token}` },
                timeout: 30000,
            });
            if (!ownsView()) return;
            if (response.data?.restart_required) {
                setApplyStatus('Hot reload applied partially. Restart the AI Engine to finish applying changes.');
                toast.warning('Restart still required', { description: response.data?.message });
            } else {
                const generation = response.data?.tool_generation;
                const message = `Applied${generation ? ` as tool generation ${generation}` : ''}. New calls use it; active calls remain unchanged.`;
                setApplyStatus(message);
                toast.success('Changes applied', { description: message });
            }
            await refetch();
        } catch (error: any) {
            if (ownsView()) {
                setApplyStatus(UNKNOWN_APPLY_STATUS);
                setApplyUncertain(true);
                toast.warning('Apply outcome could not be confirmed', { description: error.response?.data?.detail || error.message });
                await refreshApplyStatus();
            }
        } finally {
            if (isMounted()) {
                setRestartingEngine(false);
                if (!ownsView()) {
                    setApplyStatus(UNKNOWN_APPLY_STATUS);
                    setApplyUncertain(true);
                }
            }
        }
    };

    const mergeToolsConfig = (baseConfig: any, newToolsConfig: any) => {
        // Extract root-level settings that should not be nested under tools
        const {
            farewell_hangup_delay_sec,
            on_provider_failure,
            provider_failure_prompt,
            provider_failure_redirect_context,
            provider_failure_redirect_extension,
            provider_failure_redirect_priority,
            ...toolsOnly
        } = newToolsConfig;

        // P1 Fix: Preserve ALL existing tool entries that are not being explicitly updated.
        // This prevents silent config loss of custom/unknown tool entries.
        // Built-in tools that ToolForm manages: transfer, hangup_call, leave_voicemail, 
        // send_email_summary, request_transcript
        const builtInToolKeys = ['transfer', 'attended_transfer', 'cancel_transfer', 'hangup_call', 'leave_voicemail', 'send_email_summary', 'request_transcript', 'google_calendar', 'microsoft_calendar', 'calendly_calendar'];
        const existingTools = baseConfig.tools || {};
        const preservedTools: Record<string, any> = {};

        Object.entries(existingTools).forEach(([k, v]) => {
            // Preserve if:
            // 1. It's a phase-based HTTP tool (has kind and phase)
            // 2. It's NOT a built-in tool that ToolForm manages (those get updated from toolsOnly)
            const isPhaseHttpTool = v && typeof v === 'object' && (v as any).kind && (v as any).phase;
            const isBuiltInTool = builtInToolKeys.includes(k);

            if (isPhaseHttpTool || !isBuiltInTool) {
                // Only preserve if not being explicitly set in toolsOnly
                if (!(k in toolsOnly)) {
                    preservedTools[k] = v;
                }
            }
        });

        // Update both tools config and root-level call-behavior settings
        const updatedConfig = { ...baseConfig, tools: { ...preservedTools, ...toolsOnly } };
        if (farewell_hangup_delay_sec !== undefined) {
            updatedConfig.farewell_hangup_delay_sec = farewell_hangup_delay_sec;
        }
        if (on_provider_failure !== undefined) {
            updatedConfig.on_provider_failure = on_provider_failure;
        }
        if (provider_failure_prompt !== undefined) {
            updatedConfig.provider_failure_prompt = provider_failure_prompt;
        }
        if (provider_failure_redirect_context !== undefined) {
            updatedConfig.provider_failure_redirect_context = provider_failure_redirect_context;
        }
        if (provider_failure_redirect_extension !== undefined) {
            updatedConfig.provider_failure_redirect_extension = provider_failure_redirect_extension;
        }
        if (provider_failure_redirect_priority !== undefined) {
            updatedConfig.provider_failure_redirect_priority = provider_failure_redirect_priority;
        }
        return updatedConfig;
    };

    const updateToolsConfig = (newToolsConfig: any) => {
        setConfig((prev: any) => mergeToolsConfig(prev, newToolsConfig));
    };

    const updateToolsConfigAndSaveNow = async (newToolsConfig: any) => {
        if (applyUncertain || checkingApplyStatus) throw new Error('Refresh status before saving tool settings.');
        const nextConfig = mergeToolsConfig(configRef.current, newToolsConfig);
        setConfig(nextConfig);
        await persistConfigNow(nextConfig);
    };

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading configuration...</div>;
    if (loadError) return (
        <div role="alert" className="space-y-4 p-6">
            <p>{loadError}</p>
            <button type="button" onClick={() => fetchConfig(true)} className="px-3 py-2 rounded border border-border">Retry loading tools</button>
        </div>
    );
    if (yamlError) {
        return (
            <div className="space-y-4 p-6">
                <YamlErrorBanner error={yamlError} />
                <div className="flex items-center justify-between rounded-md border border-red-500/30 bg-red-500/10 p-4 text-red-700 dark:text-red-400">
                    <div className="flex items-center">
                        <AlertCircle className="mr-2 h-5 w-5" />
                        Tools editing is disabled while `config/ai-agent.yaml` has YAML errors. Fix the YAML and reload.
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

    return (
        <div className="space-y-6">
            {(applyRequired || restartRequired) && (
                <div className="bg-orange-500/15 border-orange-500/30 border text-yellow-800 dark:text-yellow-500 p-4 rounded-md flex items-center justify-between">
                    <div className="flex items-center">
                        <AlertCircle className="w-5 h-5 mr-2" />
                        <span>
                            {recommendedApplyMethod === 'hot_reload'
                                ? 'Saved tool changes are ready to apply to new calls. Active calls will remain unchanged.'
                                : 'Some saved configuration changes require an AI Engine restart to take effect.'}
                            {stateStale && ' Status refresh failed; showing the last known action.'}
                        </span>
                    </div>
                    <button
                        onClick={handleApplyAIEngine}
                        disabled={restartingEngine || saving || applyUncertain || checkingApplyStatus}
                        className="flex items-center text-xs px-3 py-1.5 rounded transition-colors bg-orange-500 text-white hover:bg-orange-600 font-medium disabled:opacity-50"
                    >
                        {restartingEngine ? (
                            <Loader2 className="w-3 h-3 mr-1.5 animate-spin" />
                        ) : (
                            <RefreshCw className="w-3 h-3 mr-1.5" />
                        )}
                        {restartingEngine
                            ? (recommendedApplyMethod === 'hot_reload' ? 'Applying...' : 'Restarting...')
                            : (recommendedApplyMethod === 'hot_reload' ? 'Apply Changes' : 'Restart AI Engine')}
                    </button>
                </div>
            )}
            {stateStale && !applyRequired && !restartRequired && (
                <div className="bg-yellow-500/10 border-yellow-500/30 border text-yellow-800 dark:text-yellow-500 p-3 rounded-md flex items-center">
                    <AlertCircle className="w-5 h-5 mr-2" />
                    Unable to refresh the AI Engine apply status. No pending action is known; retry after connectivity recovers.
                </div>
            )}
            {applyStatus && (
                <div className="rounded-md border border-border bg-muted/30 px-4 py-3 text-sm text-muted-foreground">
                    <span role="status">{applyStatus}</span>
                    {applyUncertain && <button type="button" disabled={checkingApplyStatus || saving || restartingEngine} onClick={() => void refreshApplyStatus()} className="ml-3 px-3 py-1 border rounded disabled:opacity-50">
                        {checkingApplyStatus ? 'Checking status…' : 'Refresh status'}
                    </button>}
                </div>
            )}
            <div hidden={!!selectedSection || httpEditing || activePhase === 'catalog'} className="space-y-6">
                <div className="flex flex-wrap justify-between items-start gap-4">
                    <div>
                        <h1 className="text-3xl font-bold tracking-tight">Tools</h1>
                        <p className="text-muted-foreground mt-1">Manage what your agents can do.</p>
                    </div>
                    <div className="flex gap-4 items-center">
                        <button type="button" onClick={() => selectSection('defaults')} className="text-sm text-muted-foreground hover:text-foreground">Tool defaults</button>
                        <button type="button" onClick={() => setActivePhase('catalog')} className="inline-flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm hover:bg-accent"><BookOpen className="h-4 w-4" /> Catalog</button>
                    </div>
                </div>
                <div className="flex gap-6 border-b border-border" aria-label="Call phase">
                    {(['pre_call', 'in_call', 'post_call'] as const).map(phase => (
                        <button type="button" key={phase} aria-pressed={activePhase === phase} onClick={() => setActivePhase(phase)} className={`pb-3 text-sm border-b-2 ${activePhase === phase ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground'}`}>
                            {{ pre_call: 'Pre-Call', in_call: 'In-Call', post_call: 'Post-Call' }[phase]}
                        </button>
                    ))}
                </div>
                <div className="relative">
                    <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" aria-hidden="true" />
                    <input aria-label="Search tools" placeholder="Search tools…" value={query} onChange={event => setQuery(event.target.value)} className="w-full rounded-md border border-input bg-background pl-9 pr-3 py-2 text-sm" />
                </div>
                {activePhase === 'in_call' && <ToolList config={config.tools || {}} query={query} onSelect={selectSection} />}
            </div>

            <div hidden={!selectedSection}>
                <button type="button" onClick={() => setSelectedSection(null)} className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground mb-5"><ArrowLeft className="h-4 w-4" /> Back to tools</button>
                <h1 ref={headingRef} tabIndex={-1} className="text-2xl font-semibold tracking-tight">{selectedTool?.title}</h1>
                <p className="text-sm text-muted-foreground mt-2 mb-6">{selectedTool?.description}. Agent access is managed in Agents.</p>
                <fieldset disabled={saving} className="min-w-0">
                    <ToolForm
                        section={selectedSection ?? 'defaults'}
                        config={{
                            ...(config.tools || {}),
                            farewell_hangup_delay_sec: config.farewell_hangup_delay_sec,
                            on_provider_failure: config.on_provider_failure,
                            provider_failure_prompt: config.provider_failure_prompt,
                            provider_failure_redirect_context: config.provider_failure_redirect_context,
                            provider_failure_redirect_extension: config.provider_failure_redirect_extension,
                            provider_failure_redirect_priority: config.provider_failure_redirect_priority,
                        }}
                        contexts={config.contexts || {}}
                        hangupUsage={hangupUsage}
                        onChange={updateToolsConfig}
                        onContextsChange={newContexts => setConfig((prev: any) => ({ ...prev, contexts: newContexts }))}
                        onSaveNow={updateToolsConfigAndSaveNow}
                    />
                </fieldset>
            </div>

            {(['pre_call', 'in_call', 'post_call'] as const).map(phase => (
                <div key={phase} hidden={!!selectedSection || activePhase !== phase}>
                    <fieldset disabled={saving} className="min-w-0">
                        <HTTPToolForm
                            presentation="page"
                            onEditingChange={setHttpEditing}
                            query={query}
                            config={(phase === 'in_call' ? config.in_call_tools : config.tools) || {}}
                            onChange={newTools => setConfig((prev: any) => ({ ...prev, [phase === 'in_call' ? 'in_call_tools' : 'tools']: newTools }))}
                            phase={phase}
                            contexts={config.contexts}
                        />
                    </fieldset>
                </div>
            ))}
            {!selectedSection && !httpEditing && activePhase !== 'catalog' && <p className="text-xs text-muted-foreground">Configure tools here. Choose which tools each agent can use in Agents.</p>}
            {activePhase === 'catalog' && <button type="button" onClick={() => setActivePhase('in_call')} className="inline-flex items-center gap-2 text-sm text-muted-foreground"><ArrowLeft className="h-4 w-4" /> Back to tools</button>}
            {activePhase === 'catalog' && (
                <ConfigSection
                    title="Tool Catalog (Read-only)"
                    description="Reference for all tools currently registered in the AI Engine, including built-in, HTTP, and MCP tools. This reflects tool schemas and descriptions, not every runtime config option or per-tool setting."
                >
                    <ConfigCard>
                        <div className="space-y-4">
                            <div className="flex flex-col md:flex-row md:items-center gap-3">
                                <div className="flex-1 relative">
                                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                                    <input
                                        type="text"
                                        className="w-full pl-10 pr-3 py-2 rounded border border-input bg-background text-sm"
                                        placeholder="Search tools (name, description, phase, source)"
                                        value={toolCatalogQuery}
                                        onChange={(e) => setToolCatalogQuery(e.target.value)}
                                    />
                                </div>
                                <button
                                    type="button"
                                    onClick={fetchToolCatalog}
                                    disabled={toolCatalogLoading}
                                    className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2"
                                >
                                    {toolCatalogLoading ? (
                                        <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                                    ) : (
                                        <RefreshCw className="w-4 h-4 mr-2" />
                                    )}
                                    Refresh
                                </button>
                            </div>

                            {toolCatalogError && (
                                <div className="rounded-md border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-700 dark:text-red-400">
                                    {toolCatalogError}
                                </div>
                            )}

                            <div className="overflow-x-auto border border-border rounded-md">
                                <table className="w-full text-sm">
                                    <thead className="bg-secondary/40 text-muted-foreground">
                                        <tr>
                                            <th className="text-left px-3 py-2 w-10"></th>
                                            <th className="text-left px-3 py-2">Tool</th>
                                            <th className="text-left px-3 py-2">Phase</th>
                                            <th className="text-left px-3 py-2">Source</th>
                                            <th className="text-left px-3 py-2">Description</th>
                                            <th className="text-left px-3 py-2 w-16">Params</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {(() => {
                                            const q = toolCatalogQuery.trim().toLowerCase();
                                            const filtered = (toolCatalog || [])
                                                .filter((t) => {
                                                    if (!q) return true;
                                                    const hay = [
                                                        t.name,
                                                        t.description,
                                                        t.phase,
                                                        t.source,
                                                        t.category,
                                                    ]
                                                        .filter(Boolean)
                                                        .join(' ')
                                                        .toLowerCase();
                                                    return hay.includes(q);
                                                })
                                                .sort((a, b) => a.name.localeCompare(b.name));

                                            if (toolCatalogLoading && filtered.length === 0) {
                                                return (
                                                    <tr>
                                                        <td className="px-3 py-3" colSpan={6}>
                                                            <div className="flex items-center text-muted-foreground">
                                                                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                                                                Loading tool catalog...
                                                            </div>
                                                        </td>
                                                    </tr>
                                                );
                                            }

                                            if (filtered.length === 0) {
                                                return (
                                                    <tr>
                                                        <td className="px-3 py-3 text-muted-foreground" colSpan={6}>
                                                            No tools match this search.
                                                        </td>
                                                    </tr>
                                                );
                                            }

                                            return filtered.flatMap((t) => {
                                                const expanded = !!toolCatalogExpanded[t.name];
                                                const params = Array.isArray(t.parameters) ? t.parameters : [];
                                                return [
                                                    (
                                                        <tr key={t.name} className="border-t border-border align-top">
                                                            <td className="px-3 py-2">
                                                                <button
                                                                    type="button"
                                                                    className="text-muted-foreground hover:text-foreground"
                                                                    onClick={() => setToolCatalogExpanded((prev) => ({ ...prev, [t.name]: !expanded }))}
                                                                    aria-label={expanded ? 'Collapse tool details' : 'Expand tool details'}
                                                                >
                                                                    {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                                                                </button>
                                                            </td>
                                                            <td className="px-3 py-2 font-medium text-foreground whitespace-nowrap">
                                                                {t.name}
                                                                {t.is_global ? (
                                                                    <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded border border-border bg-secondary/40 text-muted-foreground">
                                                                        global
                                                                    </span>
                                                                ) : null}
                                                            </td>
                                                            <td className="px-3 py-2 text-muted-foreground whitespace-nowrap">{t.phase || '-'}</td>
                                                            <td className="px-3 py-2 text-muted-foreground whitespace-nowrap">{t.source || 'unknown'}</td>
                                                            <td className="px-3 py-2 text-foreground/90">{t.description || '-'}</td>
                                                            <td className="px-3 py-2 text-muted-foreground text-right">{params.length}</td>
                                                        </tr>
                                                    ),
                                                    expanded ? (
                                                        <tr key={`${t.name}-details`} className="border-t border-border bg-secondary/20">
                                                            <td className="px-3 py-2" colSpan={6}>
                                                                {params.length === 0 ? (
                                                                    <div className="text-xs text-muted-foreground">No parameters.</div>
                                                                ) : (
                                                                    <div className="space-y-2">
                                                                        <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Parameters</div>
                                                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                                                                            {params.map((p) => (
                                                                                <div key={`${t.name}-${p.name}`} className="rounded border border-border bg-background/60 p-2">
                                                                                    <div className="flex items-center justify-between">
                                                                                        <div className="text-xs font-medium text-foreground">
                                                                                            {p.name}{p.required ? <span className="ml-1 text-red-500">*</span> : null}
                                                                                        </div>
                                                                                        <div className="text-[10px] text-muted-foreground">{p.type}</div>
                                                                                    </div>
                                                                                    <div className="text-xs text-muted-foreground mt-1">{p.description || '-'}</div>
                                                                                </div>
                                                                            ))}
                                                                        </div>
                                                                    </div>
                                                                )}
                                                            </td>
                                                        </tr>
                                                    ) : null,
                                                ].filter(Boolean) as any;
                                            });
                                        })()}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </ConfigCard>
                </ConfigSection>
            )}
            {(dirty || selectedSection || applyStatus) && activePhase !== 'catalog' && !httpEditing && (
                <div className="sticky bottom-0 z-10 flex flex-wrap items-center justify-between gap-3 border-t border-border bg-background py-4" role="region" aria-label="Save tool changes">
                    <span className="text-sm text-muted-foreground" role="status">{saving ? 'Saving…' : dirty ? 'Unsaved changes' : 'Save checks whether a reload or restart is needed.'}</span>
                    {/* Keep Save available for fields that commit their local draft on blur. */}
                    <button type="button" onClick={handleSave} disabled={saving || restartingEngine || applyUncertain || checkingApplyStatus} className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">
                        <Save className="h-4 w-4" /> {saving ? 'Saving…' : 'Save & Apply'}
                    </button>
                </div>
            )}
        </div>
    );
};

export default ToolsPage;
