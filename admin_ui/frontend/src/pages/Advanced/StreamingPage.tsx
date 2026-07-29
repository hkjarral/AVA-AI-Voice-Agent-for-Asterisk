import React, { useState, useEffect, useMemo } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import yaml from 'js-yaml';
import { Save, Zap, AlertCircle, RefreshCw, Loader2, Clock } from 'lucide-react';
import { YamlErrorBanner, YamlErrorInfo } from '../../components/ui/YamlErrorBanner';
import { ConfigSection } from '../../components/ui/ConfigSection';
import { ConfigCard } from '../../components/ui/ConfigCard';
import { FormInput, FormSelect, FormSwitch } from '../../components/ui/FormComponents';
import { sanitizeConfigForSave } from '../../utils/configSanitizers';
import { getCachedConfig, loadConfigYaml } from '../../utils/configCache';
import { useRestartRequired } from '../../hooks/useRestartRequired';

const StreamingPage = () => {
    const [config, setConfig] = useState<any>(() => getCachedConfig()?.config ?? {});
    const [loading, setLoading] = useState(() => getCachedConfig() == null);
    const [yamlError, setYamlError] = useState<YamlErrorInfo | null>(() => getCachedConfig()?.yamlError ?? null);
    const [saving, setSaving] = useState(false);
    const { restartRequired, refetch } = useRestartRequired();
    const [restartingEngine, setRestartingEngine] = useState(false);
    const [applyMethod, setApplyMethod] = useState<string>('restart');

    useEffect(() => {
        // Cache-first: seed from the shared cache (no flash on revisit). The write
        // interceptor invalidates the cache on every save, so a background
        // revalidate is unnecessary and could clobber in-progress form edits.
        fetchConfig();
    }, []);

    const fetchConfig = async (force = false) => {
        try {
            const r = await loadConfigYaml(force);
            setConfig(r.config);
            setYamlError(r.yamlError);
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
            const response = await axios.post('/api/config/yaml', { content: yaml.dump(sanitized) });
            const method = response.data?.recommended_apply_method || 'restart';
            setApplyMethod(method);
            await refetch();
            if (method === 'hot_reload') {
                toast.success('Streaming configuration saved. Changes can be applied via hot-reload.');
            } else {
                toast.success('Streaming configuration saved. Restart AI Engine to apply changes.');
            }
        } catch (err) {
            console.error('Failed to save config', err);
            toast.error('Failed to save configuration');
        } finally {
            setSaving(false);
        }
    };

    const handleApplyAIEngine = async (force: boolean = false) => {
        setRestartingEngine(true);
        try {
            // Prefer hot-reload (no dropped calls) when the backend says it suffices (MED-R1).
            if (applyMethod === 'hot_reload') {
                const response = await axios.post('/api/system/containers/ai_engine/reload');

                if (response.data?.restart_required) {
                    setApplyMethod('restart');
                    await refetch();
                    toast.warning('Hot reload applied partially', { description: response.data.message || 'Restart AI Engine to fully apply changes' });
                    return;
                }

                if (response.data?.status === 'success') {
                    await refetch();
                    toast.success('AI Engine hot reloaded! Changes are now active.');
                    return;
                }

                toast.info(`Hot reload response: ${response.data?.message || 'unknown status'}`);
                return;
            }

            const response = await axios.post(`/api/system/containers/ai_engine/restart?force=${force}`);

            if (response.data.status === 'warning') {
                if (!force) {
                    const confirmForce = window.confirm(
                        `${response.data.message}\n\nDo you want to force restart anyway? This may disconnect active calls.`
                    );
                    if (confirmForce) {
                        await handleApplyAIEngine(true);
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
                await refetch();
                toast.success('AI Engine restarted! Changes are now active.');
            }
        } catch (error: any) {
            const actionLabel = applyMethod === 'hot_reload' ? 'hot reload' : 'restart';
            toast.error(`Failed to ${actionLabel} AI Engine`, { description: error.response?.data?.detail || error.message });
        } finally {
            setRestartingEngine(false);
        }
    };

    const updateStreamingConfig = (field: string, value: any) => {
        setConfig({
            ...config,
            streaming: {
                ...config.streaming,
                [field]: value
            }
        });
    };

    const toFiniteNumber = (v: unknown, fallback: number): number => {
        const n = Number(v);
        return Number.isFinite(n) ? n : fallback;
    };

    const latencyEstimate = useMemo(() => {
        const sc = config.streaming || {};
        // Base latency: STT recognition + LLM inference + TTS synthesis
        let estimated = 3.0; // seconds baseline

        // LLM→TTS streaming overlap significantly reduces time-to-first-audio
        if (sc.pipeline_streaming_overlap ?? true) {
            estimated -= 1.0;
        }

        // Filler audio makes perceived latency lower but adds actual processing
        const fillerEnabled = sc.pipeline_filler_enabled ?? false;

        // Jitter buffer adds proportional delay (baseline 950ms)
        const jitterMs = toFiniteNumber(sc.jitter_buffer_ms, 950);
        estimated += (jitterMs - 950) / 1000;

        // Min start threshold affects when playback begins (baseline 120ms)
        const minStartMs = toFiniteNumber(sc.min_start_ms, 120);
        estimated += (minStartMs - 120) / 1000;

        // Low watermark affects buffering delay (baseline 80ms)
        const lowWatermarkMs = toFiniteNumber(sc.low_watermark_ms, 80);
        estimated += (lowWatermarkMs - 80) / 1000;

        const actual = Math.max(0.5, estimated);
        const perceived = fillerEnabled ? Math.max(0.3, actual - 0.7) : actual;

        return { actual, perceived, fillerEnabled };
    }, [config]);

    const getLatencyColor = (seconds: number) => {
        if (seconds < 2) return 'text-green-600 dark:text-green-400';
        if (seconds <= 3) return 'text-yellow-800 dark:text-yellow-400';
        return 'text-red-600 dark:text-red-400';
    };

    const getLatencyBg = (seconds: number) => {
        if (seconds < 2) return 'bg-green-500/10 border-green-500/20';
        if (seconds <= 3) return 'bg-yellow-500/10 border-yellow-500/20';
        return 'bg-red-500/10 border-red-500/20';
    };

    const getLatencyLabel = (seconds: number) => {
        if (seconds < 2) return 'Fast';
        if (seconds <= 3) return 'Moderate';
        return 'Slow';
    };

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading configuration...</div>;

    if (yamlError) return (
        <div className="space-y-6">
            <YamlErrorBanner error={yamlError} />
        </div>
    );

    const streamingConfig = config.streaming || {};

    const bannerMessage = applyMethod === 'hot_reload'
        ? 'Changes saved. Apply Changes to hot reload AI Engine without dropping active calls.'
        : 'Changes to streaming configurations require an AI Engine restart to take effect.';

    return (
        <div className="space-y-6">
            {restartRequired && (
                <div className="bg-orange-500/15 border-orange-500/30 border text-yellow-800 dark:text-yellow-500 p-4 rounded-md flex items-center justify-between">
                    <div className="flex items-center">
                        <AlertCircle className="w-5 h-5 mr-2" />
                        {bannerMessage}
                    </div>
                    <button
                        onClick={() => handleApplyAIEngine(false)}
                        disabled={restartingEngine}
                        className="flex items-center text-xs px-3 py-1.5 rounded transition-colors bg-orange-500 text-white hover:bg-orange-600 font-medium disabled:opacity-50"
                    >
                        {restartingEngine ? (
                            <Loader2 className="w-3 h-3 mr-1.5 animate-spin" />
                        ) : (
                            <RefreshCw className="w-3 h-3 mr-1.5" />
                        )}
                        {restartingEngine
                            ? (applyMethod === 'hot_reload' ? 'Applying...' : 'Restarting...')
                            : (applyMethod === 'hot_reload' ? 'Apply Changes' : 'Restart AI Engine')}
                    </button>
                </div>
            )}

            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Streaming Settings</h1>
                    <p className="text-muted-foreground mt-1">
                        Fine-tune real-time audio streaming performance and latency.
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

            <ConfigSection title="Playback Mode" description="Choose how AI responses are delivered to callers.">
                <ConfigCard>
                    <FormSelect
                        label="Downstream Mode"
                        value={config.downstream_mode || 'stream'}
                        onChange={(e) => setConfig({ ...config, downstream_mode: e.target.value })}
                        options={[
                            { value: 'stream', label: 'Streaming (Real-time)' },
                            { value: 'file', label: 'File-based (Debugging)' }
                        ]}
                        tooltip="Use 'stream' for production (low latency). Use 'file' for debugging playback issues."
                    />
                </ConfigCard>
            </ConfigSection>

            <ConfigSection title="Audio Stream Parameters" description="Core settings for audio packet handling.">
                <ConfigCard>
                    <div className="space-y-6">
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <FormInput
                                label="Chunk Size (ms)"
                                type="number"
                                value={streamingConfig.chunk_size_ms || 20}
                                onChange={(e) => updateStreamingConfig('chunk_size_ms', parseInt(e.target.value))}
                                tooltip="Duration of each audio packet."
                            />
                            <FormInput
                                label="Sample Rate"
                                type="number"
                                value={streamingConfig.sample_rate || 8000}
                                onChange={(e) => updateStreamingConfig('sample_rate', parseInt(e.target.value))}
                                tooltip="Audio sampling rate (Hz)."
                            />
                            <FormInput
                                label="Jitter Buffer (ms)"
                                type="number"
                                value={streamingConfig.jitter_buffer_ms || 950}
                                onChange={(e) => updateStreamingConfig('jitter_buffer_ms', parseInt(e.target.value))}
                                tooltip="Buffer to smooth out network variations."
                            />
                            <FormInput
                                label="Connection Timeout (ms)"
                                type="number"
                                value={streamingConfig.connection_timeout_ms || 120000}
                                onChange={(e) => updateStreamingConfig('connection_timeout_ms', parseInt(e.target.value))}
                                tooltip="Maximum time to wait for provider connection before failing (default: 120000ms = 2 min)."
                            />
                            <FormInput
                                label="Keepalive Interval (ms)"
                                type="number"
                                value={streamingConfig.keepalive_interval_ms || 5000}
                                onChange={(e) => updateStreamingConfig('keepalive_interval_ms', parseInt(e.target.value))}
                                tooltip="How often to send keepalive pings to prevent connection timeout (default: 5000ms)."
                            />
                            <FormInput
                                label="Provider Grace Period (ms)"
                                type="number"
                                value={streamingConfig.provider_grace_ms || 200}
                                onChange={(e) => updateStreamingConfig('provider_grace_ms', parseInt(e.target.value))}
                                tooltip="Wait time for provider response before considering it unresponsive (default: 200ms)."
                            />
                            <FormInput
                                label="Fallback Timeout (ms)"
                                type="number"
                                value={streamingConfig.fallback_timeout_ms || 8000}
                                onChange={(e) => updateStreamingConfig('fallback_timeout_ms', parseInt(e.target.value))}
                                tooltip="Time before switching to fallback provider if primary fails (default: 8000ms)."
                            />
                            <FormInput
                                label="Low Watermark (ms)"
                                type="number"
                                value={streamingConfig.low_watermark_ms || 80}
                                onChange={(e) => updateStreamingConfig('low_watermark_ms', parseInt(e.target.value))}
                                tooltip="Minimum audio buffered before playback starts - lower = faster but may be choppy (default: 80ms)."
                            />
                            <FormInput
                                label="Min Start (ms)"
                                type="number"
                                value={streamingConfig.min_start_ms || 120}
                                onChange={(e) => updateStreamingConfig('min_start_ms', parseInt(e.target.value))}
                                tooltip="Minimum audio required before starting response playback (default: 120ms)."
                            />
                            <FormInput
                                label="Greeting Min Start (ms)"
                                type="number"
                                value={streamingConfig.greeting_min_start_ms || 40}
                                onChange={(e) => updateStreamingConfig('greeting_min_start_ms', parseInt(e.target.value))}
                                tooltip="Reduced min start for greetings - faster initial response (default: 40ms)."
                            />
                            <FormInput
                                label="Greeting RTP Wait (ms)"
                                type="number"
                                value={streamingConfig.greeting_rtp_wait_ms || 250}
                                onChange={(e) => updateStreamingConfig('greeting_rtp_wait_ms', parseInt(e.target.value))}
                                tooltip="ExternalMedia only: How long to wait for RTP endpoint before falling back to file playback for greeting (default: 250ms). Increase if greetings are cut off."
                            />
                            <FormInput
                                label="Empty Backoff Ticks Max"
                                type="number"
                                value={streamingConfig.empty_backoff_ticks_max || 5}
                                onChange={(e) => updateStreamingConfig('empty_backoff_ticks_max', parseInt(e.target.value))}
                                tooltip="Max retries when buffer is empty before pausing playback (default: 5)."
                            />
                        </div>

                        <FormSwitch
                            label="Continuous Stream"
                            description="Keep the stream open even during silence."
                            checked={streamingConfig.continuous_stream ?? true}
                            onChange={(e) => updateStreamingConfig('continuous_stream', e.target.checked)}
                        />
                    </div>
                </ConfigCard>
            </ConfigSection>

            <ConfigSection title="Audio Normalizer" description="Normalize audio levels for consistent volume.">
                <ConfigCard>
                    <div className="space-y-6">
                        <FormSwitch
                            label="Enable Normalizer"
                            description="Automatically adjust audio gain."
                            checked={streamingConfig.normalizer?.enabled ?? true}
                            onChange={(e) => updateStreamingConfig('normalizer', { ...streamingConfig.normalizer, enabled: e.target.checked })}
                        />
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <FormInput
                                label="Max Gain (dB)"
                                type="number"
                                value={streamingConfig.normalizer?.max_gain_db || 18}
                                onChange={(e) => updateStreamingConfig('normalizer', { ...streamingConfig.normalizer, max_gain_db: parseInt(e.target.value) })}
                                disabled={!streamingConfig.normalizer?.enabled}
                                tooltip="Maximum volume boost applied to quiet audio (default: 18dB)."
                            />
                            <FormInput
                                label="Target RMS"
                                type="number"
                                value={streamingConfig.normalizer?.target_rms || 1400}
                                onChange={(e) => updateStreamingConfig('normalizer', { ...streamingConfig.normalizer, target_rms: parseInt(e.target.value) })}
                                disabled={!streamingConfig.normalizer?.enabled}
                                tooltip="Target audio level for normalization - higher = louder output (default: 1400)."
                            />
                        </div>
                    </div>
                </ConfigCard>
            </ConfigSection>

            <ConfigSection title="Egress Format" description="Control audio byte ordering and format for downstream playback.">
                <ConfigCard>
                    <div className="space-y-6">
                        <FormSelect
                            label="Egress Swap Mode"
                            value={streamingConfig.egress_swap_mode || 'auto'}
                            onChange={(e) => updateStreamingConfig('egress_swap_mode', e.target.value)}
                            options={[
                                { value: 'auto', label: 'Auto (detect from system)' },
                                { value: 'swap', label: 'Swap (force byte swap)' },
                                { value: 'none', label: 'None (no byte swap)' }
                            ]}
                            tooltip="Controls PCM16 byte ordering for downstream playback. 'auto' detects system endianness. Use 'swap' if audio sounds garbled/static, 'none' if already correct."
                        />
                        <FormSwitch
                            label="Force μ-law Encoding"
                            description="Always encode egress audio as μ-law regardless of profile."
                            checked={streamingConfig.egress_force_mulaw ?? false}
                            onChange={(e) => updateStreamingConfig('egress_force_mulaw', e.target.checked)}
                            tooltip="Force μ-law (G.711) encoding for all downstream audio. Enable if Asterisk expects μ-law but provider sends PCM16. Typically needed for telephony compatibility."
                        />
                    </div>
                </ConfigCard>
            </ConfigSection>

            <ConfigSection title="Estimated Latency" description="Live estimate of first-byte latency based on current settings. Updates as you change options below.">
                <ConfigCard className={`border ${getLatencyBg(latencyEstimate.actual)}`}>
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                            <Clock className={`w-6 h-6 ${getLatencyColor(latencyEstimate.actual)}`} />
                            <div>
                                <div className="flex items-baseline gap-2">
                                    <span className={`text-3xl font-bold ${getLatencyColor(latencyEstimate.actual)}`}>
                                        ~{latencyEstimate.actual.toFixed(1)}s
                                    </span>
                                    <span className={`text-sm font-medium ${getLatencyColor(latencyEstimate.actual)}`}>
                                        {getLatencyLabel(latencyEstimate.actual)}
                                    </span>
                                </div>
                                <p className="text-xs text-muted-foreground mt-0.5">
                                    Estimated first-byte latency (actual)
                                </p>
                            </div>
                        </div>
                        {latencyEstimate.fillerEnabled && (
                            <div className="text-right">
                                <div className={`text-2xl font-bold ${getLatencyColor(latencyEstimate.perceived)}`}>
                                    ~{latencyEstimate.perceived.toFixed(1)}s
                                </div>
                                <p className="text-xs text-muted-foreground">perceived (with filler)</p>
                            </div>
                        )}
                    </div>
                    <div className="mt-4 grid grid-cols-3 gap-2 text-xs text-muted-foreground border-t border-border/50 pt-3">
                        <div className="flex items-center gap-1">
                            <span className="w-2 h-2 rounded-full bg-green-500 inline-block" />
                            &lt;2s Fast
                        </div>
                        <div className="flex items-center gap-1">
                            <span className="w-2 h-2 rounded-full bg-yellow-500 inline-block" />
                            2–3s Moderate
                        </div>
                        <div className="flex items-center gap-1">
                            <span className="w-2 h-2 rounded-full bg-red-500 inline-block" />
                            &gt;3s Slow
                        </div>
                    </div>
                </ConfigCard>
            </ConfigSection>

            <ConfigSection title="Latency Optimization" description="Reduce perceived response time with streaming overlap and filler audio.">
                <ConfigCard>
                    <div className="space-y-6">
                        <FormSwitch
                            label="LLM → TTS Streaming Overlap"
                            description="Stream LLM tokens and synthesize TTS per-sentence instead of waiting for full response. Significantly reduces time-to-first-audio."
                            checked={streamingConfig.pipeline_streaming_overlap ?? true}
                            onChange={(e) => updateStreamingConfig('pipeline_streaming_overlap', e.target.checked)}
                            tooltip="Only applies to pipeline LLM adapters that support token streaming (e.g. OpenAI, Groq). Non-streaming adapters use the serial path automatically."
                        />
                        <FormSwitch
                            label="Enable Pipeline Filler Audio"
                            description="Play a brief acknowledgment phrase (e.g. 'One moment please.') via the pipeline TTS adapter before LLM inference starts. Works with all pipeline configurations."
                            checked={streamingConfig.pipeline_filler_enabled ?? false}
                            onChange={(e) => updateStreamingConfig('pipeline_filler_enabled', e.target.checked)}
                            tooltip="Synthesizes one random filler phrase using the pipeline's TTS adapter, plays it to the caller, then starts LLM inference. Adds ~0.5-1s of perceived responsiveness."
                        />
                        <FormInput
                            label="Filler Phrases"
                            value={(Array.isArray(streamingConfig.pipeline_filler_phrases) ? streamingConfig.pipeline_filler_phrases : ['One moment please.', 'Let me check on that.', 'Sure thing.', 'Just a moment.']).join(', ')}
                            onChange={(e) => updateStreamingConfig('pipeline_filler_phrases', e.target.value.split(',').map((s: string) => s.trim()).filter(Boolean))}
                            disabled={!streamingConfig.pipeline_filler_enabled}
                            tooltip="Comma-separated list of filler phrases to randomly choose from. One is selected at random before each LLM turn."
                        />
                    </div>
                </ConfigCard>
            </ConfigSection>

            <ConfigSection title="Diagnostics" description="Tools for debugging audio stream issues.">
                <ConfigCard>
                    <div className="space-y-6">
                        <FormSwitch
                            label="Enable Audio Taps"
                            description="Record raw audio streams to disk for analysis."
                            checked={streamingConfig.diag_enable_taps ?? false}
                            onChange={(e) => updateStreamingConfig('diag_enable_taps', e.target.checked)}
                        />
                        <FormInput
                            label="Output Directory"
                            value={streamingConfig.diag_out_dir || '/tmp/ai-engine-taps'}
                            onChange={(e) => updateStreamingConfig('diag_out_dir', e.target.value)}
                            disabled={!streamingConfig.diag_enable_taps}
                            tooltip="Directory to save diagnostic audio recordings."
                        />
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <FormInput
                                label="Diag Pre Seconds"
                                type="number"
                                value={streamingConfig.diag_pre_secs || 1}
                                onChange={(e) => updateStreamingConfig('diag_pre_secs', parseInt(e.target.value))}
                                disabled={!streamingConfig.diag_enable_taps}
                                tooltip="Seconds of audio to capture before an event (default: 1)."
                            />
                            <FormInput
                                label="Diag Post Seconds"
                                type="number"
                                value={streamingConfig.diag_post_secs || 1}
                                onChange={(e) => updateStreamingConfig('diag_post_secs', parseInt(e.target.value))}
                                disabled={!streamingConfig.diag_enable_taps}
                                tooltip="Seconds of audio to capture after an event (default: 1)."
                            />
                        </div>
                    </div>
                </ConfigCard>
            </ConfigSection>
        </div>
    );
};

export default StreamingPage;
