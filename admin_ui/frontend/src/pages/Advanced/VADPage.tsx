import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import yaml from 'js-yaml';
import { Save, Activity, AlertCircle, RefreshCw, Loader2 } from 'lucide-react';
import { YamlErrorBanner, YamlErrorInfo } from '../../components/ui/YamlErrorBanner';
import { ConfigSection } from '../../components/ui/ConfigSection';
import { ConfigCard } from '../../components/ui/ConfigCard';
import { FormInput, FormSwitch } from '../../components/ui/FormComponents';
import { sanitizeConfigForSave } from '../../utils/configSanitizers';

const VAD_UTTERANCE_EXPERT_STORAGE_KEY = 'aava.ui.vad.utteranceExpert';

const VADPage = () => {
    const { confirm } = useConfirmDialog();
    const [config, setConfig] = useState<any>({});
    const [loading, setLoading] = useState(true);
    const [yamlError, setYamlError] = useState<YamlErrorInfo | null>(null);
    const [saving, setSaving] = useState(false);
    const [pendingRestart, setPendingRestart] = useState(false);
    const [restartingEngine, setRestartingEngine] = useState(false);
    const [showUtteranceExpert, setShowUtteranceExpert] = useState<boolean>(() => {
        try {
            const v = localStorage.getItem(VAD_UTTERANCE_EXPERT_STORAGE_KEY);
            if (v === 'true') return true;
            if (v === 'false') return false;
        } catch {
            // Ignore storage failures.
        }
        return false;
    });

    useEffect(() => {
        try {
            localStorage.setItem(VAD_UTTERANCE_EXPERT_STORAGE_KEY, showUtteranceExpert ? 'true' : 'false');
        } catch {
            // Ignore.
        }
    }, [showUtteranceExpert]);

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
                const parsed = yaml.load(res.data.content) as any;
                setConfig(parsed || {});
                setYamlError(null);
            }
        } catch (err) {
            console.error('Failed to load config', err);
            setYamlError(null);
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
            toast.success('VAD configuration saved');
        } catch (err) {
            console.error('Failed to save config', err);
            toast.error('Failed to save configuration');
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
                const confirmForce = await confirm({
                    title: 'Force Restart?',
                    description: `${response.data.message}\n\nDo you want to force restart anyway? This may disconnect active calls.`,
                    confirmText: 'Force Restart',
                    variant: 'destructive'
                });
                if (confirmForce) {
                    setRestartingEngine(false);
                    return handleReloadAIEngine(true);
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

    const updateVADConfig = (field: string, value: any) => {
        setConfig({
            ...config,
            vad: {
                ...config.vad,
                [field]: value
            }
        });
    };

    useEffect(() => {
        const vad = config?.vad || {};
        const hasExpertOverrides = [
            'min_utterance_duration_ms',
            'max_utterance_duration_ms',
            'utterance_padding_ms',
            'fallback_buffer_size',
        ].some((field) => vad[field] !== undefined);
        if (hasExpertOverrides) {
            setShowUtteranceExpert(true);
        }
    }, [config?.vad]);

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading configuration...</div>;

    if (yamlError) return (
        <div className="space-y-6">
            <YamlErrorBanner error={yamlError} />
        </div>
    );

    const vadConfig = config.vad || {};

    return (
        <div className="space-y-6">
            <div className={`${pendingRestart ? 'bg-orange-500/15 border-orange-500/30' : 'bg-yellow-500/10 border-yellow-500/20'} border text-yellow-600 dark:text-yellow-500 p-4 rounded-md flex items-center justify-between`}>
                <div className="flex items-center">
                    <AlertCircle className="w-5 h-5 mr-2" />
                    Changes to VAD configurations require an AI Engine restart to take effect.
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
                    <h1 className="text-3xl font-bold tracking-tight">Voice Activity Detection</h1>
                    <p className="text-muted-foreground mt-1">
                        Configure how the system detects speech and silence.
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

            <ConfigSection title="Primary Detection" description="Main VAD settings for speech detection.">
                <ConfigCard>
                    <div className="space-y-6">
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <FormSwitch
                                label="Enhanced VAD"
                                description="Use advanced algorithms for better accuracy."
                                tooltip="Enables engine-side VAD (energy + optional WebRTC VAD) used for local heuristics like barge-in fallback and silence robustness. Does not change provider-owned turn-taking."
                                checked={vadConfig.enhanced_enabled ?? false}
                                onChange={(e) => updateVADConfig('enhanced_enabled', e.target.checked)}
                            />
                            <FormSwitch
                                label="Use Provider VAD"
                                description="Prefer provider-managed turn detection when supported; engine VAD is used only for local fallback heuristics."
                                tooltip="When enabled, the engine avoids making primary turn/endpointing decisions and relies on provider-side detection where available. Engine VAD may still be used for safe local fallbacks."
                                checked={vadConfig.use_provider_vad ?? false}
                                onChange={(e) => updateVADConfig('use_provider_vad', e.target.checked)}
                            />
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <FormInput
                                label="Energy Threshold (RMS)"
                                type="number"
                                value={vadConfig.energy_threshold ?? 1500}
                                onChange={(e) => updateVADConfig('energy_threshold', parseInt(e.target.value))}
                                tooltip="Engine VAD energy threshold (RMS over PCM16). Higher = less sensitive (fewer false triggers), lower = more sensitive (better for quiet callers). Only applies when Enhanced VAD is enabled."
                                disabled={!vadConfig.enhanced_enabled}
                            />
                            <FormInput
                                label="Confidence Threshold"
                                type="number"
                                step="0.05"
                                min="0"
                                max="1"
                                value={vadConfig.confidence_threshold ?? 0.6}
                                onChange={(e) => updateVADConfig('confidence_threshold', parseFloat(e.target.value))}
                                tooltip="Confidence required for engine VAD decisions (0.0–1.0). Used by engine heuristics; providers may implement their own confidence/endpointing. Only applies when Enhanced VAD is enabled."
                                disabled={!vadConfig.enhanced_enabled}
                            />
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <FormSwitch
                                label="Adaptive Threshold"
                                description="Adapt energy threshold based on observed noise floor."
                                tooltip="When enabled, engine VAD raises the effective energy threshold in noisy environments so background noise doesn’t trigger speech."
                                checked={vadConfig.adaptive_threshold_enabled ?? true}
                                onChange={(e) => updateVADConfig('adaptive_threshold_enabled', e.target.checked)}
                            />
                            <FormInput
                                label="Noise Adaptation Rate"
                                type="number"
                                step="0.05"
                                min="0"
                                max="1"
                                value={vadConfig.noise_adaptation_rate ?? 0.1}
                                onChange={(e) => updateVADConfig('noise_adaptation_rate', parseFloat(e.target.value))}
                                tooltip="How quickly the adaptive threshold reacts to background noise (0.0–1.0). Higher reacts faster but can over-adjust on short noise bursts."
                            />
                        </div>
                    </div>
                </ConfigCard>
            </ConfigSection>

            <ConfigSection title="Engine VAD (WebRTC)" description="Engine-side fallback heuristics (barge-in + safety), using WebRTC VAD when available.">
                <ConfigCard>
                    <div className="space-y-6">
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <FormSwitch
                                label="Enable Engine Fallback"
                                description="Allow periodic forwarding / heuristics during extended silence (engine-side)."
                                tooltip="If the engine believes the caller is silent for too long, it periodically lets audio through to avoid getting “stuck” on mis-detected silence. Does not affect providers that continuously stream audio."
                                checked={vadConfig.fallback_enabled ?? true}
                                onChange={(e) => updateVADConfig('fallback_enabled', e.target.checked)}
                            />
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <FormInput
                                label="Fallback Interval (ms)"
                                type="number"
                                value={vadConfig.fallback_interval_ms ?? 1500}
                                onChange={(e) => updateVADConfig('fallback_interval_ms', parseInt(e.target.value))}
                                tooltip="After this much detected silence, engine may periodically allow audio through for robustness (default: 1500ms). Increase to reduce background noise leakage; decrease if calls feel “stuck”."
                            />
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                            <FormInput
                                label="Aggressiveness (0-3)"
                                type="number"
                                min="0"
                                max="3"
                                value={vadConfig.webrtc_aggressiveness ?? 1}
                                onChange={(e) => updateVADConfig('webrtc_aggressiveness', parseInt(e.target.value))}
                                tooltip="WebRTC VAD aggressiveness (0–3). Higher = more aggressive silence detection (fewer false speech triggers) but may miss quiet speech."
                            />
                            <FormInput
                                label="Start Frames"
                                type="number"
                                value={vadConfig.webrtc_start_frames ?? 2}
                                onChange={(e) => updateVADConfig('webrtc_start_frames', parseInt(e.target.value))}
                                tooltip="Number of consecutive “speech” frames needed to declare speech started. Higher reduces false starts but increases detection latency."
                            />
                            <FormInput
                                label="End Silence Frames"
                                type="number"
                                value={vadConfig.webrtc_end_silence_frames ?? 15}
                                onChange={(e) => updateVADConfig('webrtc_end_silence_frames', parseInt(e.target.value))}
                                tooltip="Number of consecutive “silence” frames needed to declare speech ended. Higher avoids cutting off trailing words but increases tail latency."
                            />
                        </div>
                    </div>
                </ConfigCard>
            </ConfigSection>

            <ConfigSection
                title="Utterance Controls"
                description="Fine-grained utterance boundary tuning for engine-side heuristics."
            >
                <ConfigCard>
                    <div className="space-y-6">
                        <div className="border border-amber-300/40 rounded-lg p-3 bg-amber-500/5">
                            <FormSwitch
                                label="Utterance Expert Settings"
                                description="Enable editing of low-level utterance timing and buffer controls."
                                checked={showUtteranceExpert}
                                onChange={(e) => setShowUtteranceExpert(e.target.checked)}
                                className="mb-0 border-0 p-0 bg-transparent"
                            />
                            <p className={`text-xs mt-2 ${showUtteranceExpert ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'}`}>
                                {showUtteranceExpert
                                    ? 'Warning: these controls can clip speech, delay turn-taking, or over-buffer audio if tuned aggressively.'
                                    : 'Expert values are visible and read-only until enabled.'}
                            </p>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <FormInput
                                label="Min Utterance Duration (ms)"
                                type="number"
                                value={vadConfig.min_utterance_duration_ms ?? 800}
                                onChange={(e) => updateVADConfig('min_utterance_duration_ms', parseInt(e.target.value))}
                                tooltip="Minimum duration to consider detected speech a valid utterance."
                                disabled={!showUtteranceExpert}
                            />
                            <FormInput
                                label="Max Utterance Duration (ms)"
                                type="number"
                                value={vadConfig.max_utterance_duration_ms ?? 8000}
                                onChange={(e) => updateVADConfig('max_utterance_duration_ms', parseInt(e.target.value))}
                                tooltip="Hard cap for a single utterance before forced boundary handling."
                                disabled={!showUtteranceExpert}
                            />
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <FormInput
                                label="Utterance Padding (ms)"
                                type="number"
                                value={vadConfig.utterance_padding_ms ?? 100}
                                onChange={(e) => updateVADConfig('utterance_padding_ms', parseInt(e.target.value))}
                                tooltip="Extra audio kept around utterance boundaries for naturalness."
                                disabled={!showUtteranceExpert}
                            />
                            <FormInput
                                label="Fallback Buffer Size (bytes)"
                                type="number"
                                value={vadConfig.fallback_buffer_size ?? 128000}
                                onChange={(e) => updateVADConfig('fallback_buffer_size', parseInt(e.target.value))}
                                tooltip="Internal fallback buffer size used by engine-side VAD fallback paths."
                                disabled={!showUtteranceExpert}
                            />
                        </div>
                    </div>
                </ConfigCard>
            </ConfigSection>

            <ConfigSection
                title="Upstream Squelch"
                description="Optional upstream noise squelch for continuous-audio providers with server-side VAD."
            >
                <ConfigCard>
                    <div className="space-y-6">
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <FormSwitch
                                label="Enable Upstream Squelch"
                                description="Replace non-speech frames with silence for server-VAD providers."
                                tooltip="When enabled, the AI Engine analyzes inbound PCM16 energy and replaces low-energy/noise frames with silence before forwarding audio to providers that require continuous audio and have native VAD. This can improve end-of-turn detection in noisy environments, but overly aggressive settings can clip quiet speech."
                                checked={vadConfig.upstream_squelch_enabled ?? true}
                                onChange={(e) => updateVADConfig('upstream_squelch_enabled', e.target.checked)}
                            />
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <FormInput
                                label="Base RMS Threshold"
                                type="number"
                                min="0"
                                value={vadConfig.upstream_squelch_base_rms ?? 200}
                                onChange={(e) => updateVADConfig('upstream_squelch_base_rms', parseInt(e.target.value))}
                                tooltip="Minimum RMS threshold in PCM16 space. Higher blocks more low-energy audio (less noise leakage) but may suppress quiet callers."
                            />
                            <FormInput
                                label="Noise Factor"
                                type="number"
                                step="0.1"
                                min="0"
                                value={vadConfig.upstream_squelch_noise_factor ?? 2.5}
                                onChange={(e) => updateVADConfig('upstream_squelch_noise_factor', parseFloat(e.target.value))}
                                tooltip="Dynamic threshold multiplier: threshold = max(base_rms, noise_floor_rms × noise_factor). Increase to be more conservative about what counts as speech."
                            />
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <FormInput
                                label="Noise EMA Alpha"
                                type="number"
                                step="0.01"
                                min="0"
                                max="1"
                                value={vadConfig.upstream_squelch_noise_ema_alpha ?? 0.06}
                                onChange={(e) => updateVADConfig('upstream_squelch_noise_ema_alpha', parseFloat(e.target.value))}
                                tooltip="Noise floor smoothing factor (0–1). Higher adapts faster to changing background noise but can overreact to short bursts."
                            />
                            <FormInput
                                label="Min Speech Frames"
                                type="number"
                                min="1"
                                value={vadConfig.upstream_squelch_min_speech_frames ?? 2}
                                onChange={(e) => updateVADConfig('upstream_squelch_min_speech_frames', parseInt(e.target.value))}
                                tooltip="Hysteresis: number of consecutive speech frames required before the engine considers the caller “speaking”. Higher reduces false positives but can increase detection latency."
                            />
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <FormInput
                                label="End Silence Frames"
                                type="number"
                                min="1"
                                value={vadConfig.upstream_squelch_end_silence_frames ?? 15}
                                onChange={(e) => updateVADConfig('upstream_squelch_end_silence_frames', parseInt(e.target.value))}
                                tooltip="Hysteresis: number of consecutive silence frames required to exit speaking state. Higher avoids toggling during noise, but may keep speech ‘open’ longer."
                            />
                        </div>
                    </div>
                </ConfigCard>
            </ConfigSection>
        </div>
    );
};

export default VADPage;
