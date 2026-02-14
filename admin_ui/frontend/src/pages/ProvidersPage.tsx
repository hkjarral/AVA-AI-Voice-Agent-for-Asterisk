import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useConfirmDialog } from '../hooks/useConfirmDialog';
import yaml from 'js-yaml';
import { sanitizeConfigForSave } from '../utils/configSanitizers';
import { Plus, Settings, Trash2, Server, AlertCircle, CheckCircle2, Loader2, RefreshCw, Wand2, Star } from 'lucide-react';
import { YamlErrorBanner, YamlErrorInfo } from '../components/ui/YamlErrorBanner';
import { ConfigSection } from '../components/ui/ConfigSection';
import { ConfigCard } from '../components/ui/ConfigCard';
import { Modal } from '../components/ui/Modal';

// Provider Forms
import GenericProviderForm from '../components/config/providers/GenericProviderForm';
import LocalProviderForm from '../components/config/providers/LocalProviderForm';
import OllamaProviderForm from '../components/config/providers/OllamaProviderForm';
import OpenAIRealtimeProviderForm from '../components/config/providers/OpenAIRealtimeProviderForm';
import DeepgramProviderForm from '../components/config/providers/DeepgramProviderForm';
import GoogleLiveProviderForm from '../components/config/providers/GoogleLiveProviderForm';
import OpenAIProviderForm from '../components/config/providers/OpenAIProviderForm';
import ElevenLabsProviderForm from '../components/config/providers/ElevenLabsProviderForm';
import TelnyxProviderForm from '../components/config/providers/TelnyxProviderForm';
import { Capability, capabilityFromKey, ensureModularKey, isFullAgentProvider } from '../utils/providerNaming';
import { GOOGLE_LIVE_DEFAULT_MODEL } from '../utils/googleLiveModels';

const stripModularSuffix = (name: string): string => (name || '').replace(/_(stt|llm|tts)$/i, '');

const ProvidersPage: React.FC = () => {
    const { confirm } = useConfirmDialog();
    const [config, setConfig] = useState<any>({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [yamlError, setYamlError] = useState<YamlErrorInfo | null>(null);
    const [editingProvider, setEditingProvider] = useState<string | null>(null);
    const [providerForm, setProviderForm] = useState<any>({});
    const [isNewProvider, setIsNewProvider] = useState(false);
    const [testingProvider, setTestingProvider] = useState<string | null>(null);
    const [testResults, setTestResults] = useState<{ [key: string]: { success: boolean; message: string } | undefined }>({});
    const [showAddProvidersModal, setShowAddProvidersModal] = useState(false);
    const [selectedTemplates, setSelectedTemplates] = useState<string[]>([]);
    const [pendingRestart, setPendingRestart] = useState(false);
    const [restartingEngine, setRestartingEngine] = useState(false);

    useEffect(() => {
        fetchConfig();
    }, []);

    const fetchConfig = async () => {
        try {
            const res = await axios.get('/api/config/yaml');
            if (res.data.yaml_error) {
                setYamlError(res.data.yaml_error);
                setConfig({});
                setError(null);
            } else {
                const parsed = yaml.load(res.data.content) as any;
                setConfig(parsed || {});
                setError(null);
                setYamlError(null);
            }
        } catch (err) {
            console.error('Failed to load config', err);
            const status = (err as any)?.response?.status;
            if (status === 401) {
                setError('Not authenticated. Please refresh and log in again.');
            } else {
                setError('Failed to load configuration. Check backend logs and try again.');
            }
            setYamlError(null);
        } finally {
            setLoading(false);
        }
    };

    const normalizeProviderCapabilities = (nextConfig: any) => {
        const providers = nextConfig?.providers || {};
        const normalizedProviders: Record<string, any> = { ...providers };

        Object.entries(providers).forEach(([providerKey, providerData]) => {
            if (!providerData || typeof providerData !== 'object' || Array.isArray(providerData)) return;
            // Only auto-fill for modular providers.
            if (isFullAgentProvider(providerData)) return;

            const caps = Array.isArray((providerData as any).capabilities) ? (providerData as any).capabilities : [];
            if (caps.length > 0) return;

            const inferred = capabilityFromKey(providerKey);
            if (!inferred) return;

            normalizedProviders[providerKey] = {
                ...providerData,
                capabilities: [inferred],
            };
        });

        return { ...nextConfig, providers: normalizedProviders };
    };

    const saveConfig = async (newConfig: any) => {
        try {
            const normalized = normalizeProviderCapabilities(newConfig);
            const sanitized = sanitizeConfigForSave(normalized);
            await axios.post('/api/config/yaml', { content: yaml.dump(sanitized) });
            setConfig(sanitized);
            setPendingRestart(true);
        } catch (err) {
            console.error('Failed to save config', err);
            toast.error('Failed to save configuration');
        }
    };

    const handleEditProvider = (name: string) => {
        setEditingProvider(name);
        const providerData = { ...(config.providers?.[name] || {}) };

        if (!providerData.type) {
            if (isFullAgentProvider(providerData)) {
                providerData.type = 'full';
            } else {
                const lowerName = name.toLowerCase();
                if (lowerName.includes('openai')) providerData.type = 'openai';
                else if (lowerName.includes('deepgram')) providerData.type = 'deepgram';
                else if (lowerName.includes('google') || lowerName.includes('gemini')) providerData.type = 'google_live';
                else if (lowerName.includes('elevenlabs')) providerData.type = 'elevenlabs_agent';
                else if (lowerName.includes('ollama')) providerData.type = 'ollama';
                else if (lowerName.includes('local')) providerData.type = 'local';
                else providerData.type = 'other';
            }
        }

        // Legacy migration: if capabilities are missing for a modular provider, infer from suffix for UX.
        if (!isFullAgentProvider(providerData)) {
            const caps = Array.isArray(providerData.capabilities) ? providerData.capabilities : [];
            if (caps.length === 0) {
                const inferred = capabilityFromKey(name);
                if (inferred) {
                    providerData.capabilities = [inferred];
                }
            }
        }

        setProviderForm({ ...providerData, name });
        setIsNewProvider(false);
    };

    const handleAddProvider = () => {
        setEditingProvider('new');
        setProviderForm({
            name: '',
            type: 'full',
            capabilities: ['stt', 'llm', 'tts'],
            enabled: true,
            base_url: ''
        });
        setIsNewProvider(true);
    };

    const handleOpenAddProvidersModal = () => {
        setSelectedTemplates([]);
        setShowAddProvidersModal(true);
    };

    const handleAddSelectedProviders = async () => {
        if (selectedTemplates.length === 0) {
            toast.error('Please select at least one provider template.');
            return;
        }

        const current = config.providers || {};
        const nextProviders = { ...current };
        let changed = false;

        // Provider templates - added DISABLED so user must configure and enable
        const templates: Record<string, any> = {
            openai_realtime: {
                enabled: false,
                api_version: 'beta',
                model: 'gpt-4o-realtime-preview-2024-12-17',
                voice: 'alloy',
                input_encoding: 'ulaw',
                input_sample_rate_hz: 8000,
                target_encoding: 'mulaw',
                target_sample_rate_hz: 8000,
                greeting: 'Hello, how can I help you today?',
                instructions: 'You are a helpful AI assistant.',
                turn_detection: { type: 'server_vad', threshold: 0.5, silence_duration_ms: 1000 }
            },
            deepgram: {
                enabled: false,
                model: 'nova-2-phonecall',
                tts_model: 'aura-2-thalia-en',
                input_encoding: 'mulaw',
                input_sample_rate_hz: 8000,
                output_encoding: 'mulaw',
                output_sample_rate_hz: 8000,
                greeting: 'Hello, how can I help you today?',
                instructions: 'You are a helpful AI assistant.'
            },
            google_live: {
                enabled: false,
                type: 'full',
                capabilities: ['stt', 'llm', 'tts'],
                api_key: '${GOOGLE_API_KEY}',
                llm_model: GOOGLE_LIVE_DEFAULT_MODEL,
                input_encoding: 'ulaw',
                input_sample_rate_hz: 8000,
                target_encoding: 'ulaw',
                target_sample_rate_hz: 8000,
                greeting: 'Hello, how can I help you today?',
                instructions: 'You are a helpful AI assistant.'
            },
            elevenlabs_agent: {
                enabled: false,
                type: 'full',
                capabilities: ['stt', 'llm', 'tts'],
                api_key: '${ELEVENLABS_API_KEY}',
                agent_id: '${ELEVENLABS_AGENT_ID}',
                input_encoding: 'ulaw',
                input_sample_rate_hz: 8000,
                target_encoding: 'ulaw',
                target_sample_rate_hz: 8000
            },
            local_modular: {
                // This adds local_stt, local_llm, local_tts
                local_stt: { type: 'local', capabilities: ['stt'], enabled: false, ws_url: '${LOCAL_WS_URL:-ws://127.0.0.1:8765}', auth_token: '${LOCAL_WS_AUTH_TOKEN:-}' },
                local_llm: { type: 'local', capabilities: ['llm'], enabled: false, auth_token: '${LOCAL_WS_AUTH_TOKEN:-}' },
                local_tts: { type: 'local', capabilities: ['tts'], enabled: false, ws_url: '${LOCAL_WS_URL:-ws://127.0.0.1:8765}', auth_token: '${LOCAL_WS_AUTH_TOKEN:-}' }
            },
            telnyx_llm: {
                enabled: false,
                type: 'telnyx',
                capabilities: ['llm'],
                chat_base_url: 'https://api.telnyx.com/v2/ai',
                api_key: '${TELNYX_API_KEY}',
                chat_model: 'Qwen/Qwen3-235B-A22B',
                temperature: 0.7,
                response_timeout_sec: 30.0,
            }
        };

        selectedTemplates.forEach(templateKey => {
            if (templateKey === 'local_modular') {
                // Add multiple providers for local modular
                const localProviders = templates.local_modular;
                Object.entries(localProviders).forEach(([key, value]) => {
                    if (!nextProviders[key]) {
                        nextProviders[key] = value;
                        changed = true;
                    }
                });
            } else if (!nextProviders[templateKey]) {
                nextProviders[templateKey] = templates[templateKey];
                changed = true;
            }
        });

        if (!changed) {
            toast.info('Selected providers already exist.');
            setShowAddProvidersModal(false);
            return;
        }

        await saveConfig({ ...config, providers: nextProviders });
        setShowAddProvidersModal(false);
        setPendingRestart(true);
    };

    const handleSetAsDefault = async (name: string) => {
        const newConfig = { ...config };
        newConfig.default_provider = name;
        // Clear active_pipeline for full agents
        if (isFullAgentProvider(config.providers?.[name])) {
            newConfig.active_pipeline = null;
        }
        // Auto-enable the provider when setting as default
        if (newConfig.providers?.[name]) {
            newConfig.providers[name].enabled = true;
        }
        await saveConfig(newConfig);
        setPendingRestart(true);
    };

    const handleReloadAIEngine = async (force: boolean = false) => {
        setRestartingEngine(true);
        try {
            // Provider changes may require new env vars - use restart to ensure they're picked up
            const response = await axios.post(`/api/system/containers/ai_engine/restart?force=${force}`);

            if (response.data.status === 'warning') {
                const confirmForce = await confirm({
                    title: 'Force Restart?',
                    description: `${response.data.message}\n\nDo you want to force restart anyway? This may disconnect active calls.`,
                    confirmText: 'Force Restart',
                    variant: 'destructive'
                });
                if (confirmForce) {
                    await handleReloadAIEngine(true);
                    return;
                }
                return;
            }

            if (response.data.status === 'degraded') {
                toast.warning('AI Engine restarted but may not be fully healthy', { description: response.data.output || 'Please verify manually' });
                return;
            }

            if (response.data.status === 'success') {
                setPendingRestart(false);
                toast.success('AI Engine restarted! Changes are now active.');
            }
        } catch (error: any) {
            toast.error('Failed to restart AI Engine', { description: error.response?.data?.detail || error.message });
        } finally {
            setRestartingEngine(false);
        }
    };

    const handleDeleteProvider = async (name: string) => {
        // P1 Guard: Check if this is the default provider
        if (config.default_provider === name) {
            toast.error(`Cannot delete provider "${name}"`, { description: 'Please set a different default provider first.' });
            return;
        }

        // Check pipeline usage
        const pipelines = config.pipelines || {};
        const inUsePipelines = Object.entries(pipelines).filter(([_, p]: [string, any]) => p.stt === name || p.llm === name || p.tts === name);
        
        // P1 Guard: Block if used by active pipeline
        const activePipeline = config.active_pipeline;
        if (activePipeline && pipelines[activePipeline]) {
            const ap = pipelines[activePipeline] as any;
            if (ap.stt === name || ap.llm === name || ap.tts === name) {
                toast.error(`Cannot delete provider "${name}"`, {
                    description: `This provider is used by the active pipeline "${activePipeline}". Please update the active pipeline first.`
                });
                return;
            }
        }

        // P1 Guard: Check context provider overrides
        const contexts = config.contexts || {};
        const usingContexts = Object.entries(contexts)
            .filter(([_, ctx]) => (ctx as any).provider === name)
            .map(([ctxName]) => ctxName);

        // Build warning message with all impacts
        const warnings: string[] = [];
        if (inUsePipelines.length > 0) {
            warnings.push(`Used by pipelines: ${inUsePipelines.map(([n]) => n).join(', ')}`);
        }
        if (usingContexts.length > 0) {
            warnings.push(`Used by contexts (provider override): ${usingContexts.join(', ')}`);
        }

        if (warnings.length > 0) {
            const warningMsg = `Provider "${name}" has the following dependencies:\n\n• ${warnings.join('\n• ')}\n\nDeleting may break calls.`;
            const confirmed = await confirm({
                title: 'Delete Provider?',
                description: warningMsg,
                confirmText: 'Delete',
                variant: 'destructive'
            });
            if (!confirmed) return;
        } else {
            const confirmed = await confirm({
                title: 'Delete Provider?',
                description: `Are you sure you want to delete provider "${name}"?`,
                confirmText: 'Delete',
                variant: 'destructive'
            });
            if (!confirmed) return;
        }

        const newProviders = { ...(config.providers || {}) };
        delete newProviders[name];
        await saveConfig({ ...config, providers: newProviders });
    };

    const handleToggleProvider = async (name: string, providerData: any, newEnabled: boolean) => {
        // P1 Guard: Warn/block disabling a provider used by active pipeline
        if (!newEnabled) {
            const pipelines = config.pipelines || {};
            const activePipeline = config.active_pipeline;
            
            if (activePipeline && pipelines[activePipeline]) {
                const ap = pipelines[activePipeline] as any;
                if (ap.stt === name || ap.llm === name || ap.tts === name) {
                    const role = ap.stt === name ? 'STT' : ap.llm === name ? 'LLM' : 'TTS';
                    toast.error(`Cannot disable provider "${name}"`, { description: `It is the ${role} provider for the active pipeline "${activePipeline}". Please update the active pipeline first.` });
                    return;
                }
            }

            // Check if it's the default provider
            if (config.default_provider === name) {
                toast.error(`Cannot disable provider "${name}"`, { description: 'Please set a different default provider first.' });
                return;
            }
        }

        const newProviders = { ...config.providers };
        newProviders[name] = { ...providerData, enabled: newEnabled };
        await saveConfig({ ...config, providers: newProviders });
    };

    const handleSaveProvider = async () => {
        if (!providerForm.name) {
            toast.error('Provider name is required.');
            return;
        }

        const isFull = isFullAgentProvider(providerForm);
        let finalName = (providerForm.name || '').toLowerCase();
        let capabilities = Array.isArray(providerForm.capabilities) ? providerForm.capabilities : [];

        if (!isFull) {
            // Capabilities are authoritative when present. If missing, infer from suffix for existing legacy configs
            // and persist to YAML. New modular providers must select a capability explicitly.
            let cap: Capability | null = (capabilities.length === 1) ? (capabilities[0] as Capability) : null;
            const inferred = capabilityFromKey(finalName);

            if (!cap) {
                if (!isNewProvider && inferred) {
                    cap = inferred;
                    capabilities = [cap];
                } else {
                    toast.error('Capability is required for modular providers. Select STT, LLM, or TTS.');
                    return;
                }
            }

            finalName = ensureModularKey(stripModularSuffix(finalName), cap);
            capabilities = [cap];
        } else {
            capabilities = ['stt', 'llm', 'tts'];
        }

        const providerKey = isNewProvider ? finalName : editingProvider;
        if (!providerKey) return;

        const newConfig = { ...config };
        if (!newConfig.providers) newConfig.providers = {};

        if ((isNewProvider || editingProvider !== finalName) && newConfig.providers[finalName]) {
            toast.error(`Provider "${finalName}" already exists.`);
            return;
        }

        const existingData = !isNewProvider && editingProvider ? (config.providers?.[editingProvider] || {}) : {};
        const providerData = { ...existingData, ...providerForm, name: finalName, capabilities };

        // Telnyx LLM defaults: ensure the values shown in the form are actually persisted to YAML.
        // Without this, the form may display placeholders while the YAML remains unset, causing ai_engine
        // to fall back to its internal defaults (which can differ across releases).
        try {
            const providerType = String(providerData.type || '').toLowerCase();
            const isTelnyx = providerType === 'telnyx' || providerType === 'telenyx' || finalName.includes('telnyx') || finalName.includes('telenyx');
            const isLLMOnly = Array.isArray(providerData.capabilities) && providerData.capabilities.length === 1 && providerData.capabilities[0] === 'llm';
            if (isTelnyx && isLLMOnly) {
                if (!providerData.chat_base_url) providerData.chat_base_url = 'https://api.telnyx.com/v2/ai';
                if (!providerData.chat_model) providerData.chat_model = 'Qwen/Qwen3-235B-A22B';
                if (providerData.temperature === undefined || providerData.temperature === null) providerData.temperature = 0.7;
                if (!providerData.response_timeout_sec) providerData.response_timeout_sec = 30.0;
            }
        } catch {
            // Non-blocking defaults
        }

        if (!isFull && providerData.capabilities.length !== 1) {
            toast.error('Modular providers must have exactly one capability.');
            return;
        }

        if (!isFull && !providerData.capabilities[0]) {
            toast.error('Capability is required for modular providers.');
            return;
        }

        if (!isFull) {
            const cap = providerData.capabilities[0];
            providerData.name = ensureModularKey(stripModularSuffix(providerData.name), cap);
        }

        if (!isNewProvider && editingProvider && editingProvider !== finalName) {
            delete newConfig.providers[editingProvider];
            if (newConfig.pipelines) {
                Object.entries(newConfig.pipelines).forEach(([pipelineName, pipeline]: [string, any]) => {
                    const updated = { ...pipeline };
                    let changed = false;
                    (['stt', 'llm', 'tts'] as const).forEach(role => {
                        if (updated[role] === editingProvider) {
                            updated[role] = finalName;
                            changed = true;
                        }
                    });
                    if (changed) newConfig.pipelines[pipelineName] = updated;
                });
            }
        }

        newConfig.providers[finalName] = providerData;

        await saveConfig(newConfig);
        setEditingProvider(null);
    };

    const handleTestConnection = async (name: string, providerData: any) => {
        setTestingProvider(name);
        setTestResults(prev => ({ ...prev, [name]: undefined }));
        try {
            const response = await axios.post('/api/config/providers/test', { name, config: providerData });
            setTestResults(prev => ({
                ...prev,
                [name]: { success: response.data.success, message: response.data.message || 'Connection successful!' }
            }));
        } catch (err: any) {
            setTestResults(prev => ({
                ...prev,
                [name]: { success: false, message: err.response?.data?.detail || 'Connection failed' }
            }));
        } finally {
            setTestingProvider(null);
        }
    };

    const handleSetModularCapability = (cap: Capability) => {
        const rawName = (providerForm.name || '').toLowerCase();
        if (!rawName.trim()) {
            toast.error('Please enter a provider name before selecting a capability.');
            return;
        }
        const normalizedName = ensureModularKey(stripModularSuffix(rawName), cap);
        setProviderForm({ ...providerForm, name: normalizedName, capabilities: [cap] });
    };

    const renderProviderForm = () => {
        const updateForm = (newValues: any) => setProviderForm({ ...providerForm, ...newValues });

        // Check provider name for specific forms, fallback to type
        const providerName = (providerForm.name || '').toLowerCase();
        
        // Local provider (including full agent mode) uses LocalProviderForm
        if (providerForm.type === 'local' || providerName === 'local' || providerName.includes('local')) {
            return <LocalProviderForm config={providerForm} onChange={updateForm} />;
        }
        
        // Check by provider NAME first (for full agents that have type='full')
        // This ensures Deepgram, Google Live, etc. use their specific forms
        if (providerName === 'deepgram' || providerName.includes('deepgram')) {
            return <DeepgramProviderForm config={providerForm} onChange={updateForm} />;
        }
        if (providerName === 'google_live' || providerName.includes('google') || providerName.includes('gemini')) {
            return <GoogleLiveProviderForm config={providerForm} onChange={updateForm} />;
        }
        if (providerName === 'openai_realtime' || providerName.includes('realtime')) {
            return <OpenAIRealtimeProviderForm config={providerForm} onChange={updateForm} />;
        }
        if (providerName.includes('elevenlabs')) {
            return <ElevenLabsProviderForm config={providerForm} onChange={updateForm} />;
        }
        if (providerName.includes('telnyx') || providerName.includes('telenyx')) {
            return <TelnyxProviderForm config={providerForm} onChange={updateForm} />;
        }
        
        // Fall back to type-based selection
        switch (providerForm.type) {
            case 'openai_realtime':
                return <OpenAIRealtimeProviderForm config={providerForm} onChange={updateForm} />;
            case 'deepgram':
                return <DeepgramProviderForm config={providerForm} onChange={updateForm} />;
            case 'google_live':
                return <GoogleLiveProviderForm config={providerForm} onChange={updateForm} />;
            case 'openai':
                return <OpenAIProviderForm config={providerForm} onChange={updateForm} />;
            case 'elevenlabs_agent':
            case 'elevenlabs':
                return <ElevenLabsProviderForm config={providerForm} onChange={updateForm} />;
            case 'ollama':
                return <OllamaProviderForm config={providerForm} onChange={updateForm} />;
            case 'telnyx':
            case 'telenyx':
                return <TelnyxProviderForm config={providerForm} onChange={updateForm} />;
            default:
                return <GenericProviderForm config={providerForm} onChange={updateForm} isNew={isNewProvider} />;
        }
    };

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading configuration...</div>;
    if (yamlError) {
        return (
            <div className="space-y-4 p-6">
                <YamlErrorBanner error={yamlError} />
                <div className="flex items-center justify-between rounded-md border border-red-500/30 bg-red-500/10 p-4 text-red-700 dark:text-red-400">
                    <div className="flex items-center">
                        <AlertCircle className="mr-2 h-5 w-5" />
                        Provider editing is disabled while `config/ai-agent.yaml` has YAML errors. Fix the YAML and reload.
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
            <div className={`${pendingRestart ? 'bg-orange-500/15 border-orange-500/30' : 'bg-yellow-500/10 border-yellow-500/20'} border text-yellow-600 dark:text-yellow-500 p-4 rounded-md flex items-center justify-between`}>
                <div className="flex items-center">
                    <AlertCircle className="w-5 h-5 mr-2" />
                    Provider configuration changes require an AI Engine restart to take effect.
                </div>
                <button
                    onClick={() => handleReloadAIEngine(false)}
                    disabled={restartingEngine}
                    className={`flex items-center text-xs px-3 py-1.5 rounded transition-colors ${
                        pendingRestart 
                            ? 'bg-orange-500 text-white hover:bg-orange-600 font-medium' 
                            : 'bg-yellow-500/20 hover:bg-yellow-500/30'
                    } disabled:opacity-50`}
                >
                    {restartingEngine ? (
                        <Loader2 className="w-3 h-3 mr-1.5 animate-spin" />
                    ) : (
                        <RefreshCw className="w-3 h-3 mr-1.5" />
                    )}
                    {restartingEngine ? 'Restarting...' : 'Restart AI Engine'}
                </button>
            </div>

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
                    <h1 className="text-3xl font-bold tracking-tight">Providers</h1>
                    <p className="text-muted-foreground mt-1">
                        Manage connections to external AI services (STT, LLM, TTS).
                        <span className="block text-xs mt-1">
                            Modular providers are auto-suffixed (e.g., <code>_stt</code>) to match engine factories. Full agents stay unsuffixed.
                        </span>
                    </p>
                </div>
                <div className="flex gap-2">
                    <button
                        onClick={handleOpenAddProvidersModal}
                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2"
                    >
                        <Wand2 className="w-4 h-4 mr-2" />
                        Add Provider Templates
                    </button>
                    <button
                        onClick={handleAddProvider}
                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                    >
                        <Plus className="w-4 h-4 mr-2" />
                        Add Provider
                    </button>
                </div>
            </div>

            <ConfigSection title="Full Agents" description="End-to-end agents (STT+LLM+TTS) that bypass pipelines.">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {Object.entries(config.providers || {}).filter(([_, p]) => isFullAgentProvider(p)).map(([name, providerData]: [string, any]) => (
                        <ConfigCard key={name} className="group relative hover:border-primary/50 transition-colors">
                            {/* Row 1: Provider info */}
                            <div className="flex items-start gap-3">
                                <div className={`p-2 rounded-md flex-shrink-0 ${providerData.enabled ? 'bg-secondary' : 'bg-muted'}`}>
                                    <Server className={`w-5 h-5 ${providerData.enabled ? 'text-primary' : 'text-muted-foreground'}`} />
                                </div>
                                <div className="min-w-0 flex-1">
                                    <div className="flex items-center gap-2 flex-wrap">
                                        <h4 className={`font-semibold text-lg truncate ${!providerData.enabled ? 'text-muted-foreground' : ''}`}>{name}</h4>
                                        {config.default_provider === name && (
                                            <span className="text-xs bg-green-500/10 text-green-600 dark:text-green-400 px-2 py-0.5 rounded-full flex items-center gap-1 flex-shrink-0">
                                                <span className="w-1.5 h-1.5 bg-green-500 rounded-full"></span>
                                                Default
                                            </span>
                                        )}
                                        {!providerData.enabled && (
                                            <span className="text-xs bg-muted text-muted-foreground px-2 py-0.5 rounded flex-shrink-0">Disabled</span>
                                        )}
                                    </div>
                                    <div className="flex flex-wrap gap-1.5 mt-1.5">
                                            {(providerData.model || providerData.voice || providerData.tts_model || providerData.llm_model || providerData.tts_voice_name || providerData.agent_id || providerData.voice_id || providerData.model_id) && (
                                                <>
                                                    {providerData.model && (
                                                        <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 text-foreground">
                                                            {providerData.model}
                                                        </span>
                                                    )}
                                                    {providerData.voice && (
                                                        <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 text-muted-foreground">
                                                            {providerData.voice}
                                                        </span>
                                                    )}
                                                    {providerData.tts_model && (
                                                        <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 text-muted-foreground">
                                                            {providerData.tts_model}
                                                        </span>
                                                    )}
                                                    {providerData.llm_model && (
                                                        <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 text-muted-foreground">
                                                            {providerData.llm_model}
                                                        </span>
                                                    )}
                                                    {providerData.tts_voice_name && (
                                                        <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 text-muted-foreground">
                                                            {providerData.tts_voice_name}
                                                        </span>
                                                    )}
                                                    {providerData.model_id && (
                                                        <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 text-foreground">
                                                            {providerData.model_id}
                                                        </span>
                                                    )}
                                                    {providerData.voice_id && (
                                                        <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 text-muted-foreground" title={providerData.voice_id}>
                                                            {providerData.voice_id.length > 15 ? `${providerData.voice_id.substring(0, 15)}...` : providerData.voice_id}
                                                        </span>
                                                    )}
                                                    {providerData.agent_id && !providerData.agent_id.startsWith('${') && (
                                                        <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 text-muted-foreground" title={providerData.agent_id}>
                                                            {providerData.agent_id.length > 20 ? `${providerData.agent_id.substring(0, 20)}...` : providerData.agent_id}
                                                        </span>
                                                    )}
                                                </>
                                            )}
                                        </div>
                                    </div>
                                </div>
                            {/* Row 2: Actions */}
                            <div className="flex items-center justify-between mt-3 pt-3 border-t border-border/50">
                                <div className="flex items-center gap-2">
                                    <label className="relative inline-flex items-center cursor-pointer">
                                        <input
                                            type="checkbox"
                                            className="sr-only peer"
                                            checked={providerData.enabled ?? true}
                                            onChange={(e) => handleToggleProvider(name, providerData, e.target.checked)}
                                        />
                                        <div className="w-9 h-5 bg-input peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-ring rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-primary"></div>
                                    </label>
                                    <span className="text-xs text-muted-foreground">{providerData.enabled !== false ? 'Enabled' : 'Disabled'}</span>
                                </div>
                                <div className="flex items-center gap-1">
                                    {config.default_provider !== name && (
                                        <button
                                            onClick={() => handleSetAsDefault(name)}
                                            className="p-1.5 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground transition-colors"
                                            title="Set as Default"
                                        >
                                            <Star className="w-4 h-4" />
                                        </button>
                                    )}
                                    <button
                                        onClick={() => handleTestConnection(name, providerData)}
                                        disabled={testingProvider === name}
                                        className="p-1.5 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground disabled:opacity-50 transition-colors"
                                        title="Test Connection"
                                    >
                                        {testingProvider === name ? (
                                            <Loader2 className="w-4 h-4 animate-spin" />
                                        ) : testResults[name]?.success ? (
                                            <CheckCircle2 className="w-4 h-4 text-green-500" />
                                        ) : testResults[name]?.success === false ? (
                                            <AlertCircle className="w-4 h-4 text-destructive" />
                                        ) : (
                                            <Server className="w-4 h-4" />
                                        )}
                                    </button>
                                    <button
                                        onClick={() => handleEditProvider(name)}
                                        className="p-1.5 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground transition-colors"
                                        title="Settings"
                                    >
                                        <Settings className="w-4 h-4" />
                                    </button>
                                    <button
                                        onClick={() => handleDeleteProvider(name)}
                                        className="p-1.5 hover:bg-destructive/10 rounded-md text-destructive transition-colors"
                                        title="Delete"
                                    >
                                        <Trash2 className="w-4 h-4" />
                                    </button>
                                </div>
                            </div>
                            {testResults[name] && (
                                <div className={`mt-2 p-2 rounded text-xs ${testResults[name]?.success
                                    ? 'bg-green-500/10 text-green-600 dark:text-green-400'
                                    : 'bg-destructive/10 text-destructive'
                                    }`}>
                                    {testResults[name]?.message}
                                </div>
                            )}
                        </ConfigCard>
                    ))}
                    {Object.entries(config.providers || {}).filter(([_, p]) => isFullAgentProvider(p)).length === 0 && (
                        <div className="col-span-full p-8 border border-dashed rounded-lg text-center text-muted-foreground">
                            No full agents configured. Click "Add Provider" to get started.
                        </div>
                    )}
                </div>
            </ConfigSection>

            <ConfigSection title="Modular Providers" description="Providers you can mix in pipelines (STT/LLM/TTS) based on their capabilities.">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {Object.entries(config.providers || {}).filter(([_, p]) => !isFullAgentProvider(p)).map(([name, providerData]: [string, any]) => (
                        <ConfigCard key={name} className="group relative hover:border-primary/50 transition-colors">
                            {/* Row 1: Provider info */}
                            <div className="flex items-start gap-3">
                                <div className={`p-2 rounded-md flex-shrink-0 ${providerData.enabled ? 'bg-secondary' : 'bg-muted'}`}>
                                    <Server className={`w-5 h-5 ${providerData.enabled ? 'text-primary' : 'text-muted-foreground'}`} />
                                </div>
                                <div className="min-w-0 flex-1">
                                    <div className="flex items-center gap-2 flex-wrap">
                                        <h4 className={`font-semibold text-lg truncate ${!providerData.enabled ? 'text-muted-foreground' : ''}`}>{name}</h4>
                                        {!providerData.enabled && (
                                            <span className="text-xs bg-muted text-muted-foreground px-2 py-0.5 rounded flex-shrink-0">Disabled</span>
                                        )}
                                    </div>
                                    <div className="flex flex-wrap gap-1.5 mt-1.5">
                                        {(providerData.capabilities || []).map((cap: string) => (
                                            <span key={cap} className="inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold text-muted-foreground">
                                                {cap.toUpperCase()}
                                            </span>
                                        ))}
                                    </div>
                                </div>
                            </div>
                            {/* Row 2: Actions */}
                            <div className="flex items-center justify-between mt-3 pt-3 border-t border-border/50">
                                <div className="flex items-center gap-2">
                                    <label className="relative inline-flex items-center cursor-pointer">
                                        <input
                                            type="checkbox"
                                            className="sr-only peer"
                                            checked={providerData.enabled ?? true}
                                            onChange={(e) => handleToggleProvider(name, providerData, e.target.checked)}
                                        />
                                        <div className="w-9 h-5 bg-input peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-ring rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-primary"></div>
                                    </label>
                                    <span className="text-xs text-muted-foreground">{providerData.enabled !== false ? 'Enabled' : 'Disabled'}</span>
                                </div>
                                <div className="flex items-center gap-1">
                                    <button
                                        onClick={() => handleTestConnection(name, providerData)}
                                        disabled={testingProvider === name}
                                        className="p-1.5 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground disabled:opacity-50 transition-colors"
                                        title="Test Connection"
                                    >
                                        {testingProvider === name ? (
                                            <Loader2 className="w-4 h-4 animate-spin" />
                                        ) : testResults[name]?.success ? (
                                            <CheckCircle2 className="w-4 h-4 text-green-500" />
                                        ) : testResults[name]?.success === false ? (
                                            <AlertCircle className="w-4 h-4 text-destructive" />
                                        ) : (
                                            <Server className="w-4 h-4" />
                                        )}
                                    </button>
                                    <button
                                        onClick={() => handleEditProvider(name)}
                                        className="p-1.5 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground transition-colors"
                                        title="Settings"
                                    >
                                        <Settings className="w-4 h-4" />
                                    </button>
                                    <button
                                        onClick={() => handleDeleteProvider(name)}
                                        className="p-1.5 hover:bg-destructive/10 rounded-md text-destructive transition-colors"
                                        title="Delete"
                                    >
                                        <Trash2 className="w-4 h-4" />
                                    </button>
                                </div>
                            </div>
                            {testResults[name] && (
                                <div className={`mt-2 p-2 rounded text-xs ${testResults[name]?.success
                                    ? 'bg-green-500/10 text-green-600 dark:text-green-400'
                                    : 'bg-destructive/10 text-destructive'
                                    }`}>
                                    {testResults[name]?.message}
                                </div>
                            )}
                        </ConfigCard>
                    ))}
                    {Object.entries(config.providers || {}).filter(([_, p]) => !isFullAgentProvider(p)).length === 0 && (
                        <div className="col-span-full p-8 border border-dashed rounded-lg text-center text-muted-foreground">
                            No composable providers configured. Click "Add Provider" to get started.
                        </div>
                    )}
                </div>
            </ConfigSection>

            <Modal
                isOpen={!!editingProvider}
                onClose={() => setEditingProvider(null)}
                title={isNewProvider ? 'Add Provider' : `Edit Provider: ${editingProvider}`}
                size="lg"
                footer={
                    <div className="flex w-full justify-between items-center">
                        <div className="text-xs text-muted-foreground">
                            Modular providers are automatically suffixed for their capability (e.g., <code>openai_stt</code>, <code>openai_llm</code>, <code>openai_tts</code>).
                        </div>
                        <div className="flex items-center gap-2">
                            <button
                                onClick={() => handleTestConnection(providerForm.name || 'new_provider', providerForm)}
                                disabled={!!testingProvider || !providerForm.name}
                                className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2"
                            >
                                {testingProvider === (providerForm.name || 'new_provider') ? (
                                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                                ) : (
                                    <Server className="w-4 h-4 mr-2" />
                                )}
                                Test Connection
                            </button>
                            {testResults[providerForm.name || 'new_provider'] && (
                                <span className={`text-xs ${testResults[providerForm.name || 'new_provider']?.success ? 'text-green-500' : 'text-destructive'}`}>
                                    {testResults[providerForm.name || 'new_provider']?.success ? 'Success' : 'Failed'}
                                </span>
                            )}
                        </div>
                        <div className="flex gap-2">
                            <button
                                onClick={() => setEditingProvider(null)}
                                className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={handleSaveProvider}
                                className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                            >
                                Save Changes
                            </button>
                        </div>
                    </div>
                }
            >
                <div className="space-y-4">
                    {!isFullAgentProvider(providerForm) && (
                        <div className="rounded-lg border border-border bg-card/40 p-4 space-y-3">
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Capability (required)</label>
                                    <select
                                        className="w-full p-2 rounded border border-input bg-background"
                                        value={Array.isArray(providerForm.capabilities) && providerForm.capabilities.length === 1 ? providerForm.capabilities[0] : ''}
                                        disabled={!isNewProvider && Array.isArray(providerForm.capabilities) && providerForm.capabilities.length === 1}
                                        onChange={(e) => handleSetModularCapability(e.target.value as Capability)}
                                    >
                                        <option value="">Select capability...</option>
                                        <option value="stt">Speech-to-Text (STT)</option>
                                        <option value="llm">Large Language Model (LLM)</option>
                                        <option value="tts">Text-to-Speech (TTS)</option>
                                    </select>
                                    <p className="text-xs text-muted-foreground">
                                        Determines which pipeline slot this provider appears in. Saved providers will persist this in YAML.
                                    </p>
                                </div>
                            </div>

                            {(() => {
                                const declared = Array.isArray(providerForm.capabilities) && providerForm.capabilities.length === 1
                                    ? (providerForm.capabilities[0] as Capability)
                                    : null;
                                const suffix = capabilityFromKey(providerForm.name || '');
                                if (!declared || !suffix || declared === suffix) return null;
                                const suggested = ensureModularKey(stripModularSuffix((providerForm.name || '').toLowerCase()), declared);
                                return (
                                    <div className="bg-amber-500/10 border border-amber-500/30 text-amber-700 dark:text-amber-400 p-3 rounded-md text-sm">
                                        <div className="font-semibold mb-1">Capability/name mismatch</div>
                                        <div>
                                            This provider name ends with <code className="px-1 rounded bg-muted">_{suffix}</code> but capabilities says{' '}
                                            <code className="px-1 rounded bg-muted">{declared}</code>. Pipelines will trust capabilities.
                                        </div>
                                        <div className="mt-2">
                                            Suggested fix: rename to <code className="px-1 rounded bg-muted">{suggested}</code>.
                                        </div>
                                    </div>
                                );
                            })()}
                        </div>
                    )}

                    {renderProviderForm()}
                </div>
            </Modal>

            {/* Add Provider Templates Modal */}
            <Modal
                isOpen={showAddProvidersModal}
                onClose={() => setShowAddProvidersModal(false)}
                title="Add Provider Templates"
                size="md"
                footer={
                    <div className="flex justify-end gap-2">
                        <button
                            onClick={() => setShowAddProvidersModal(false)}
                            className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2"
                        >
                            Cancel
                        </button>
                        <button
                            onClick={handleAddSelectedProviders}
                            disabled={selectedTemplates.length === 0}
                            className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                        >
                            Add Selected
                        </button>
                    </div>
                }
            >
                <div className="space-y-4">
                    <p className="text-sm text-muted-foreground">
                        Select provider templates to add. Templates are added <strong>disabled</strong> by default.
                        Configure API keys in the Environment page, then enable the provider.
                    </p>
                    <div className="space-y-2">
                        <h4 className="text-sm font-medium">Full Agents (Cloud)</h4>
                        {[
                            { id: 'openai_realtime', name: 'OpenAI Realtime', desc: 'GPT-4o real-time voice agent' },
                            { id: 'deepgram', name: 'Deepgram', desc: 'Nova-2 STT + Aura TTS voice agent' },
                            { id: 'google_live', name: 'Google Live', desc: 'Gemini 2.5 Native Audio real-time agent' },
                            { id: 'elevenlabs_agent', name: 'ElevenLabs Agent', desc: 'ElevenLabs conversational AI' },
                        ].map(template => (
                            <label key={template.id} className="flex items-start gap-3 p-3 border rounded-lg hover:bg-accent/50 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={selectedTemplates.includes(template.id)}
                                    onChange={(e) => {
                                        if (e.target.checked) {
                                            setSelectedTemplates([...selectedTemplates, template.id]);
                                        } else {
                                            setSelectedTemplates(selectedTemplates.filter(t => t !== template.id));
                                        }
                                    }}
                                    disabled={!!config.providers?.[template.id]}
                                    className="mt-1"
                                />
                                <div className="flex-1">
                                    <div className="flex items-center gap-2">
                                        <span className="font-medium">{template.name}</span>
                                        {config.providers?.[template.id] && (
                                            <span className="text-xs bg-muted text-muted-foreground px-2 py-0.5 rounded">Already exists</span>
                                        )}
                                    </div>
                                    <p className="text-xs text-muted-foreground">{template.desc}</p>
                                </div>
                            </label>
                        ))}
                    </div>
                    <div className="space-y-2">
                        <h4 className="text-sm font-medium">Modular Providers (Local)</h4>
                        <label className="flex items-start gap-3 p-3 border rounded-lg hover:bg-accent/50 cursor-pointer">
                            <input
                                type="checkbox"
                                checked={selectedTemplates.includes('local_modular')}
                                onChange={(e) => {
                                    if (e.target.checked) {
                                        setSelectedTemplates([...selectedTemplates, 'local_modular']);
                                    } else {
                                        setSelectedTemplates(selectedTemplates.filter(t => t !== 'local_modular'));
                                    }
                                }}
                                disabled={!!(config.providers?.local_stt && config.providers?.local_llm && config.providers?.local_tts)}
                                className="mt-1"
                            />
                            <div className="flex-1">
                                <div className="flex items-center gap-2">
                                    <span className="font-medium">Local Modular (STT + LLM + TTS)</span>
                                    {config.providers?.local_stt && config.providers?.local_llm && config.providers?.local_tts && (
                                        <span className="text-xs bg-muted text-muted-foreground px-2 py-0.5 rounded">Already exists</span>
                                    )}
                                </div>
                                <p className="text-xs text-muted-foreground">Adds local_stt, local_llm, local_tts for pipeline use</p>
                            </div>
                        </label>
                    </div>
                    <div className="space-y-2">
                        <h4 className="text-sm font-medium">Modular Providers (Cloud)</h4>
                        {[
                            { id: 'telnyx_llm', name: 'Telnyx LLM', desc: 'Telnyx AI Inference (OpenAI-compatible /chat/completions)' },
                        ].map(template => (
                            <label key={template.id} className="flex items-start gap-3 p-3 border rounded-lg hover:bg-accent/50 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={selectedTemplates.includes(template.id)}
                                    onChange={(e) => {
                                        if (e.target.checked) {
                                            setSelectedTemplates([...selectedTemplates, template.id]);
                                        } else {
                                            setSelectedTemplates(selectedTemplates.filter(t => t !== template.id));
                                        }
                                    }}
                                    disabled={!!config.providers?.[template.id]}
                                    className="mt-1"
                                />
                                <div className="flex-1">
                                    <div className="flex items-center gap-2">
                                        <span className="font-medium">{template.name}</span>
                                        {config.providers?.[template.id] && (
                                            <span className="text-xs bg-muted text-muted-foreground px-2 py-0.5 rounded">Already exists</span>
                                        )}
                                    </div>
                                    <p className="text-xs text-muted-foreground">{template.desc}</p>
                                </div>
                            </label>
                        ))}
                    </div>
                </div>
            </Modal>
        </div>
    );
};

export default ProvidersPage;
