import React, { useState, useEffect } from 'react';
import axios from 'axios';
import yaml from 'js-yaml';
import { sanitizeConfigForSave } from '../utils/configSanitizers';
import { Plus, Settings, Trash2, ArrowRight, Workflow, AlertTriangle, AlertCircle, RefreshCw, Loader2 } from 'lucide-react';
import { ConfigSection } from '../components/ui/ConfigSection';
import { ConfigCard } from '../components/ui/ConfigCard';
import { Modal } from '../components/ui/Modal';
import PipelineForm from '../components/config/PipelineForm';
import { ensureModularKey, isFullAgentProvider } from '../utils/providerNaming';

const PipelinesPage = () => {
    const [config, setConfig] = useState<any>({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [editingPipeline, setEditingPipeline] = useState<string | null>(null);
    const [pipelineForm, setPipelineForm] = useState<any>({});
    const [isNewPipeline, setIsNewPipeline] = useState(false);
    const [pendingRestart, setPendingRestart] = useState(false);
    const [restartingEngine, setRestartingEngine] = useState(false);
    const providers = config?.providers || {};

    const compactModelLabel = (val: any) => {
        if (val == null) return '';
        const str = String(val).trim();
        if (!str) return '';
        const looksLikePath =
            str.includes('models/') ||
            str.startsWith('/') ||
            str.endsWith('.onnx') ||
            str.endsWith('.gguf') ||
            str.endsWith('.bin') ||
            str.endsWith('.pt') ||
            str.endsWith('.pth') ||
            str.endsWith('.tflite');
        if (looksLikePath && str.includes('/')) {
            const parts = str.split('/').filter(Boolean);
            return parts[parts.length - 1] || str;
        }
        return str;
    };

    const getProviderModelLabel = (providerKey: string, role: 'stt' | 'llm' | 'tts', pipeline: any) => {
        const provider = (providerKey && providers && (providers as any)[providerKey]) ? (providers as any)[providerKey] : null;
        if (!provider) return 'default';

        if (role === 'stt') {
            const model = provider?.stt_model || provider?.model || pipeline?.options?.stt?.model || '';
            const label = compactModelLabel(model);
            if (label) return label;
            const explicit = pipeline?.options?.stt?.streaming;
            if (explicit != null) return explicit ? 'Streaming' : 'Buffered';
            return providerKey === 'local_stt' ? 'Streaming' : 'Buffered';
        }

        if (role === 'tts') {
            // Local TTS typically stores the voice/model path in `tts_voice`.
            const modelOrVoice = provider?.tts_model || provider?.model_id || provider?.model || provider?.tts_voice || '';
            const label = compactModelLabel(modelOrVoice);
            const voice = provider?.voice || '';
            if (label && voice && voice !== label) return `${label} (${voice})`;
            if (label) return label;
            return pipeline?.options?.tts?.format?.encoding || 'mulaw';
        }

        const llmLabel =
            provider?.chat_model ||
            provider?.llm_model ||
            provider?.model ||
            pipeline?.options?.llm?.chat_model ||
            pipeline?.options?.llm?.model ||
            '';
        return compactModelLabel(llmLabel) || 'default model';
    };

    const normalizeSttOptions = (sttKey: string, sttOptions: any) => {
        const opts = (sttOptions && typeof sttOptions === 'object') ? sttOptions : {};

        if (sttKey === 'local_stt') {
            return {
                streaming: true,
                chunk_ms: 160,
                stream_format: 'pcm16_16k',
                mode: 'stt',
            };
        }

        const normalized: any = {};
        normalized.chunk_ms = typeof opts.chunk_ms === 'number' ? opts.chunk_ms : 4000;
        if (typeof opts.response_format === 'string') normalized.response_format = opts.response_format;
        if (typeof opts.temperature === 'number') normalized.temperature = opts.temperature;
        if (typeof opts.language === 'string') normalized.language = opts.language;
        if (typeof opts.prompt === 'string') normalized.prompt = opts.prompt;
        if (opts.request_timeout_sec != null) normalized.request_timeout_sec = opts.request_timeout_sec;
        if (opts.timeout_sec != null) normalized.timeout_sec = opts.timeout_sec;
        return normalized;
    };

    useEffect(() => {
        fetchConfig();
    }, []);

    const fetchConfig = async () => {
        try {
            const res = await axios.get('/api/config/yaml');
            const parsed = yaml.load(res.data.content) as any;
            setConfig(parsed || {});
            setError(null);
        } catch (err) {
            console.error('Failed to load config', err);
            const status = (err as any)?.response?.status;
            if (status === 401) {
                setError('Not authenticated. Please refresh and log in again.');
            } else {
                setError('Failed to load configuration. Check backend logs and try again.');
            }
        } finally {
            setLoading(false);
        }
    };

    const saveConfig = async (newConfig: any) => {
        try {
            const sanitized = sanitizeConfigForSave(newConfig);
            await axios.post('/api/config/yaml', { content: yaml.dump(sanitized) });
            setConfig(sanitized);
            setPendingRestart(true);
        } catch (err) {
            console.error('Failed to save config', err);
            alert('Failed to save configuration');
        }
    };

    const handleReloadAIEngine = async (force: boolean = false) => {
        setRestartingEngine(true);
        try {
            // Pipeline changes may require new providers - use restart to ensure they're loaded
            const response = await axios.post(`/api/system/containers/ai_engine/restart?force=${force}`);

            if (response.data.status === 'warning') {
                const confirmForce = window.confirm(
                    `${response.data.message}\n\nDo you want to force restart anyway? This may disconnect active calls.`
                );
                if (confirmForce) {
                    setRestartingEngine(false);
                    return handleReloadAIEngine(true);
                }
                return;
            }

            if (response.data.status === 'degraded') {
                alert(`AI Engine restarted but may not be fully healthy: ${response.data.output || 'Health check issue'}\n\nPlease verify manually.`);
                return;
            }

            if (response.data.status === 'success') {
                setPendingRestart(false);
                alert('AI Engine restarted! Changes are now active.');
            }
        } catch (error: any) {
            alert(`Failed to restart AI Engine: ${error.response?.data?.detail || error.message}`);
        } finally {
            setRestartingEngine(false);
        }
    };

    const handleEditPipeline = (name: string) => {
        setEditingPipeline(name);
        const existing = config.pipelines?.[name] || {};
        const { tools: _legacyTools, ...rest } = (existing && typeof existing === 'object') ? existing : {};
        setPipelineForm({ name, ...rest });
        setIsNewPipeline(false);
    };

    const handleAddPipeline = () => {
        setEditingPipeline('new_pipeline');
        setPipelineForm({
            name: '',
            stt: 'local_stt',
            llm: 'openai_llm',
            tts: 'local_tts',
            options: {
                stt: { streaming: true, chunk_ms: 160, stream_format: 'pcm16_16k' },
                llm: { model: 'gpt-4o-mini', temperature: 0.7, max_tokens: 150 },
                tts: { format: { encoding: 'mulaw', sample_rate: 8000 } }
            }
        });
        setIsNewPipeline(true);
    };

    const handleDeletePipeline = async (name: string) => {
        if (!confirm(`Are you sure you want to delete pipeline "${name}"?`)) return;
        const newPipelines = { ...config.pipelines };
        delete newPipelines[name];
        await saveConfig({ ...config, pipelines: newPipelines });
    };

    const handleSavePipeline = async () => {
        if (!pipelineForm.name) {
            alert('Pipeline name is required');
            return;
        }

        const pipelineName = isNewPipeline ? pipelineForm.name : editingPipeline;
        if (!pipelineName) return;

        const normalizedForm = {
            ...pipelineForm,
            stt: ensureModularKey(pipelineForm.stt || '', 'stt'),
            llm: ensureModularKey(pipelineForm.llm || '', 'llm'),
            tts: ensureModularKey(pipelineForm.tts || '', 'tts'),
        };

        // Tools allowlists belong to Contexts. Strip pipeline-level tools from the saved config.
        if ('tools' in normalizedForm) {
            delete normalizedForm.tools;
        }

        // Validate required components
        if (!normalizedForm.stt || !normalizedForm.llm || !normalizedForm.tts) {
            alert('STT, LLM, and TTS providers are required');
            return;
        }

        // Validate provider existence
        const providers = config.providers || {};
        if (!providers[normalizedForm.stt]) {
            alert(`STT provider '${normalizedForm.stt}' does not exist`);
            return;
        }
        if (!providers[normalizedForm.llm]) {
            alert(`LLM provider '${normalizedForm.llm}' does not exist`);
            return;
        }
        if (!providers[normalizedForm.tts]) {
            alert(`TTS provider '${normalizedForm.tts}' does not exist`);
            return;
        }

        // Block full agents in modular slots
        if (isFullAgentProvider(providers[normalizedForm.stt]) || isFullAgentProvider(providers[normalizedForm.llm]) || isFullAgentProvider(providers[normalizedForm.tts])) {
            alert('Full-agent providers cannot be used in modular pipeline slots. Please select modular providers with a single capability.');
            return;
        }

        // Basic compatibility check: ensure provider capabilities match roles
        const sttCaps = providers[normalizedForm.stt]?.capabilities || [];
        const llmCaps = providers[normalizedForm.llm]?.capabilities || [];
        const ttsCaps = providers[normalizedForm.tts]?.capabilities || [];
        if (sttCaps.length && !sttCaps.includes('stt')) {
            alert(`Provider '${normalizedForm.stt}' is not marked as STT-capable.`);
            return;
        }
        if (llmCaps.length && !llmCaps.includes('llm')) {
            alert(`Provider '${normalizedForm.llm}' is not marked as LLM-capable.`);
            return;
        }
        if (ttsCaps.length && !ttsCaps.includes('tts')) {
            alert(`Provider '${normalizedForm.tts}' is not marked as TTS-capable.`);
            return;
        }

        // Check for disabled providers
        const components = ['stt', 'llm', 'tts'];
        const disabledComponents: string[] = [];

        components.forEach(comp => {
            const providerName = normalizedForm[comp];
            if (providerName && providers[providerName] && providers[providerName].enabled === false) {
                disabledComponents.push(`${comp.toUpperCase()}: ${providerName}`);
            }
        });

        if (disabledComponents.length > 0) {
            alert(`Cannot save pipeline. The following providers are disabled:\n- ${disabledComponents.join('\n- ')}\n\nPlease enable them in the Providers page first.`);
            return;
        }

        const newConfig = { ...config };
        if (!newConfig.pipelines) newConfig.pipelines = {};

        const { name, ...pipelineData } = normalizedForm;

        // Merge with existing config
        const existingData = !isNewPipeline && config.pipelines ? config.pipelines[pipelineName] : {};
        const mergedPipeline = { ...existingData, ...pipelineData };
        // Tools are configured per-context only (pipelines.*.tools is deprecated).
        delete (mergedPipeline as any).tools;

        // If the user swaps a component provider, provider-specific option keys from the old provider can linger
        // (e.g., Groq TTS voice "hannah" carried into OpenAI TTS and causing silent greetings).
        // Keep only portable TTS options when TTS provider changes.
        if (!isNewPipeline && existingData?.tts && mergedPipeline.tts && existingData.tts !== mergedPipeline.tts) {
            const existingTtsOpts = (existingData.options || {}).tts || {};
            const nextTtsOpts = (mergedPipeline.options || {}).tts || existingTtsOpts;
            const portable: any = {};
            if (nextTtsOpts && typeof nextTtsOpts === 'object') {
                if (nextTtsOpts.format) portable.format = nextTtsOpts.format;
                if (nextTtsOpts.response_format) portable.response_format = nextTtsOpts.response_format;
                if (nextTtsOpts.chunk_size_ms != null) portable.chunk_size_ms = nextTtsOpts.chunk_size_ms;
                if (nextTtsOpts.timeout_sec != null) portable.timeout_sec = nextTtsOpts.timeout_sec;
                if (nextTtsOpts.response_timeout_sec != null) portable.response_timeout_sec = nextTtsOpts.response_timeout_sec;
                if (nextTtsOpts.mode) portable.mode = nextTtsOpts.mode;
            }
            mergedPipeline.options = { ...(mergedPipeline.options || {}), tts: portable };
        }

        // Keep only portable STT options when STT provider changes (avoid carrying provider-specific models).
        if (!isNewPipeline && existingData?.stt && mergedPipeline.stt && existingData.stt !== mergedPipeline.stt) {
            const existingSttOpts = (existingData.options || {}).stt || {};
            const nextSttOpts = (mergedPipeline.options || {}).stt || existingSttOpts;
            mergedPipeline.options = { ...(mergedPipeline.options || {}), stt: normalizeSttOptions(mergedPipeline.stt, nextSttOpts) };
        }

        // Keep only portable LLM options when LLM provider changes (avoid carrying provider-specific base_url/model).
        if (!isNewPipeline && existingData?.llm && mergedPipeline.llm && existingData.llm !== mergedPipeline.llm) {
            const existingLlmOpts = (existingData.options || {}).llm || {};
            const nextLlmOpts = (mergedPipeline.options || {}).llm || existingLlmOpts;
            const portable: any = {};
            if (nextLlmOpts && typeof nextLlmOpts === 'object') {
                if (nextLlmOpts.max_tokens != null) portable.max_tokens = nextLlmOpts.max_tokens;
                if (nextLlmOpts.temperature != null) portable.temperature = nextLlmOpts.temperature;
                if (nextLlmOpts.top_p != null) portable.top_p = nextLlmOpts.top_p;
                if (nextLlmOpts.top_k != null) portable.top_k = nextLlmOpts.top_k;
                if (nextLlmOpts.response_timeout_sec != null) portable.response_timeout_sec = nextLlmOpts.response_timeout_sec;
                if (nextLlmOpts.timeout_sec != null) portable.timeout_sec = nextLlmOpts.timeout_sec;
                if (nextLlmOpts.mode) portable.mode = nextLlmOpts.mode;
            }
            mergedPipeline.options = { ...(mergedPipeline.options || {}), llm: portable };
        }

        // Always normalize STT options for the selected STT provider. This prevents stale cloud STT keys
        // (e.g., response_format/temperature/chunk_ms=4000) from breaking local_stt when users swap providers.
        mergedPipeline.options = { ...(mergedPipeline.options || {}) };
        mergedPipeline.options.stt = normalizeSttOptions(mergedPipeline.stt, (mergedPipeline.options || {}).stt);

        newConfig.pipelines[pipelineName] = mergedPipeline;

        await saveConfig(newConfig);
        setEditingPipeline(null);
    };

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading configuration...</div>;

    return (
        <div className="space-y-6">
            <div className={`${pendingRestart ? 'bg-orange-500/15 border-orange-500/30' : 'bg-yellow-500/10 border-yellow-500/20'} border text-yellow-600 dark:text-yellow-500 p-4 rounded-md flex items-center justify-between`}>
                <div className="flex items-center">
                    <AlertCircle className="w-5 h-5 mr-2" />
                    Changes to pipeline configurations require an AI Engine restart to take effect.
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
                    {restartingEngine ? 'Restarting...' : 'Reload AI Engine'}
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
                    <h1 className="text-3xl font-bold tracking-tight">Pipelines</h1>
                    <p className="text-muted-foreground mt-1">
                        Define data flow pipelines (Input → Processors → Output).
                    </p>
                </div>
                <button
                    onClick={handleAddPipeline}
                    className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                >
                    <Plus className="w-4 h-4 mr-2" />
                    Add Pipeline
                </button>
            </div>

            <ConfigSection title="Active Pipeline" description="Select the pipeline to use for incoming calls.">
                <ConfigCard>
                    <div className="flex items-center space-x-4">
                        <select
                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                            value={config.active_pipeline || ''}
                            onChange={(e) => saveConfig({ ...config, active_pipeline: e.target.value })}
                        >
                            <option value="" disabled>Select a pipeline...</option>
                            {Object.keys(config.pipelines || {}).map((name) => (
                                <option key={name} value={name}>{name}</option>
                            ))}
                        </select>
                        <button
                            onClick={() => saveConfig({ ...config, active_pipeline: config.active_pipeline })}
                            className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-secondary text-secondary-foreground hover:bg-secondary/80 h-10 px-4 py-2"
                        >
                            Set Active
                        </button>
                    </div>
                </ConfigCard>
            </ConfigSection>

            <ConfigSection title="Active Pipelines" description="Configure how audio streams are processed.">
                <div className="grid grid-cols-1 gap-4">
                    {Object.entries(config.pipelines || {}).map(([name, pipeline]: [string, any]) => (
                        <ConfigCard key={name} className="group relative hover:border-primary/50 transition-colors">
                            <div className="flex justify-between items-start mb-4">
                                <div className="flex items-center gap-3">
                                    <div className="p-2 bg-secondary rounded-md">
                                        <Workflow className="w-5 h-5 text-primary" />
                                    </div>
                                    <h4 className="font-semibold text-lg">{name}</h4>
                                    {config.active_pipeline === name && (
                                        <span className="ml-2 px-2 py-0.5 rounded-full text-xs font-medium bg-green-500/10 text-green-500 flex items-center gap-1">
                                            <div className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse"></div>
                                            Active
                                        </span>
                                    )}
                                </div>
                                <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                    <button
                                        onClick={() => handleEditPipeline(name)}
                                        className="p-2 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground"
                                    >
                                        <Settings className="w-4 h-4" />
                                    </button>
                                    <button
                                        onClick={() => handleDeletePipeline(name)}
                                        className="p-2 hover:bg-destructive/10 rounded-md text-destructive"
                                    >
                                        <Trash2 className="w-4 h-4" />
                                    </button>
                                </div>
                            </div>

                            <div className="flex items-center space-x-2 text-sm overflow-x-auto pb-2 scrollbar-thin scrollbar-thumb-secondary">
                                {/* STT Node */}
                                <div className="flex flex-col items-center p-3 bg-secondary/50 rounded-lg min-w-[120px] border border-border">
                                    <span className="font-semibold text-xs uppercase tracking-wider text-muted-foreground mb-1">STT</span>
                                    <span className="font-medium">{pipeline.stt || 'default'}</span>
                                    <span className="text-xs text-muted-foreground mt-1">
                                        {getProviderModelLabel(pipeline.stt || '', 'stt', pipeline)}
                                    </span>
                                </div>

                                <ArrowRight className="w-4 h-4 text-muted-foreground flex-shrink-0" />

                                {/* LLM Node */}
                                <div className="flex flex-col items-center p-3 bg-accent/50 rounded-lg min-w-[120px] border border-accent-foreground/10">
                                    <span className="font-semibold text-xs uppercase tracking-wider text-primary mb-1">LLM</span>
                                    <span className="font-medium">{pipeline.llm || 'default'}</span>
                                    <span className="text-xs text-muted-foreground mt-1">
                                        {getProviderModelLabel(pipeline.llm || '', 'llm', pipeline)}
                                    </span>
                                </div>

                                <ArrowRight className="w-4 h-4 text-muted-foreground flex-shrink-0" />

                                {/* TTS Node */}
                                <div className="flex flex-col items-center p-3 bg-secondary/50 rounded-lg min-w-[120px] border border-border">
                                    <span className="font-semibold text-xs uppercase tracking-wider text-muted-foreground mb-1">TTS</span>
                                    <span className="font-medium">{pipeline.tts || 'default'}</span>
                                    <span className="text-xs text-muted-foreground mt-1">
                                        {getProviderModelLabel(pipeline.tts || '', 'tts', pipeline)}
                                    </span>
                                </div>
                            </div>

                            {name === 'local_only' && (
                                <div className="mt-3 p-2 bg-yellow-500/10 border border-yellow-500/20 rounded text-xs text-yellow-600 dark:text-yellow-400 flex items-start gap-2">
                                    <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                                    <div>
                                        <strong>Hardware Warning:</strong> This pipeline runs entirely on your local machine.
                                        Ensure you have sufficient RAM (8GB+) and CPU/GPU resources.
                                    </div>
                                </div>
                            )}
                        </ConfigCard>
                    ))}
                    {Object.keys(config.pipelines || {}).length === 0 && (
                        <div className="col-span-full p-8 border border-dashed rounded-lg text-center text-muted-foreground">
                            No pipelines configured. Click "Add Pipeline" to create one.
                        </div>
                    )}
                </div>
            </ConfigSection >

            <Modal
                isOpen={!!editingPipeline}
                onClose={() => setEditingPipeline(null)}
                title={isNewPipeline ? 'Add Pipeline' : 'Edit Pipeline'}
                size="xl"
                footer={
                    <>
                        <button
                            onClick={() => setEditingPipeline(null)}
                            className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2"
                        >
                            Cancel
                        </button>
                        <button
                            onClick={handleSavePipeline}
                            className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                        >
                            Save Changes
                        </button>
                    </>
                }
            >
                <PipelineForm
                    config={pipelineForm}
                    providers={config.providers}
                    onChange={setPipelineForm}
                    isNew={isNewPipeline}
                />
            </Modal>
        </div >
    );
};

export default PipelinesPage;
