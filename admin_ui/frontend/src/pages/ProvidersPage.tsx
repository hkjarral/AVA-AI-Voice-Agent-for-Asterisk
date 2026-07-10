import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useConfirmDialog } from '../hooks/useConfirmDialog';
import yaml from 'js-yaml';
import { sanitizeConfigForSave } from '../utils/configSanitizers';
import { getCachedConfig, loadConfigYaml } from '../utils/configCache';
import { Plus, Settings, Trash2, Server, AlertCircle, CheckCircle2, Loader2, RefreshCw, Wand2, Star } from 'lucide-react';
import { YamlErrorBanner, YamlErrorInfo } from '../components/ui/YamlErrorBanner';
import { ConfigSection } from '../components/ui/ConfigSection';
import { ConfigCard } from '../components/ui/ConfigCard';
import { Modal } from '../components/ui/Modal';
import HelpTooltip from '../components/ui/HelpTooltip';
import { useRestartRequired } from '../hooks/useRestartRequired';
import { localAIStatusFromLiveSnapshot } from '../utils/liveStatus';

// Provider Forms
import GenericProviderForm from '../components/config/providers/GenericProviderForm';
import LocalProviderForm from '../components/config/providers/LocalProviderForm';
import OllamaProviderForm from '../components/config/providers/OllamaProviderForm';
import OpenAIRealtimeProviderForm from '../components/config/providers/OpenAIRealtimeProviderForm';
import DeepgramProviderForm from '../components/config/providers/DeepgramProviderForm';
import GoogleLiveProviderForm from '../components/config/providers/GoogleLiveProviderForm';
import GrokProviderForm from '../components/config/providers/GrokProviderForm';
import OpenAIProviderForm from '../components/config/providers/OpenAIProviderForm';
import ElevenLabsProviderForm from '../components/config/providers/ElevenLabsProviderForm';
import TelnyxProviderForm from '../components/config/providers/TelnyxProviderForm';
import AzureProviderForm from '../components/config/providers/AzureProviderForm';
import { Capability, capabilityFromKey, ensureModularKey, isFullAgentProvider, getEffectiveFullAgentKind } from '../utils/providerNaming';
import { GOOGLE_LIVE_DEFAULT_MODEL } from '../utils/googleLiveModels';

const stripModularSuffix = (name: string): string => (name || '').replace(/_(stt|llm|tts)$/i, '');
const FULL_AGENT_TYPES = ['openai_realtime', 'deepgram', 'google_live', 'elevenlabs_agent', 'grok', 'local'];
const providerLabel = (name: string, provider: any): string => provider?.display_name || provider?.customer || name;

const ProvidersPage: React.FC = () => {
    const { confirm } = useConfirmDialog();
    const [config, setConfig] = useState<any>(() => getCachedConfig()?.config ?? {});
    const [loading, setLoading] = useState(() => getCachedConfig() == null);
    const [error, setError] = useState<string | null>(null);
    const [yamlError, setYamlError] = useState<YamlErrorInfo | null>(() => getCachedConfig()?.yamlError ?? null);
    const [editingProvider, setEditingProvider] = useState<string | null>(null);
    const [providerForm, setProviderForm] = useState<any>({});
    const [isNewProvider, setIsNewProvider] = useState(false);
    const [testingProvider, setTestingProvider] = useState<string | null>(null);
    const [testResults, setTestResults] = useState<{ [key: string]: { success: boolean; message: string } | undefined }>({});
    const [showAddProvidersModal, setShowAddProvidersModal] = useState(false);
    const [selectedTemplates, setSelectedTemplates] = useState<string[]>([]);
    const { restartRequired, refetch } = useRestartRequired();
    const [restartingEngine, setRestartingEngine] = useState(false);
    const [localAIStatus, setLocalAIStatus] = useState<any>(null);
    const [providerHealth, setProviderHealth] = useState<Record<string, { status: string; total: number; failures: number; summary: string }>>({});
    const [providerHealthUnavailable, setProviderHealthUnavailable] = useState(false);

    useEffect(() => {
        // Cache-first: seed from the shared cache (no flash on revisit). The write
        // interceptor invalidates the cache on every save, so a background
        // revalidate is unnecessary and could clobber in-progress form edits.
        fetchConfig();
        fetchProviderHealth();
        const healthInterval = setInterval(fetchProviderHealth, 30000);
        // Fetch local AI status for live model info on cards
        const fetchLocalStatus = async () => {
            try {
                const [liveStatusRes, healthRes] = await Promise.allSettled([
                    axios.get('/api/system/live-status'),
                    axios.get('/api/system/health'),
                ]);
                if (liveStatusRes.status === 'fulfilled') {
                    const liveLocalAI = localAIStatusFromLiveSnapshot(liveStatusRes.value.data);
                    if (liveLocalAI?.connected) {
                        setLocalAIStatus(liveLocalAI.details);
                        return;
                    }
                }
                if (healthRes.status === 'fulfilled' && healthRes.value.data?.local_ai_server?.status === 'connected') {
                    setLocalAIStatus(healthRes.value.data.local_ai_server.details);
                }
            } catch { /* ignore */ }
        };
        fetchLocalStatus();
        const interval = setInterval(fetchLocalStatus, 15000);
        return () => { clearInterval(interval); clearInterval(healthInterval); };
    }, []);

    const fetchConfig = async (force = false) => {
        try {
            const r = await loadConfigYaml(force);
            setConfig(r.config);
            setYamlError(r.yamlError);
            setError(null);
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

    const fetchProviderHealth = async () => {
        try {
            const res = await axios.get('/api/providers/health');
            setProviderHealth(res.data?.providers || {});
            setProviderHealthUnavailable(false);
        } catch {
            setProviderHealth({});
            setProviderHealthUnavailable(true);
        }
    };

    // Shared health dot indicator used by both Full Agent and Modular provider cards
    const HealthDot: React.FC<{ name: string }> = ({ name }) => {
        if (providerHealthUnavailable) {
            return <span className="w-2.5 h-2.5 bg-orange-400 rounded-full flex-shrink-0" title="Health data unavailable (API error)" />;
        }
        // Normalize to lowercase to match backend key normalization
        const health = providerHealth[name.toLowerCase()];
        if (!health) {
            return <span className="w-2.5 h-2.5 bg-gray-400 rounded-full flex-shrink-0" title="No recent call data" />;
        }
        const colors: Record<string, string> = { healthy: 'bg-green-500', degraded: 'bg-yellow-500', error: 'bg-red-500', no_data: 'bg-gray-400' };
        return <span className={`w-2.5 h-2.5 ${colors[health.status] || 'bg-gray-400'} rounded-full flex-shrink-0`} title={health.summary} />;
    };

    const normalizeProviderCapabilities = (nextConfig: any) => {
        const providers = nextConfig?.providers || {};
        const normalizedProviders: Record<string, any> = { ...providers };

        Object.entries(providers).forEach(([providerKey, providerData]) => {
            if (!providerData || typeof providerData !== 'object' || Array.isArray(providerData)) return;
            // Only auto-fill for modular providers.
            if (isFullAgentProvider(providerData, providerKey)) return;

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
            await refetch();
        } catch (err) {
            console.error('Failed to save config', err);
            toast.error('Failed to save configuration');
        }
    };

    const handleEditProvider = (name: string) => {
        setEditingProvider(name);
        const providerData = { ...(config.providers?.[name] || {}) };

        // Only infer concrete type when YAML didn't specify one. Rewriting
        // a legacy `type: full` value into a name-heuristic guess silently
        // changes the implementation kind for instances with neutral keys
        // like `acme_primary` (CodeRabbit major on PR #396).
        if (!providerData.type) {
            if (isFullAgentProvider(providerData, name)) {
                const lowerName = name.toLowerCase();
                if (lowerName === 'local' || lowerName.includes('local')) providerData.type = 'local';
                else if (lowerName.includes('google') || lowerName.includes('gemini')) providerData.type = 'google_live';
                else if (lowerName.includes('elevenlabs')) providerData.type = 'elevenlabs_agent';
                else if (lowerName.includes('deepgram')) providerData.type = 'deepgram';
                else if (lowerName.includes('grok')) providerData.type = 'grok';
                else providerData.type = 'openai_realtime';
            } else {
                const lowerName = name.toLowerCase();
                if (lowerName.includes('openai')) providerData.type = 'openai';
                else if (lowerName.includes('deepgram')) providerData.type = 'deepgram';
                else if (lowerName.includes('google') || lowerName.includes('gemini')) providerData.type = 'google_live';
                else if (lowerName.includes('elevenlabs')) providerData.type = 'elevenlabs_agent';
                else if (lowerName.includes('ollama')) providerData.type = 'ollama';
                else if (lowerName.includes('local')) providerData.type = 'local';
                else if (lowerName.includes('azure')) providerData.type = 'azure';
                else providerData.type = 'other';
            }
        }

        // Legacy migration: if capabilities are missing for a modular provider, infer from suffix for UX.
        if (!isFullAgentProvider(providerData, name)) {
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
            type: 'openai_realtime',
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
                type: 'openai_realtime',
                capabilities: ['stt', 'llm', 'tts'],
                // GA defaults — OpenAI sunset the Beta Realtime API on 2026-05-12
                // and removed gpt-4o-realtime-preview-* on 2026-05-07. The
                // OpenAI Realtime provider form lists all four current GA models
                // (gpt-realtime, gpt-realtime-1.5, gpt-realtime-2, gpt-realtime-mini).
                api_version: 'ga',
                model: 'gpt-realtime',
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
                type: 'deepgram',
                capabilities: ['stt', 'llm', 'tts'],
                // Default aligned with shipped config/ai-agent.yaml + DeepgramProviderConfig.
                // Pre-v6.5.0 the runtime hardcoded nova-3 regardless; v6.5.0 makes the listen
                // model honor config and this default preserves that effective behavior.
                model: 'nova-3',
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
                type: 'google_live',
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
            grok: {
                enabled: false,
                type: 'grok',
                capabilities: ['stt', 'llm', 'tts'],
                api_key: '${XAI_API_KEY}',
                base_url: 'wss://api.x.ai/v1/realtime',
                model: 'grok-voice-latest',
                voice: 'eve',
                // μ-law @ 8 kHz inbound passthrough (matches Asterisk native telephony)
                input_encoding: 'ulaw',
                input_sample_rate_hz: 8000,
                provider_input_encoding: 'ulaw',
                provider_input_sample_rate_hz: 8000,
                // xAI emits 24 kHz PCM16; we resample down to μ-law for Asterisk
                output_encoding: 'linear16',
                output_sample_rate_hz: 24000,
                target_encoding: 'ulaw',
                target_sample_rate_hz: 8000,
                response_modalities: ['audio', 'text'],
                greeting: 'Hello, how can I help you today?',
                instructions: 'You are a helpful AI assistant.',
                turn_detection: { type: 'server_vad', threshold: 0.5, silence_duration_ms: 200, prefix_padding_ms: 200 },
                session_warn_after_seconds: 1680,
            },
            elevenlabs_agent: {
                enabled: false,
                type: 'elevenlabs_agent',
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
            },
            azure_stt: {
                enabled: false,
                type: 'azure',
                capabilities: ['stt'],
                region: 'eastus',
                language: 'en-US',
                variant: 'realtime',
                request_timeout_sec: 15.0,
            },
            azure_tts: {
                enabled: false,
                type: 'azure',
                capabilities: ['tts'],
                region: 'eastus',
                voice_name: 'en-US-JennyNeural',
                output_format: 'riff-8khz-16bit-mono-pcm',
                target_encoding: 'mulaw',
                target_sample_rate_hz: 8000,
                chunk_size_ms: 20,
                request_timeout_sec: 15.0,
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
                const template = templates[templateKey];
                if (!template) {
                    return;
                }
                nextProviders[templateKey] = template;
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
    };

    const handleSetAsDefault = async (name: string) => {
        const newConfig = { ...config };
        newConfig.default_provider = name;
        // Clear active_pipeline for full agents
        if (isFullAgentProvider(config.providers?.[name], name)) {
            newConfig.active_pipeline = null;
        }
        // Auto-enable the provider when setting as default
        if (newConfig.providers?.[name]) {
            newConfig.providers[name].enabled = true;
        }
        await saveConfig(newConfig);
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
                await refetch();
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
            // Resolve the concrete full-agent kind the way the engine does, so a
            // canonical legacy entry (e.g. google_live: { type: full }) validates
            // and saves without forcing a type change. #436
            const fullAgentKey = isNewProvider ? finalName : (editingProvider || '');
            const effectiveKind = getEffectiveFullAgentKind(providerForm, fullAgentKey);
            if (!effectiveKind || !FULL_AGENT_TYPES.includes(effectiveKind)) {
                toast.error('Select a full-agent provider type.');
                return;
            }
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
        if (!isNewProvider && editingProvider !== finalName) {
            toast.error('Provider keys are immutable. Clone the provider to create a new key.');
            return;
        }
        if (isNewProvider && newConfig.pipelines?.[finalName]) {
            toast.error(`Provider "${finalName}" collides with an existing pipeline name.`);
            return;
        }
        if (isNewProvider && String(providerForm.type || '').toLowerCase() === 'local') {
            // Match both `type: local` AND the legacy `type: full` shape
            // (where the YAML key was `local` but type defaulted to `full`),
            // so a second local full-agent can't slip in undetected
            // (CodeRabbit on PR #396).
            const hasLocal = Object.entries(newConfig.providers || {}).some(([key, value]: [string, any]) => {
                if (key === finalName) return false;
                const t = String(value?.type || key).toLowerCase();
                return t === 'local' || (t === 'full' && key.toLowerCase() === 'local');
            });
            if (hasLocal) {
                toast.error('Only one local full-agent provider can be configured.');
                return;
            }
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
        // Functional setState so async callbacks (e.g. credential uploads
        // resolving after the user has edited other fields) don't merge against
        // a stale `providerForm` captured at render time.
        //
        // Delete semantics: a key set to `undefined` in `newValues` is treated
        // as "remove this key from the form state". This is how the credential
        // card signals deletion of `api_key_file` / `agent_id_file` after the
        // user clicks Delete — without this, a shallow merge would preserve
        // the prior path and a later form Save would write that stale
        // reference back to YAML, pointing at a file that was just removed.
        // (Reported in PR #395 review.)
        const updateForm = (newValues: any) =>
            setProviderForm((prev: any) => {
                const next: any = { ...prev };
                for (const [k, v] of Object.entries(newValues)) {
                    if (v === undefined) delete next[k];
                    else next[k] = v;
                }
                return next;
            });

        // Check provider name for specific forms, fallback to type
        const providerName = (providerForm.name || '').toLowerCase();

        // --- MODULAR GUARD ---
        // Modular providers (single capability) always use GenericProviderForm,
        // which handles subtype selection (OpenAI-compatible, Ollama, etc.)
        // This prevents modular type=openai from routing to the full OpenAI agent form.
        const caps = providerForm.capabilities || [];
        const isModular = Array.isArray(caps) && caps.length === 1;
        if (isModular) {
            return <GenericProviderForm config={providerForm} onChange={updateForm} isNew={isNewProvider} />;
        }

        // Local provider (including full agent mode) uses LocalProviderForm
        if (providerForm.type === 'local' || providerName === 'local' || providerName.includes('local')) {
            return <LocalProviderForm config={providerForm} onChange={updateForm} />;
        }

        // Check by provider NAME first (for full agents that have type='full')
        // This ensures Deepgram, Google Live, etc. use their specific forms
        // Per-instance credentials require a saved YAML entry; pass `editingProvider`
        // when editing an existing provider, undefined for unsaved new ones.
        const credKey = isNewProvider ? undefined : (editingProvider || undefined);
        if (providerName === 'deepgram' || providerName.includes('deepgram')) {
            return <DeepgramProviderForm config={providerForm} onChange={updateForm} providerKey={credKey} />;
        }
        if (providerName === 'google_live' || providerName.includes('google') || providerName.includes('gemini')) {
            return <GoogleLiveProviderForm config={providerForm} onChange={updateForm} providerKey={credKey} />;
        }
        if (providerName.includes('azure')) {
            return <AzureProviderForm config={providerForm} onChange={updateForm} />;
        }
        if (providerName === 'openai_realtime' || providerName.includes('realtime')) {
            return <OpenAIRealtimeProviderForm config={providerForm} onChange={updateForm} providerKey={credKey} />;
        }
        if (providerName === 'grok' || providerName.includes('grok') || providerForm.type === 'grok') {
            return <GrokProviderForm config={providerForm} onChange={updateForm} providerKey={credKey} />;
        }
        if (providerName.includes('elevenlabs')) {
            return <ElevenLabsProviderForm config={providerForm} onChange={updateForm} providerKey={credKey} />;
        }
        if (providerName.includes('telnyx') || providerName.includes('telenyx')) {
            return <TelnyxProviderForm config={providerForm} onChange={updateForm} />;
        }

        // Fall back to type-based selection (for full agents only at this point)
        switch (providerForm.type) {
            case 'openai_realtime':
                return <OpenAIRealtimeProviderForm config={providerForm} onChange={updateForm} providerKey={credKey} />;
            case 'deepgram':
                return <DeepgramProviderForm config={providerForm} onChange={updateForm} providerKey={credKey} />;
            case 'google_live':
                return <GoogleLiveProviderForm config={providerForm} onChange={updateForm} providerKey={credKey} />;
            case 'grok':
                return <GrokProviderForm config={providerForm} onChange={updateForm} providerKey={credKey} />;
            case 'openai':
                return <OpenAIProviderForm config={providerForm} onChange={updateForm} />;
            case 'elevenlabs_agent':
            case 'elevenlabs':
                return <ElevenLabsProviderForm config={providerForm} onChange={updateForm} providerKey={credKey} />;
            case 'ollama':
                return <OllamaProviderForm config={providerForm} onChange={updateForm} />;
            case 'telnyx':
            case 'telenyx':
                return <TelnyxProviderForm config={providerForm} onChange={updateForm} />;
            case 'azure':
                return <AzureProviderForm config={providerForm} onChange={updateForm} />;
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
            {restartRequired && (
                <div className="bg-orange-500/15 border-orange-500/30 border text-yellow-800 dark:text-yellow-500 p-4 rounded-md flex items-center justify-between">
                    <div className="flex items-center">
                        <AlertCircle className="w-5 h-5 mr-2" />
                        Provider configuration changes require an AI Engine restart to take effect.
                    </div>
                    <button
                        onClick={() => handleReloadAIEngine(false)}
                        disabled={restartingEngine}
                        className="flex items-center text-xs px-3 py-1.5 rounded transition-colors bg-orange-500 text-white hover:bg-orange-600 font-medium disabled:opacity-50"
                    >
                        {restartingEngine ? (
                            <Loader2 className="w-3 h-3 mr-1.5 animate-spin" />
                        ) : (
                            <RefreshCw className="w-3 h-3 mr-1.5" />
                        )}
                        {restartingEngine ? 'Restarting...' : 'Restart AI Engine'}
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
                    {Object.entries(config.providers || {}).filter(([name, p]) => isFullAgentProvider(p, name)).map(([name, providerData]: [string, any]) => (
                        <ConfigCard key={name} className="group relative hover:border-primary/50 transition-colors">
                            {/* Row 1: Provider info */}
                            <div className="flex items-start gap-3">
                                <div className={`p-2 rounded-md flex-shrink-0 ${providerData.enabled ? 'bg-secondary' : 'bg-muted'}`}>
                                    <Server className={`w-5 h-5 ${providerData.enabled ? 'text-primary' : 'text-muted-foreground'}`} />
                                </div>
                                <div className="min-w-0 flex-1">
                                    <div className="flex items-center gap-2 flex-wrap">
                                        <h4 className={`font-semibold text-lg truncate ${!providerData.enabled ? 'text-muted-foreground' : ''}`}>{providerLabel(name, providerData)}</h4>
                                        {config.default_provider === name && (
                                            <span className="text-xs bg-green-500/10 text-green-600 dark:text-green-400 px-2 py-0.5 rounded-full flex items-center gap-1 flex-shrink-0">
                                                <span className="w-1.5 h-1.5 bg-green-500 rounded-full"></span>
                                                Default
                                            </span>
                                        )}
                                        {!providerData.enabled && (
                                            <span className="text-xs bg-muted text-muted-foreground px-2 py-0.5 rounded flex-shrink-0">Disabled</span>
                                        )}
                                        <HealthDot name={name} />
                                    </div>
                                    <div className="text-xs text-muted-foreground mt-0.5">
                                        {name} · {providerData.type || name}{providerData.customer ? ` · ${providerData.customer}` : ''}
                                    </div>
                                    <div className="flex flex-wrap gap-1.5 mt-1.5">
                                        {(() => {
                                            // For local provider, show live-loaded model from health endpoint
                                            const isLocal = name === 'local' || (providerData.type === 'local') || (providerData.type === 'full' && name.includes('local'));
                                            if (isLocal && localAIStatus) {
                                                const llmName = localAIStatus.models?.llm?.path?.split('/').pop() || null;
                                                const sttName = localAIStatus.stt_backend || null;
                                                const ttsName = localAIStatus.tts_backend || null;
                                                const parts: string[] = [];
                                                if (sttName) parts.push(`STT: ${sttName.charAt(0).toUpperCase() + sttName.slice(1)}`);
                                                if (llmName) parts.push(llmName);
                                                if (ttsName) parts.push(`TTS: ${ttsName.charAt(0).toUpperCase() + ttsName.slice(1)}`);
                                                if (parts.length > 0) {
                                                    return parts.map((label) => (
                                                        <span key={label} className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors text-foreground">
                                                            {label}
                                                        </span>
                                                    ));
                                                }
                                            }
                                            // Fallback: show static YAML fields for non-local providers
                                            return (
                                                <>
                                                    {providerData.model && (
                                                        <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors text-foreground">
                                                            {providerData.model}
                                                        </span>
                                                    )}
                                                    {providerData.voice && (
                                                        <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors text-muted-foreground">
                                                            {providerData.voice}
                                                        </span>
                                                    )}
                                                    {providerData.tts_model && (
                                                        <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors text-muted-foreground">
                                                            {providerData.tts_model}
                                                        </span>
                                                    )}
                                                    {providerData.llm_model && (
                                                        <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors text-muted-foreground">
                                                            {providerData.llm_model}
                                                        </span>
                                                    )}
                                                    {providerData.tts_voice_name && (
                                                        <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors text-muted-foreground">
                                                            {providerData.tts_voice_name}
                                                        </span>
                                                    )}
                                                    {providerData.model_id && (
                                                        <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors text-foreground">
                                                            {providerData.model_id}
                                                        </span>
                                                    )}
                                                    {providerData.voice_id && (
                                                        <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors text-muted-foreground" title={providerData.voice_id}>
                                                            {providerData.voice_id.length > 15 ? `${providerData.voice_id.substring(0, 15)}...` : providerData.voice_id}
                                                        </span>
                                                    )}
                                                    {providerData.agent_id && !providerData.agent_id.startsWith('${') && (
                                                        <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors text-muted-foreground" title={providerData.agent_id}>
                                                            {providerData.agent_id.length > 20 ? `${providerData.agent_id.substring(0, 20)}...` : providerData.agent_id}
                                                        </span>
                                                    )}
                                                </>
                                            );
                                        })()}
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
                                        <div className="w-9 h-5 bg-input peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-ring rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-background after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-primary"></div>
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
                    {Object.entries(config.providers || {}).filter(([name, p]) => isFullAgentProvider(p, name)).length === 0 && (
                        <div className="col-span-full p-8 border border-dashed rounded-lg text-center text-muted-foreground">
                            No full agents configured. Click "Add Provider" to get started.
                        </div>
                    )}
                </div>
            </ConfigSection>

            <ConfigSection title="Modular Providers" description="Providers you can mix in pipelines (STT/LLM/TTS) based on their capabilities.">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {Object.entries(config.providers || {}).filter(([name, p]) => !isFullAgentProvider(p, name)).map(([name, providerData]: [string, any]) => (
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
                                        <HealthDot name={name} />
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
                                        <div className="w-9 h-5 bg-input peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-ring rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-background after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-primary"></div>
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
                    {Object.entries(config.providers || {}).filter(([name, p]) => !isFullAgentProvider(p, name)).length === 0 && (
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
                    <div className="rounded-lg border border-border bg-card/40 p-4 space-y-3">
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <div className="flex items-center gap-1.5">
                                    <label className="text-sm font-medium">Provider Key</label>
                                    <HelpTooltip
                                        content={
                                            <>
                                                <strong>Provider Key</strong> — the unique YAML identifier for this provider instance.
                                                <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                    <li>Lowercase, digits, <code>_</code>, <code>-</code>, <code>.</code> only — no spaces</li>
                                                    <li>Used in the dialplan to route calls: <code>Set(AI_PROVIDER=&lt;key&gt;)</code></li>
                                                    <li><strong>Immutable</strong> after creation — clone to rename</li>
                                                    <li>For multi-tenant, prefix with the tenant: <code>acme_grok</code>, <code>globex_grok</code></li>
                                                </ul>
                                            </>
                                        }
                                    />
                                </div>
                                <input
                                    className="w-full p-2 rounded border border-input bg-background"
                                    value={providerForm.name || ''}
                                    disabled={!isNewProvider}
                                    onChange={(e) => setProviderForm({ ...providerForm, name: e.target.value.toLowerCase().replace(/[^a-z0-9_.-]/g, '') })}
                                    placeholder="acme_google_live"
                                    autoComplete="off"
                                    spellCheck={false}
                                />
                            </div>
                            {isFullAgentProvider(providerForm) && (
                                <div className="space-y-2">
                                    <div className="flex items-center gap-1.5">
                                        <label className="text-sm font-medium">Provider Type</label>
                                        <HelpTooltip
                                            content={
                                                <>
                                                    <strong>Provider Type</strong> — which engine adapter handles calls for this instance.
                                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                        <li>Determines which form fields appear below (different providers expose different settings)</li>
                                                        <li><strong>Immutable</strong> after creation</li>
                                                        <li>Each type's defaults populate from the Templates modal</li>
                                                        <li><code>local</code> = full-agent mode for the on-premises Local AI Server</li>
                                                    </ul>
                                                </>
                                            }
                                        />
                                    </div>
                                    <select
                                        className="w-full p-2 rounded border border-input bg-background"
                                        // Show the resolved concrete kind so a canonical legacy
                                        // entry (e.g. google_live: { type: full }) displays correctly
                                        // instead of falling back to the first option. #436
                                        value={getEffectiveFullAgentKind(providerForm, editingProvider || undefined) || providerForm.type || 'openai_realtime'}
                                        disabled={!isNewProvider}
                                        onChange={(e) => setProviderForm({ ...providerForm, type: e.target.value, capabilities: ['stt', 'llm', 'tts'] })}
                                    >
                                        <option value="openai_realtime">OpenAI Realtime</option>
                                        <option value="deepgram">Deepgram Voice Agent</option>
                                        <option value="google_live">Google Live</option>
                                        <option value="elevenlabs_agent">ElevenLabs Agent</option>
                                        <option value="grok">xAI Grok Voice Agent</option>
                                        <option value="local">Local</option>
                                    </select>
                                </div>
                            )}
                            <div className="space-y-2">
                                <div className="flex items-center gap-1.5">
                                    <label className="text-sm font-medium">Display Name</label>
                                    <HelpTooltip
                                        content={
                                            <>
                                                <strong>Display Name</strong> — friendly label shown in the dashboard topology and Providers list.
                                                <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                    <li>Falls back to the YAML key when blank</li>
                                                    <li>Use the customer / tenant name for clarity in multi-instance setups (e.g. "Acme Google Live")</li>
                                                    <li>Doesn't affect routing — pure cosmetic</li>
                                                </ul>
                                            </>
                                        }
                                    />
                                </div>
                                <input
                                    className="w-full p-2 rounded border border-input bg-background"
                                    value={providerForm.display_name || ''}
                                    onChange={(e) => setProviderForm({ ...providerForm, display_name: e.target.value })}
                                    // Placeholder mirrors the selected provider type so it reads
                                    // sensibly while the user is editing (was hard-coded to
                                    // "Acme Google Live" regardless of provider type).
                                    placeholder={(() => {
                                        const t = (providerForm.type || '').toLowerCase();
                                        const kindLabel =
                                            t === 'openai_realtime' ? 'OpenAI Realtime' :
                                            t === 'google_live' ? 'Google Live' :
                                            t === 'deepgram' ? 'Deepgram' :
                                            t === 'elevenlabs_agent' || t === 'elevenlabs' ? 'ElevenLabs' :
                                            t === 'grok' ? 'Grok' :
                                            t === 'local' ? 'Local' :
                                            t ? t.charAt(0).toUpperCase() + t.slice(1) :
                                            'Provider';
                                        return `Acme ${kindLabel}`;
                                    })()}
                                    autoComplete="off"
                                    spellCheck={false}
                                />
                            </div>
                            <div className="space-y-2">
                                <div className="flex items-center gap-1.5">
                                    <label className="text-sm font-medium">Customer</label>
                                    <HelpTooltip
                                        content={
                                            <>
                                                <strong>Customer</strong> — optional tenant identifier for multi-instance deployments.
                                                <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                    <li>Shown in the dashboard sub-row to distinguish instances of the same provider kind</li>
                                                    <li>Helps you tell apart e.g. <code>acme_grok</code> from <code>globex_grok</code> at a glance</li>
                                                    <li>Doesn't affect routing or billing — purely a label</li>
                                                </ul>
                                            </>
                                        }
                                    />
                                </div>
                                <input
                                    className="w-full p-2 rounded border border-input bg-background"
                                    value={providerForm.customer || ''}
                                    onChange={(e) => setProviderForm({ ...providerForm, customer: e.target.value })}
                                    placeholder="Acme"
                                    autoComplete="off"
                                    spellCheck={false}
                                />
                            </div>
                        </div>
                    </div>

                    {!isFullAgentProvider(providerForm) && (
                        <div className="rounded-lg border border-border bg-card/40 p-4 space-y-3">
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <div className="flex items-center gap-1.5">
                                        <label className="text-sm font-medium">Capability (required)</label>
                                        <HelpTooltip
                                            content={
                                                <>
                                                    <strong>Capability</strong> — which pipeline slot this modular provider fills.
                                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                        <li><code>STT</code> — Speech-to-Text (transcription)</li>
                                                        <li><code>LLM</code> — Large Language Model (reasoning)</li>
                                                        <li><code>TTS</code> — Text-to-Speech (synthesis)</li>
                                                        <li>Pipelines reference providers by capability — only one per provider</li>
                                                        <li>The YAML key gets auto-suffixed (e.g. <code>_stt</code>) for clarity</li>
                                                        <li><strong>Immutable</strong> after first save</li>
                                                    </ul>
                                                </>
                                            }
                                        />
                                    </div>
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
                            {
                                id: 'openai_realtime',
                                name: 'OpenAI Realtime',
                                desc: 'GPT-4o real-time voice agent',
                                doc: 'https://platform.openai.com/docs/guides/realtime',
                                tooltip: (
                                    <>
                                        <strong>OpenAI Realtime</strong> — native speech-to-speech with the <code>gpt-realtime</code> model family (gpt-realtime, gpt-realtime-1.5, gpt-realtime-2, gpt-realtime-mini).
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>~5–8¢/min (gpt-realtime) · ~3¢/min (gpt-realtime-mini)</li>
                                            <li>10 voices (alloy, echo, ash, ballad, cedar, coral, marin, sage, shimmer, verse)</li>
                                            <li>Server-side VAD, native barge-in</li>
                                            <li>Most natural conversational quality</li>
                                            <li>Requires <code>OPENAI_API_KEY</code> with Realtime API access</li>
                                        </ul>
                                    </>
                                ),
                            },
                            {
                                id: 'deepgram',
                                name: 'Deepgram',
                                desc: 'Nova-3 STT + Aura-2 TTS voice agent (Flux available)',
                                doc: 'https://developers.deepgram.com/docs/voice-agent',
                                tooltip: (
                                    <>
                                        <strong>Deepgram Voice Agent</strong> — Nova-3 STT + Aura-2 TTS, with built-in Think stage for reasoning.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>~8¢/min</li>
                                            <li>Lowest STT latency (Nova-3)</li>
                                            <li>Flux variant available for sub-200ms turn-taking</li>
                                            <li>Best for enterprise telephony workloads</li>
                                            <li>Requires <code>DEEPGRAM_API_KEY</code></li>
                                        </ul>
                                    </>
                                ),
                            },
                            {
                                id: 'google_live',
                                name: 'Google Live',
                                desc: 'Gemini 2.5 Native Audio real-time agent',
                                doc: 'https://ai.google.dev/gemini-api/docs/live',
                                tooltip: (
                                    <>
                                        <strong>Google Gemini Live</strong> — fastest response time + best multilingual support.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>~1.5¢/min (cheapest cloud option)</li>
                                            <li>&lt;1s response latency</li>
                                            <li>24+ languages out of the box</li>
                                            <li>Two auth modes: Developer API or Vertex AI</li>
                                            <li>Requires <code>GOOGLE_API_KEY</code> or service-account JSON</li>
                                        </ul>
                                    </>
                                ),
                            },
                            {
                                id: 'elevenlabs_agent',
                                name: 'ElevenLabs Agent',
                                desc: 'ElevenLabs conversational AI',
                                doc: 'https://elevenlabs.io/docs/conversational-ai/overview',
                                tooltip: (
                                    <>
                                        <strong>ElevenLabs Conversational AI</strong> — premium voice quality, hosted agent platform.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>~8–10¢/min</li>
                                            <li>Highest TTS voice quality (eleven_flash_v2_5)</li>
                                            <li>Voice cloning supported in ElevenLabs dashboard</li>
                                            <li>Best for English-first deployments</li>
                                            <li>Requires <code>ELEVENLABS_API_KEY</code> + <code>ELEVENLABS_AGENT_ID</code></li>
                                        </ul>
                                    </>
                                ),
                            },
                            {
                                id: 'grok',
                                name: 'xAI Grok Voice Agent',
                                desc: 'Grok Voice (μ-law direct, 5 named voices, 30-min session cap)',
                                doc: 'https://docs.x.ai/developers/model-capabilities/audio/voice-agent',
                                tooltip: (
                                    <>
                                        <strong>xAI Grok Voice Agent</strong> — newest cloud option.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>~5¢/min ($3/hr flat)</li>
                                            <li>5 named voices (eve, ara, rex, sal, leo) + custom cloned IDs</li>
                                            <li>μ-law direct @ 8 kHz — no resampling for telephony</li>
                                            <li>30-minute hard session cap (xAI limit)</li>
                                            <li>Multi-instance ready (per-tenant keys via secret files)</li>
                                            <li>Requires <code>XAI_API_KEY</code></li>
                                        </ul>
                                    </>
                                ),
                            },
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
                                        {/* Hover for pricing, voice options, env var requirements, doc link. */}
                                        <span
                                            // Tooltip is a button — keep clicks from toggling the parent
                                            // <label>'s checkbox when the user clicks the help icon.
                                            onClick={(e) => e.preventDefault()}
                                        >
                                            <HelpTooltip
                                                content={template.tooltip}
                                                link={template.doc}
                                                linkText="Setup docs"
                                            />
                                        </span>
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
                                    <span onClick={(e) => e.preventDefault()}>
                                        <HelpTooltip
                                            content={
                                                <>
                                                    <strong>Local Modular Stack</strong> — three role-split providers backed by the on-premises Local AI Server.
                                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                        <li>~0.2¢/min (Kroko ASR + Kokoro/Piper TTS)</li>
                                                        <li>100% on-premises — no API egress for audio</li>
                                                        <li>Requires 4+ CPU cores; GPU optional for faster inference</li>
                                                        <li>Use these slots inside a Pipeline (mix &amp; match with cloud roles)</li>
                                                        <li>No API key needed</li>
                                                    </ul>
                                                </>
                                            }
                                            link="https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk#local-hybrid"
                                            linkText="Setup docs"
                                        />
                                    </span>
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
                            {
                                id: 'telnyx_llm',
                                name: 'Telnyx LLM',
                                desc: 'Telnyx AI Inference (OpenAI-compatible /chat/completions)',
                                doc: 'https://developers.telnyx.com/docs/inference/getting-started/index',
                                tooltip: (
                                    <>
                                        <strong>Telnyx LLM</strong> — modular LLM slot. Use inside a Pipeline alongside any STT/TTS.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>OpenAI-compatible <code>/chat/completions</code> endpoint</li>
                                            <li>Default model: <code>Qwen/Qwen3-235B-A22B</code></li>
                                            <li>Pay per token (~$0.40 / 1M input)</li>
                                            <li>Requires <code>TELNYX_API_KEY</code></li>
                                        </ul>
                                    </>
                                ),
                            },
                            {
                                id: 'azure_stt',
                                name: 'Azure STT',
                                desc: 'Microsoft Azure Speech-to-Text (realtime or fast transcription)',
                                doc: 'https://learn.microsoft.com/en-us/azure/ai-services/speech-service/',
                                tooltip: (
                                    <>
                                        <strong>Azure STT</strong> — modular STT slot.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>Two variants: realtime streaming OR fast transcription</li>
                                            <li>100+ languages with auto-detection</li>
                                            <li>~$1/hour realtime; pay-per-second fast transcription</li>
                                            <li>Requires <code>AZURE_SPEECH_KEY</code> + region (e.g. eastus)</li>
                                        </ul>
                                    </>
                                ),
                            },
                            {
                                id: 'azure_tts',
                                name: 'Azure TTS',
                                desc: 'Microsoft Azure Text-to-Speech (neural voices, SSML)',
                                doc: 'https://learn.microsoft.com/en-us/azure/ai-services/speech-service/text-to-speech',
                                tooltip: (
                                    <>
                                        <strong>Azure TTS</strong> — modular TTS slot.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>400+ neural voices across 140 languages</li>
                                            <li>Full SSML support (pitch, rate, emphasis)</li>
                                            <li>~$16 / 1M characters (Neural)</li>
                                            <li>Output formats include μ-law @ 8 kHz for telephony</li>
                                            <li>Requires <code>AZURE_SPEECH_KEY</code> + region</li>
                                        </ul>
                                    </>
                                ),
                            },
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
                                        <span onClick={(e) => e.preventDefault()}>
                                            <HelpTooltip
                                                content={template.tooltip}
                                                link={template.doc}
                                                linkText="Setup docs"
                                            />
                                        </span>
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
