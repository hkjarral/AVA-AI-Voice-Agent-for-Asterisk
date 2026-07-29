import React from 'react';

interface VADConfigProps {
    config: any;
    onChange: (newConfig: any) => void;
}

const VADConfig: React.FC<VADConfigProps> = ({ config, onChange }) => {
    const handleChange = (field: string, value: any) => {
        onChange({ ...config, [field]: value });
    };
    const effectiveVadMode =
        config.vad_mode ?? (config.use_provider_vad ? 'provider' : 'auto');

    return (
        <div className="space-y-6">
            <div className="space-y-4">
                <h3 className="text-lg font-semibold">Voice Activity Detection</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="flex items-center space-x-2">
                        <input
                            type="checkbox"
                            id="enhanced_enabled"
                            className="rounded border-input"
                            checked={config.enhanced_enabled ?? true}
                            onChange={(e) => handleChange('enhanced_enabled', e.target.checked)}
                        />
                        <label htmlFor="enhanced_enabled" className="text-sm font-medium">Enhanced VAD</label>
                    </div>

                    <div className="space-y-2">
                        <label htmlFor="vad_mode" className="text-sm font-medium">VAD Mode</label>
                        <select
                            id="vad_mode"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={effectiveVadMode}
                            onChange={(e) => handleChange('vad_mode', e.target.value)}
                        >
                            <option value="auto">Auto (per-provider)</option>
                            <option value="local">Always Local VAD</option>
                            <option value="provider">Always Provider VAD</option>
                        </select>
                        <p className="text-xs text-muted-foreground">
                            {effectiveVadMode === 'local'
                                ? 'Local Enhanced + WebRTC VAD active for all providers.'
                                : effectiveVadMode === 'provider'
                                ? 'Provider-managed turn detection for all providers (legacy behavior).'
                                : 'Automatically decides per-provider: providers with native VAD + barge-in use provider VAD; others use local VAD.'}
                        </p>
                    </div>

                    <div className="space-y-2">
                        <label className="text-sm font-medium">Min Utterance Duration (ms)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.min_utterance_duration_ms || 600}
                            onChange={(e) => handleChange('min_utterance_duration_ms', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Max Utterance Duration (ms)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.max_utterance_duration_ms || 10000}
                            onChange={(e) => handleChange('max_utterance_duration_ms', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Utterance Padding (ms)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.utterance_padding_ms || 200}
                            onChange={(e) => handleChange('utterance_padding_ms', parseInt(e.target.value))}
                        />
                    </div>
                </div>
            </div>

            <div className="space-y-4">
                <h3 className="text-lg font-semibold">Fallback VAD (WebRTC)</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="flex items-center space-x-2">
                        <input
                            type="checkbox"
                            id="fallback_enabled"
                            className="rounded border-input"
                            checked={config.fallback_enabled ?? true}
                            onChange={(e) => handleChange('fallback_enabled', e.target.checked)}
                        />
                        <label htmlFor="fallback_enabled" className="text-sm font-medium">Enable Fallback</label>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Aggressiveness (0-3)</label>
                        <input
                            type="number"
                            min="0"
                            max="3"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.webrtc_aggressiveness || 1}
                            onChange={(e) => handleChange('webrtc_aggressiveness', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Start Frames</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.webrtc_start_frames || 3}
                            onChange={(e) => handleChange('webrtc_start_frames', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">End Silence Frames</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.webrtc_end_silence_frames || 50}
                            onChange={(e) => handleChange('webrtc_end_silence_frames', parseInt(e.target.value))}
                        />
                    </div>
                </div>
            </div>

            <div className="space-y-4">
                <h3 className="text-lg font-semibold">Upstream Squelch</h3>
                <p className="text-sm text-muted-foreground">
                    Optional: suppress non-speech frames for continuous-audio providers with server-side VAD.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="flex items-center space-x-2">
                        <input
                            type="checkbox"
                            id="upstream_squelch_enabled"
                            className="rounded border-input"
                            checked={config.upstream_squelch_enabled ?? true}
                            onChange={(e) => handleChange('upstream_squelch_enabled', e.target.checked)}
                        />
                        <label htmlFor="upstream_squelch_enabled" className="text-sm font-medium">Enable Upstream Squelch</label>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Base RMS Threshold</label>
                        <input
                            type="number"
                            min="0"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.upstream_squelch_base_rms ?? 200}
                            onChange={(e) => handleChange('upstream_squelch_base_rms', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Noise Factor</label>
                        <input
                            type="number"
                            step="0.1"
                            min="0"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.upstream_squelch_noise_factor ?? 2.5}
                            onChange={(e) => handleChange('upstream_squelch_noise_factor', parseFloat(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Noise EMA Alpha</label>
                        <input
                            type="number"
                            step="0.01"
                            min="0"
                            max="1"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.upstream_squelch_noise_ema_alpha ?? 0.06}
                            onChange={(e) => handleChange('upstream_squelch_noise_ema_alpha', parseFloat(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Min Speech Frames</label>
                        <input
                            type="number"
                            min="1"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.upstream_squelch_min_speech_frames ?? 2}
                            onChange={(e) => handleChange('upstream_squelch_min_speech_frames', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">End Silence Frames</label>
                        <input
                            type="number"
                            min="1"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.upstream_squelch_end_silence_frames ?? 15}
                            onChange={(e) => handleChange('upstream_squelch_end_silence_frames', parseInt(e.target.value))}
                        />
                    </div>
                </div>
            </div>
        </div>
    );
};

export default VADConfig;
