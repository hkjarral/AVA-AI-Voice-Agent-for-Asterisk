import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import yaml from 'js-yaml';
import { Save, Brain, AlertCircle, RefreshCw, Loader2 } from 'lucide-react';
import { YamlErrorBanner, YamlErrorInfo } from '../../components/ui/YamlErrorBanner';
import { ConfigSection } from '../../components/ui/ConfigSection';
import { ConfigCard } from '../../components/ui/ConfigCard';
import { FormInput } from '../../components/ui/FormComponents';
import HelpTooltip from '../../components/ui/HelpTooltip';
import { sanitizeConfigForSave } from '../../utils/configSanitizers';

const CHAT_FORMAT_OPTIONS = [
    { value: '', label: '(Legacy Phi-style — no chat template)' },
    { value: 'chatml', label: 'ChatML (Phi-3, Qwen, Hermes, TinyLlama, Command-R)' },
    { value: 'llama-3', label: 'Llama 3 (Llama 3.x family)' },
    { value: 'mistral-instruct', label: 'Mistral Instruct (Mistral, Nemo)' },
    { value: 'gemma', label: 'Gemma (Google Gemma 2)' },
    { value: 'functionary-v2', label: 'Functionary v2 (tool-calling tuned)' },
];

const LLMPage = () => {
    const [config, setConfig] = useState<any>({});
    const [env, setEnv] = useState<Record<string, string>>({});
    const [loading, setLoading] = useState(true);
    const [yamlError, setYamlError] = useState<YamlErrorInfo | null>(null);
    const [saving, setSaving] = useState(false);
    const [pendingRestart, setPendingRestart] = useState(false);
    const [restartingEngine, setRestartingEngine] = useState(false);
    const [localCapability, setLocalCapability] = useState<any>(null);
    const [localConnected, setLocalConnected] = useState(false);

    useEffect(() => {
        fetchConfig();
    }, []);

    const fetchConfig = async () => {
        try {
            const [yamlRes, healthRes, envRes] = await Promise.allSettled([
                axios.get('/api/config/yaml'),
                axios.get('/api/system/health'),
                axios.get('/api/config/env'),
            ]);

            if (yamlRes.status !== 'fulfilled') {
                throw new Error('Failed to load yaml');
            }

            const res = yamlRes.value;
            if (res.data.yaml_error) {
                setYamlError(res.data.yaml_error);
                setConfig({});
            } else {
                const parsed = yaml.load(res.data.content) as any;
                setConfig(parsed || {});
                setYamlError(null);
            }

            if (envRes.status === 'fulfilled') {
                setEnv(envRes.value.data || {});
            }

            if (healthRes.status === 'fulfilled') {
                const localDetails = healthRes.value.data?.local_ai_server?.details || {};
                setLocalConnected(healthRes.value.data?.local_ai_server?.status === 'connected');
                setLocalCapability(localDetails?.models?.llm?.tool_capability || null);
            } else {
                setLocalConnected(false);
                setLocalCapability(null);
            }
        } catch (err) {
            console.error('Failed to load config', err);
            setYamlError(null);
            setLocalConnected(false);
            setLocalCapability(null);
        } finally {
            setLoading(false);
        }
    };

    const updateEnv = (key: string, value: string) => {
        setEnv(prev => ({ ...prev, [key]: value }));
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            const sanitized = sanitizeConfigForSave(config);
            const [yamlSave, envSave] = await Promise.allSettled([
                axios.post('/api/config/yaml', { content: yaml.dump(sanitized) }),
                axios.post('/api/config/env', env),
            ]);

            const yamlOk = yamlSave.status === 'fulfilled';
            const envOk = envSave.status === 'fulfilled';
            if (yamlOk || envOk) {
                setPendingRestart(true);
            }
            if (!yamlOk || !envOk) {
                const yamlState = yamlOk ? 'ok' : 'failed';
                const envState = envOk ? 'ok' : 'failed';
                throw new Error(`Partial save detected (yaml=${yamlState}, env=${envState})`);
            }

            toast.success('LLM configuration saved');
        } catch (err: any) {
            console.error('Failed to save config', err);
            toast.error('Failed to save configuration', {
                description: err?.message || 'Unknown error',
            });
        } finally {
            setSaving(false);
        }
    };

    const handleReloadAIEngine = async (force: boolean = false) => {
        setRestartingEngine(true);
        try {
            // Use restart to ensure all changes are picked up
            const response = await axios.post(`/api/system/containers/ai_engine/restart?force=${force}`);

            if (response.data.status === 'warning') {
                if (!force) {
                    const confirmForce = window.confirm(
                        `${response.data.message}\n\nDo you want to force restart anyway? This may disconnect active calls.`
                    );
                    if (confirmForce) {
                        await handleReloadAIEngine(true);
                    }
                    return;
                }
                toast.warning(response.data.message, { description: 'Force restart is still blocked.' });
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

    const updateLLMConfig = (field: string, value: any) => {
        setConfig({
            ...config,
            llm: {
                ...config.llm,
                [field]: value
            }
        });
    };

    const updateLocalProviderConfig = (field: string, value: any) => {
        const providers = config.providers || {};
        const local = providers.local || {};
        setConfig({
            ...config,
            providers: {
                ...providers,
                local: {
                    ...local,
                    [field]: value
                }
            }
        });
    };

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading configuration...</div>;

    if (yamlError) return (
        <div className="space-y-6">
            <YamlErrorBanner error={yamlError} />
        </div>
    );

    const llmConfig = config.llm || {};
    const localProviderConfig = config.providers?.local || {};
    const configuredToolPolicy = String(localProviderConfig.tool_call_policy || 'auto').trim().toLowerCase();
    const toolCapabilityLevel = String(localCapability?.level || 'unknown').trim().toLowerCase();
    const resolvedToolPolicy = configuredToolPolicy !== 'auto'
        ? configuredToolPolicy
        : (toolCapabilityLevel === 'strict' ? 'strict' : toolCapabilityLevel === 'none' ? 'off' : 'compatible');
    const policyMismatchWarning = (
        configuredToolPolicy !== 'auto' &&
        ((configuredToolPolicy === 'strict' && toolCapabilityLevel === 'none') ||
            (configuredToolPolicy === 'off' && toolCapabilityLevel === 'strict'))
    );

    return (
        <div className="space-y-6">
            <div className={`${pendingRestart ? 'bg-orange-500/15 border-orange-500/30' : 'bg-yellow-500/10 border-yellow-500/20'} border text-yellow-600 dark:text-yellow-500 p-4 rounded-md flex items-center justify-between`}>
                <div className="flex items-center">
                    <AlertCircle className="w-5 h-5 mr-2" />
                    LLM configuration changes require an AI Engine restart to take effect.
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

            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">LLM Defaults</h1>
                    <p className="text-muted-foreground mt-1">
                        Set default parameters for Large Language Model interactions.
                    </p>
                </div>
                <button
                    onClick={handleSave}
                    disabled={saving}
                    className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                >
                    <Save className="w-4 h-4 mr-2" />
                    {saving ? 'Saving...' : 'Save Changes'}
                </button>
            </div>

            <ConfigSection title="Default Parameters" description="Fallback settings when not specified by a context.">
                <ConfigCard>
                    <div className="space-y-6">
                        <FormInput
                            label="Initial Greeting"
                            value={llmConfig.initial_greeting || ''}
                            onChange={(e) => updateLLMConfig('initial_greeting', e.target.value)}
                            placeholder="Hello, how can I help you today?"
                            tooltip="The first message spoken by the AI when the call starts."
                        />
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70">
                                    System Prompt
                                </label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>System Prompt</strong> — core instructions defining the AI's persona, role, and behavior. Sent as the <code>system</code> message at the start of every conversation.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>Overridden by a context's <code>prompt</code> when one is matched.</li>
                                                <li>Keep it focused; long prompts eat into the LLM context window.</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <textarea
                                className="flex min-h-[120px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                                value={llmConfig.prompt || ''}
                                onChange={(e) => updateLLMConfig('prompt', e.target.value)}
                                placeholder="You are a helpful AI assistant..."
                            />
                            <p className="text-xs text-muted-foreground">
                                The core personality and instructions for the AI.
                            </p>
                        </div>
                    </div>
                </ConfigCard>
            </ConfigSection>

            <ConfigSection title="Local LLM Prompting" description="Chat template and voice preamble for the local LLM (llama-cpp). Requires local_ai_server restart.">
                <ConfigCard>
                    <div className="space-y-6">
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium leading-none">Chat Format</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Chat Format</strong> — the prompt template <code>llama-cpp-python</code> uses for <code>create_chat_completion()</code>. Each model family expects a specific format (special tokens, role markers).
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>Auto-set when you pick a model on the Models page.</li>
                                                <li>Wrong format = garbled / looping output.</li>
                                                <li>Leave empty for legacy raw Phi-style prompting.</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <select
                                className="w-full p-2 rounded border border-input bg-background text-sm"
                                value={env['LOCAL_LLM_CHAT_FORMAT'] || ''}
                                onChange={(e) => updateEnv('LOCAL_LLM_CHAT_FORMAT', e.target.value)}
                            >
                                {CHAT_FORMAT_OPTIONS.map(opt => (
                                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                                ))}
                            </select>
                            <p className="text-xs text-muted-foreground">
                                Determines the prompt template used by <code>create_chat_completion()</code>.
                                Auto-set when selecting a model on the Models page. Leave empty for legacy Phi-style raw prompting.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium leading-none">Voice Preamble</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Voice Preamble</strong> — meta-instructions prepended to the system prompt so the local LLM produces voice-friendly output.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>No markdown / bullets / headings (TTS would read them literally).</li>
                                                <li>Encourages short, conversational replies.</li>
                                                <li>Applied on every call for the local provider.</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <textarea
                                className="flex min-h-[100px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                                value={env['LOCAL_LLM_VOICE_PREAMBLE'] || ''}
                                onChange={(e) => updateEnv('LOCAL_LLM_VOICE_PREAMBLE', e.target.value)}
                                placeholder="You are a voice assistant on a phone call. Keep responses short and conversational. Do not use markdown, bullet points, numbered lists, or any visual formatting. Speak naturally as if talking to someone on the phone."
                            />
                            <p className="text-xs text-muted-foreground">
                                Meta-instructions prepended to the system prompt so local LLMs produce voice-friendly output
                                (no markdown, concise, natural speech). Applied to every call.
                            </p>
                        </div>
                    </div>
                </ConfigCard>
            </ConfigSection>

            <ConfigSection title="Local Tool Calling" description="Full-local provider tool execution policy and structured gateway controls.">
                <ConfigCard>
                    <div className="space-y-4">
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <div className="flex items-center gap-1.5">
                                    <label className="text-sm font-medium">Tool Policy Override</label>
                                    <HelpTooltip
                                        content={
                                            <>
                                                <strong>Tool Policy Override</strong> — how aggressively the full-local provider attempts tool calls.
                                                <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                    <li><code>auto</code> — pick based on model capability probe (recommended).</li>
                                                    <li><code>strict</code> — only structured tool-decision path.</li>
                                                    <li><code>compatible</code> — structured + parser/repair fallback for weaker models.</li>
                                                    <li><code>off</code> — disable model tool execution entirely.</li>
                                                </ul>
                                            </>
                                        }
                                    />
                                </div>
                                <select
                                    className="w-full p-2 rounded border border-input bg-background"
                                    value={configuredToolPolicy || 'auto'}
                                    onChange={(e) => updateLocalProviderConfig('tool_call_policy', e.target.value)}
                                >
                                    <option value="auto">Auto (recommended)</option>
                                    <option value="strict">Strict</option>
                                    <option value="compatible">Compatible</option>
                                    <option value="off">Off</option>
                                </select>
                                <p className="text-xs text-muted-foreground">
                                    Auto resolves from model capability. Override only when testing model-specific behavior.
                                </p>
                                <div className="rounded border border-border bg-muted/20 p-2 text-xs text-muted-foreground space-y-1">
                                    <p><span className="font-medium text-foreground">Auto:</span> Uses capability probe result (`strict`, `partial`, `none`).</p>
                                    <p><span className="font-medium text-foreground">Strict:</span> Requires structured tool decision path for full-local calls.</p>
                                    <p><span className="font-medium text-foreground">Compatible:</span> Structured decision with parser/repair fallback for weaker models.</p>
                                    <p><span className="font-medium text-foreground">Off:</span> Disables model tool execution for full-local provider.</p>
                                </div>
                            </div>
                            <div className="space-y-2">
                                <div className="flex items-center gap-1.5">
                                    <label className="text-sm font-medium">Structured Tool Gateway</label>
                                    <HelpTooltip
                                        content={
                                            <>
                                                <strong>Structured Tool Gateway</strong> — runs a dedicated full-local tool-decision pass separate from spoken-response parsing.
                                                <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                    <li>Helps weaker LLMs reliably emit tool calls without polluting the spoken reply.</li>
                                                    <li>STT-only and TTS-only modular pipelines are unaffected.</li>
                                                </ul>
                                            </>
                                        }
                                    />
                                </div>
                                <label className="flex items-center gap-2 p-2 rounded border border-input bg-background">
                                    <input
                                        type="checkbox"
                                        checked={Boolean(localProviderConfig.tool_gateway_enabled ?? true)}
                                        onChange={(e) => updateLocalProviderConfig('tool_gateway_enabled', e.target.checked)}
                                    />
                                    <span className="text-sm">Enable for full-local provider only</span>
                                </label>
                                <p className="text-xs text-muted-foreground">
                                    Enables a dedicated full-local tool decision pass (separate from spoken response parsing).
                                </p>
                                <p className="text-xs text-muted-foreground">
                                    STT-only and TTS-only modular paths are not changed by this switch.
                                </p>
                            </div>
                        </div>

                        <div className="rounded-md border border-border bg-muted/20 p-3 text-xs space-y-1">
                            <div className="flex items-center justify-between gap-2">
                                <span className="text-muted-foreground">Local AI Server</span>
                                <span className={`font-mono ${localConnected ? 'text-green-600' : 'text-yellow-600'}`}>
                                    {localConnected ? 'connected' : 'not-connected'}
                                </span>
                            </div>
                            <div className="flex items-center justify-between gap-2">
                                <span className="text-muted-foreground">Capability</span>
                                <span className="font-mono">{toolCapabilityLevel || 'unknown'}</span>
                            </div>
                            <div className="flex items-center justify-between gap-2">
                                <span className="text-muted-foreground">Resolved policy</span>
                                <span className="font-mono">{resolvedToolPolicy}</span>
                            </div>
                        </div>

                        {policyMismatchWarning && (
                            <div className="text-xs rounded-md border border-yellow-500/40 bg-yellow-500/10 px-3 py-2 text-yellow-700 dark:text-yellow-300">
                                Current override may not match detected model capability. Use Auto unless you are intentionally forcing this mode.
                            </div>
                        )}
                    </div>
                </ConfigCard>
            </ConfigSection>
        </div>
    );
};

export default LLMPage;
