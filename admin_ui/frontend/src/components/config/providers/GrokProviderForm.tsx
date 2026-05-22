import React from 'react';

interface GrokProviderFormProps {
    config: any;
    onChange: (newConfig: any) => void;
}

const GROK_VOICES = [
    { value: 'eve', label: 'eve — energetic, upbeat' },
    { value: 'ara', label: 'ara — warm, friendly' },
    { value: 'rex', label: 'rex — confident, clear' },
    { value: 'sal', label: 'sal — smooth, balanced' },
    { value: 'leo', label: 'leo — authoritative, strong' },
];

const GROK_MODELS = [
    { value: 'grok-voice-latest', label: 'grok-voice-latest (recommended)' },
    { value: 'grok-voice-think-fast-1.0', label: 'grok-voice-think-fast-1.0 (flagship)' },
];

const GrokProviderForm: React.FC<GrokProviderFormProps> = ({ config, onChange }) => {
    const handleChange = (field: string, value: any) => {
        onChange({ ...config, [field]: value });
    };

    const handleNestedChange = (parent: string, field: string, value: any) => {
        onChange({
            ...config,
            [parent]: {
                ...config[parent],
                [field]: value,
            },
        });
    };

    const isNamedVoice = GROK_VOICES.some((v) => v.value === config.voice);
    const voiceMode = isNamedVoice || !config.voice ? 'named' : 'custom';

    return (
        <div className="space-y-6">
            <div>
                <h4 className="font-semibold mb-3">Connection</h4>
                <div className="space-y-2">
                    <label className="text-sm font-medium">
                        Realtime Base URL
                        <span className="text-xs text-muted-foreground ml-2">(base_url)</span>
                    </label>
                    <input
                        type="text"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.base_url || 'wss://api.x.ai/v1/realtime'}
                        onChange={(e) => handleChange('base_url', e.target.value)}
                        placeholder="wss://api.x.ai/v1/realtime"
                    />
                    <p className="text-xs text-muted-foreground">
                        xAI Grok Voice Agent WebSocket endpoint. Override only for proxy / regional routes.
                    </p>
                </div>
            </div>

            <div>
                <h4 className="font-semibold mb-3">Identity</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Display Name</label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.display_name || ''}
                            onChange={(e) => handleChange('display_name', e.target.value || null)}
                            placeholder="e.g. Acme Grok"
                        />
                        <p className="text-xs text-muted-foreground">
                            Operator-facing label. Shown in topology and call history.
                        </p>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Customer</label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.customer || ''}
                            onChange={(e) => handleChange('customer', e.target.value || null)}
                            placeholder="e.g. Acme"
                        />
                        <p className="text-xs text-muted-foreground">
                            Optional customer tag for multi-tenant deployments.
                        </p>
                    </div>
                </div>
            </div>

            <div>
                <h4 className="font-semibold mb-3">Model & Voice</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Model</label>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.model || 'grok-voice-latest'}
                            onChange={(e) => handleChange('model', e.target.value)}
                        >
                            {GROK_MODELS.map((m) => (
                                <option key={m.value} value={m.value}>
                                    {m.label}
                                </option>
                            ))}
                        </select>
                        <p className="text-xs text-muted-foreground">
                            Sent as <code>?model=</code> query param on the WebSocket URL.
                        </p>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Voice</label>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={voiceMode}
                            onChange={(e) => {
                                if (e.target.value === 'named') {
                                    handleChange('voice', 'eve');
                                } else {
                                    handleChange('voice', '');
                                }
                            }}
                        >
                            <option value="named">Named voice (eve / ara / rex / sal / leo)</option>
                            <option value="custom">Custom voice ID (cloned voice)</option>
                        </select>
                        {voiceMode === 'named' ? (
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.voice || 'eve'}
                                onChange={(e) => handleChange('voice', e.target.value)}
                            >
                                {GROK_VOICES.map((v) => (
                                    <option key={v.value} value={v.value}>
                                        {v.label}
                                    </option>
                                ))}
                            </select>
                        ) : (
                            <input
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.voice || ''}
                                onChange={(e) => handleChange('voice', e.target.value)}
                                placeholder="e.g. custom-voice-abc123"
                            />
                        )}
                    </div>
                </div>
            </div>

            <div>
                <h4 className="font-semibold mb-3">Audio Format</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Provider Input Encoding</label>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.provider_input_encoding || 'ulaw'}
                            onChange={(e) => handleChange('provider_input_encoding', e.target.value)}
                        >
                            <option value="ulaw">μ-law direct (8 kHz) — recommended for telephony</option>
                            <option value="linear16">PCM16 (fallback — adds resample step)</option>
                        </select>
                        <p className="text-xs text-muted-foreground">
                            μ-law passes Asterisk's native frames straight through with no resampling.
                        </p>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Provider Input Sample Rate (Hz)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.provider_input_sample_rate_hz ?? 8000}
                            onChange={(e) => handleChange('provider_input_sample_rate_hz', parseInt(e.target.value, 10) || 8000)}
                        />
                        <p className="text-xs text-muted-foreground">
                            Use <code>8000</code> for μ-law; <code>16000</code> or <code>24000</code> for PCM16.
                        </p>
                    </div>
                </div>
            </div>

            <div>
                <h4 className="font-semibold mb-3">Prompt & Greeting</h4>
                <div className="space-y-3">
                    <div>
                        <label className="text-sm font-medium">System Instructions</label>
                        <textarea
                            className="w-full p-2 rounded border border-input bg-background"
                            rows={4}
                            value={config.instructions || ''}
                            onChange={(e) => handleChange('instructions', e.target.value || null)}
                            placeholder="e.g. You are a helpful customer support assistant."
                        />
                        <p className="text-xs text-muted-foreground">
                            Leave blank to fall back to the global LLM prompt.
                        </p>
                    </div>
                    <div>
                        <label className="text-sm font-medium">Initial Greeting</label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.greeting || ''}
                            onChange={(e) => handleChange('greeting', e.target.value || null)}
                            placeholder="e.g. Hello, how can I help you today?"
                        />
                    </div>
                </div>
            </div>

            <div>
                <h4 className="font-semibold mb-3">Turn Detection (server VAD)</h4>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Threshold</label>
                        <input
                            type="number"
                            step="0.1"
                            min="0.1"
                            max="0.9"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.turn_detection?.threshold ?? 0.5}
                            onChange={(e) => handleNestedChange('turn_detection', 'threshold', parseFloat(e.target.value) || 0.5)}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Silence (ms)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.turn_detection?.silence_duration_ms ?? 200}
                            onChange={(e) => handleNestedChange('turn_detection', 'silence_duration_ms', parseInt(e.target.value, 10) || 200)}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Prefix Padding (ms)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.turn_detection?.prefix_padding_ms ?? 200}
                            onChange={(e) => handleNestedChange('turn_detection', 'prefix_padding_ms', parseInt(e.target.value, 10) || 200)}
                        />
                    </div>
                </div>
            </div>

            <div>
                <h4 className="font-semibold mb-3">Session Cap Warning</h4>
                <div className="space-y-2">
                    <label className="text-sm font-medium">Warn after (seconds)</label>
                    <input
                        type="number"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.session_warn_after_seconds ?? 1680}
                        onChange={(e) => handleChange('session_warn_after_seconds', parseInt(e.target.value, 10) || 0)}
                    />
                    <p className="text-xs text-muted-foreground">
                        xAI documents a 30-minute hard session cap. We log a structured warning at this elapsed
                        threshold (default 1680 sec = 28 min). Set to 0 to disable.
                    </p>
                </div>
            </div>
        </div>
    );
};

export default GrokProviderForm;
