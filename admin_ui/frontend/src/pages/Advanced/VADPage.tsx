import React, { useState, useEffect } from 'react';
import axios from 'axios';
import yaml from 'js-yaml';
import { Save, Activity, AlertCircle, RefreshCw, Loader2 } from 'lucide-react';
import { ConfigSection } from '../../components/ui/ConfigSection';
import { ConfigCard } from '../../components/ui/ConfigCard';
import { FormInput, FormSwitch } from '../../components/ui/FormComponents';
import { sanitizeConfigForSave } from '../../utils/configSanitizers';

const VADPage = () => {
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
            alert('VAD configuration saved successfully');
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

    const updateVADConfig = (field: string, value: any) => {
        setConfig({
            ...config,
            vad: {
                ...config.vad,
                [field]: value
            }
        });
    };

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading configuration...</div>;

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
                                tooltip="Engine VAD energy threshold (RMS over PCM16). Higher = less sensitive (fewer false triggers), lower = more sensitive (better for quiet callers)."
                            />
                            <FormInput
                                label="Confidence Threshold"
                                type="number"
                                step="0.05"
                                min="0"
                                max="1"
                                value={vadConfig.confidence_threshold ?? 0.6}
                                onChange={(e) => updateVADConfig('confidence_threshold', parseFloat(e.target.value))}
                                tooltip="Confidence required for engine VAD decisions (0.0–1.0). Used by engine heuristics; providers may implement their own confidence/endpointing."
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
        </div>
    );
};

export default VADPage;
