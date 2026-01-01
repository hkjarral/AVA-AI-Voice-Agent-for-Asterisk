import React, { useState, useEffect } from 'react';
import axios from 'axios';
import yaml from 'js-yaml';
import { Save, Zap, AlertCircle, RefreshCw, Loader2 } from 'lucide-react';
import { ConfigSection } from '../../components/ui/ConfigSection';
import { ConfigCard } from '../../components/ui/ConfigCard';
import { FormInput, FormSelect, FormSwitch } from '../../components/ui/FormComponents';
import { sanitizeConfigForSave } from '../../utils/configSanitizers';

const StreamingPage = () => {
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
            alert('Streaming configuration saved successfully');
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

    const updateStreamingConfig = (field: string, value: any) => {
        setConfig({
            ...config,
            streaming: {
                ...config.streaming,
                [field]: value
            }
        });
    };

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading configuration...</div>;

    const streamingConfig = config.streaming || {};

    return (
        <div className="space-y-6">
            <div className={`${pendingRestart ? 'bg-orange-500/15 border-orange-500/30' : 'bg-yellow-500/10 border-yellow-500/20'} border text-yellow-600 dark:text-yellow-500 p-4 rounded-md flex items-center justify-between`}>
                <div className="flex items-center">
                    <AlertCircle className="w-5 h-5 mr-2" />
                    Changes to streaming configurations require an AI Engine restart to take effect.
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
