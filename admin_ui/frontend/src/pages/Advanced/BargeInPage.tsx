import { useState, useEffect } from 'react';
import axios from 'axios';
import yaml from 'js-yaml';
import { Save, Zap, AlertCircle, RefreshCw, Loader2 } from 'lucide-react';
import { ConfigSection } from '../../components/ui/ConfigSection';
import { ConfigCard } from '../../components/ui/ConfigCard';
import { FormInput, FormSwitch } from '../../components/ui/FormComponents';
import { sanitizeConfigForSave } from '../../utils/configSanitizers';

const BargeInPage = () => {
    const [config, setConfig] = useState<any>({});
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [pendingRestart, setPendingRestart] = useState(false);
    const [restartingEngine, setRestartingEngine] = useState(false);

    useEffect(() => {
        fetchConfig();
    }, []);

    const fetchConfig = async () => {
        try {
            const res = await axios.get('/api/config/yaml');
            const parsed = yaml.load(res.data.content) as any;
            setConfig(parsed || {});
        } catch (err) {
            console.error('Failed to load config', err);
        } finally {
            setLoading(false);
        }
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            const sanitized = sanitizeConfigForSave(config);
            await axios.post('/api/config/yaml', { content: yaml.dump(sanitized) });
            setPendingRestart(true);
            alert('Barge-in configuration saved successfully');
        } catch (err) {
            console.error('Failed to save config', err);
            alert('Failed to save configuration');
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

    const updateBargeInConfig = (field: string, value: any) => {
        setConfig({
            ...config,
            barge_in: {
                ...config.barge_in,
                [field]: value
            }
        });
    };

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading configuration...</div>;

    const bargeInConfig = config.barge_in || {};
    const providerFallbackProviders = Array.isArray(bargeInConfig.provider_fallback_providers)
        ? (bargeInConfig.provider_fallback_providers as string[]).filter(Boolean)
        : [];
    const providerFallbackProvidersStr = providerFallbackProviders.join(', ');

    return (
        <div className="space-y-6">
            <div className={`${pendingRestart ? 'bg-orange-500/15 border-orange-500/30' : 'bg-yellow-500/10 border-yellow-500/20'} border text-yellow-600 dark:text-yellow-500 p-4 rounded-md flex items-center justify-between`}>
                <div className="flex items-center">
                    <AlertCircle className="w-5 h-5 mr-2" />
                    Changes to barge-in configurations require an AI Engine restart to take effect.
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

            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Barge-in Settings</h1>
                    <p className="text-muted-foreground mt-1">
                        Configure how callers can interrupt the AI agent during responses.
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

            <ConfigSection 
                title="Barge-in Control" 
                description="Allow callers to interrupt the AI while it's speaking."
            >
                <ConfigCard>
                    <div className="space-y-6">
                        <FormSwitch
                            label="Enable Barge-in"
                            description="Allow users to interrupt the AI agent during TTS playback."
                            tooltip="When enabled, the engine immediately flushes/stops local agent audio when it detects caller speech during an agent response."
                            checked={bargeInConfig.enabled ?? true}
                            onChange={(e) => updateBargeInConfig('enabled', e.target.checked)}
                        />

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <FormInput
                                label="Energy Threshold"
                                type="number"
                                value={bargeInConfig.energy_threshold ?? 1000}
                                onChange={(e) => updateBargeInConfig('energy_threshold', parseInt(e.target.value))}
                                tooltip="Caller energy threshold (RMS over PCM16). Higher = less sensitive (fewer false barge-ins), lower = more sensitive (better for quiet callers)."
                            />
                            <FormInput
                                label="Minimum Duration (ms)"
                                type="number"
                                value={bargeInConfig.min_ms ?? 250}
                                onChange={(e) => updateBargeInConfig('min_ms', parseInt(e.target.value))}
                                tooltip="Minimum sustained caller speech time required before triggering barge-in. Higher reduces false triggers but feels less responsive."
                            />
                            <FormInput
                                label="Cooldown (ms)"
                                type="number"
                                value={bargeInConfig.cooldown_ms ?? 500}
                                onChange={(e) => updateBargeInConfig('cooldown_ms', parseInt(e.target.value))}
                                tooltip="Minimum time between barge-in triggers. Prevents repeated triggers from echo/noise after an interruption."
                            />
                            <FormInput
                                label="Post-TTS Protection (ms)"
                                type="number"
                                value={bargeInConfig.post_tts_end_protection_ms ?? 250}
                                onChange={(e) => updateBargeInConfig('post_tts_end_protection_ms', parseInt(e.target.value))}
                                tooltip="Guard window after agent audio ends. Helps avoid self-echo or tail audio being mistaken as caller speech."
                            />
                            <FormInput
                                label="Provider Output Suppress (ms)"
                                type="number"
                                value={bargeInConfig.provider_output_suppress_ms ?? 1200}
                                onChange={(e) => updateBargeInConfig('provider_output_suppress_ms', parseInt(e.target.value))}
                                tooltip="After a barge-in, locally suppress provider audio briefly so previously generated speech doesn’t “resume” mid-sentence."
                            />
                        </div>

                        <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
                            <div className="flex items-start">
                                <Zap className="w-5 h-5 text-blue-600 dark:text-blue-400 mt-0.5 mr-3 flex-shrink-0" />
                                <div className="text-sm text-blue-700 dark:text-blue-300">
                                    <p className="font-medium mb-1">Tuning Tips</p>
                                    <ul className="list-disc list-inside space-y-1">
                                        <li><strong>Energy Threshold:</strong> Increase if barge-in is too sensitive (500-1200 typical)</li>
                                        <li><strong>Provider Output Suppress:</strong> Increase if provider resumes speaking pre-barge audio (800-1600ms typical)</li>
                                        <li><strong>Post-TTS Protection:</strong> Increase if you see immediate re-triggers after TTS ends (200-600ms typical)</li>
                                    </ul>
                                </div>
                            </div>
                        </div>
                    </div>
                </ConfigCard>
            </ConfigSection>

            <ConfigSection 
                title="Advanced"
                description="Additional knobs for provider-owned vs pipeline modes."
            >
                <ConfigCard>
                    <details className="space-y-4">
                        <summary className="cursor-pointer text-sm font-medium">Show advanced settings</summary>
                        <div className="space-y-8 pt-4">
                            <div className="space-y-4">
                                <div className="text-sm font-medium">Protection windows</div>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                    <FormInput
                                        label="Initial Protection (ms)"
                                        type="number"
                                        value={bargeInConfig.initial_protection_ms ?? 200}
                                        onChange={(e) => updateBargeInConfig('initial_protection_ms', parseInt(e.target.value))}
                                        tooltip="Short guard window at the start of agent output to avoid triggering on initial burst/codec artifacts."
                                    />
                                    <FormInput
                                        label="Greeting Protection (ms)"
                                        type="number"
                                        value={bargeInConfig.greeting_protection_ms ?? 0}
                                        onChange={(e) => updateBargeInConfig('greeting_protection_ms', parseInt(e.target.value))}
                                        tooltip="Extra guard window during the initial greeting turn (useful if greetings are short and prone to false triggers)."
                                    />
                                </div>
                            </div>

                            <div className="space-y-4">
                                <div className="text-sm font-medium">Provider-owned mode</div>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                    <FormSwitch
                                        label="Provider Fallback Enabled"
                                        description="Use local VAD fallback only for providers that don’t emit explicit interruption events."
                                        tooltip="If enabled, the engine can trigger barge-in using local VAD only after media is confirmed and only for the providers listed below."
                                        checked={bargeInConfig.provider_fallback_enabled ?? true}
                                        onChange={(e) => updateBargeInConfig('provider_fallback_enabled', e.target.checked)}
                                    />
                                    <FormInput
                                        label="Provider Fallback Providers"
                                        type="text"
                                        value={providerFallbackProvidersStr}
                                        onChange={(e) =>
                                            updateBargeInConfig(
                                                'provider_fallback_providers',
                                                (e.target.value || '')
                                                    .split(',')
                                                    .map((s) => s.trim())
                                                    .filter(Boolean)
                                            )
                                        }
                                        tooltip="Comma-separated provider names where local fallback may apply (e.g., google_live, deepgram)."
                                    />
                                    <FormInput
                                        label="Suppress Extend (ms)"
                                        type="number"
                                        value={bargeInConfig.provider_output_suppress_extend_ms ?? 600}
                                        onChange={(e) => updateBargeInConfig('provider_output_suppress_extend_ms', parseInt(e.target.value))}
                                        tooltip="While caller keeps speaking after a barge-in, extend suppression so agent doesn’t resume too early."
                                    />
                                    <FormInput
                                        label="Chunk Extend (ms)"
                                        type="number"
                                        value={bargeInConfig.provider_output_suppress_chunk_extend_ms ?? 250}
                                        onChange={(e) => updateBargeInConfig('provider_output_suppress_chunk_extend_ms', parseInt(e.target.value))}
                                        tooltip="While suppressed, extend suppression when provider chunks keep arriving (prevents tail audio from restarting output)."
                                    />
                                </div>
                            </div>

                            <div className="space-y-4">
                                <div className="text-sm font-medium">Pipeline / local_hybrid mode</div>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                    <FormSwitch
                                        label="Enable TALK_DETECT"
                                        description="Use Asterisk TALK_DETECT for robust barge-in during local file playback."
                                        tooltip="Recommended for local_hybrid: Asterisk DSP detects caller speech even during ARI file playback."
                                        checked={bargeInConfig.pipeline_talk_detect_enabled ?? true}
                                        onChange={(e) => updateBargeInConfig('pipeline_talk_detect_enabled', e.target.checked)}
                                    />
                                    <FormInput
                                        label="Pipeline Min Duration (ms)"
                                        type="number"
                                        value={bargeInConfig.pipeline_min_ms ?? 120}
                                        onChange={(e) => updateBargeInConfig('pipeline_min_ms', parseInt(e.target.value))}
                                        tooltip="Pipeline-specific minimum speech duration (more sensitive than provider-owned mode)."
                                    />
                                    <FormInput
                                        label="Pipeline Energy Threshold"
                                        type="number"
                                        value={bargeInConfig.pipeline_energy_threshold ?? 300}
                                        onChange={(e) => updateBargeInConfig('pipeline_energy_threshold', parseInt(e.target.value))}
                                        tooltip="Pipeline-specific energy threshold (more sensitive than provider-owned mode)."
                                    />
                                    <FormInput
                                        label="TALK_DETECT Silence (ms)"
                                        type="number"
                                        value={bargeInConfig.pipeline_talk_detect_silence_ms ?? 1200}
                                        onChange={(e) => updateBargeInConfig('pipeline_talk_detect_silence_ms', parseInt(e.target.value))}
                                        tooltip="Asterisk TALK_DETECT(set) silence threshold in ms. Higher treats more audio as ‘silence’."
                                    />
                                    <FormInput
                                        label="TALK_DETECT Talking Threshold"
                                        type="number"
                                        value={bargeInConfig.pipeline_talk_detect_talking_threshold ?? 128}
                                        onChange={(e) => updateBargeInConfig('pipeline_talk_detect_talking_threshold', parseInt(e.target.value))}
                                        tooltip="Asterisk TALK_DETECT(set) talking threshold (DSP energy). Higher requires louder speech to trigger."
                                    />
                                </div>
                            </div>
                        </div>
                    </details>
                </ConfigCard>
            </ConfigSection>

            <ConfigSection 
                title="Current Configuration" 
                description="Summary of your barge-in settings."
            >
                <ConfigCard>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
                        <div>
                            <span className="text-muted-foreground">Status:</span>
                            <span className={`ml-2 font-medium ${bargeInConfig.enabled ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}`}>
                                {bargeInConfig.enabled ? 'Enabled' : 'Disabled'}
                            </span>
                        </div>
                        <div>
                            <span className="text-muted-foreground">Energy Threshold:</span>
                            <span className="ml-2 font-medium">{bargeInConfig.energy_threshold ?? 1000} RMS</span>
                        </div>
                        <div>
                            <span className="text-muted-foreground">Minimum Duration:</span>
                            <span className="ml-2 font-medium">{bargeInConfig.min_ms ?? 250}ms</span>
                        </div>
                        <div>
                            <span className="text-muted-foreground">Post-TTS Protection:</span>
                            <span className="ml-2 font-medium">{bargeInConfig.post_tts_end_protection_ms ?? 250}ms</span>
                        </div>
                    </div>
                </ConfigCard>
            </ConfigSection>
        </div>
    );
};

export default BargeInPage;
