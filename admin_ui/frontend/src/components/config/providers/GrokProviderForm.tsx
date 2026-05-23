import React from 'react';
import ProviderCredentialsCard, { applyCredentialPatch } from './ProviderCredentialsCard';

interface GrokProviderFormProps {
    config: any;
    onChange: (newConfig: any) => void;
    providerKey?: string;
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

const GrokProviderForm: React.FC<GrokProviderFormProps> = ({ config, onChange, providerKey }) => {
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
                <h4 className="font-semibold mb-3">Credentials</h4>
                <ProviderCredentialsCard
                    providerKey={providerKey}
                    credentialType="api-key"
                    label="xAI API Key"
                    placeholder="xai-..."
                    envVarFallback="XAI_API_KEY"
                    inlineValue={config.api_key}
                    onConfigPatch={(patch) => applyCredentialPatch(config, patch, onChange)}
                    helpText={
                        <>
                            Find your key in the{' '}
                            <a
                                href="https://console.x.ai/team/default/api-keys"
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-primary hover:underline"
                            >
                                xAI Console
                            </a>
                            . For multi-instance setups, each YAML provider key gets its own credential file.
                        </>
                    }
                />
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
                <h4 className="font-semibold mb-3">Audio Format — Inbound (Asterisk → xAI)</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">AudioSocket Source Encoding</label>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.input_encoding || 'ulaw'}
                            onChange={(e) => handleChange('input_encoding', e.target.value)}
                        >
                            <option value="ulaw">μ-law (G.711) — Asterisk telephony native</option>
                            <option value="slin16">slin16 (PCM16 @ 16 kHz)</option>
                            <option value="slin">slin (PCM16 @ 8 kHz)</option>
                        </select>
                        <p className="text-xs text-muted-foreground">
                            What AudioSocket sends us. <code>ulaw</code> matches the default Asterisk setup.
                        </p>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">AudioSocket Source Sample Rate (Hz)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.input_sample_rate_hz ?? 8000}
                            onChange={(e) => handleChange('input_sample_rate_hz', parseInt(e.target.value, 10) || 8000)}
                        />
                        <p className="text-xs text-muted-foreground">
                            <code>8000</code> for μ-law/slin; <code>16000</code> for slin16.
                        </p>
                    </div>
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
                            Format declared to xAI in <code>session.update.audio.input.format</code>.
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
                <h4 className="font-semibold mb-3">Audio Format — Outbound (xAI → AudioSocket)</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Provider Output Encoding</label>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.output_encoding || 'linear16'}
                            onChange={(e) => handleChange('output_encoding', e.target.value)}
                        >
                            <option value="linear16">PCM16 (linear16) — what xAI actually emits</option>
                            <option value="ulaw">μ-law (8 kHz)</option>
                            <option value="alaw">A-law (8 kHz)</option>
                        </select>
                        <p className="text-xs text-muted-foreground">
                            xAI ignores per-session output_format declarations and emits 24 kHz PCM16 regardless,
                            so leave on <code>linear16</code> unless xAI's behavior changes.
                        </p>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Provider Output Sample Rate (Hz)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.output_sample_rate_hz ?? 24000}
                            onChange={(e) => handleChange('output_sample_rate_hz', parseInt(e.target.value, 10) || 24000)}
                        />
                        <p className="text-xs text-muted-foreground">
                            xAI's actual native output rate. <code>24000</code> is correct as of 2026-05.
                        </p>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">AudioSocket Target Encoding</label>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.target_encoding || 'ulaw'}
                            onChange={(e) => handleChange('target_encoding', e.target.value)}
                        >
                            <option value="ulaw">μ-law (G.711) — Asterisk default</option>
                            <option value="slin">slin (PCM16 @ 8 kHz)</option>
                            <option value="slin16">slin16 (PCM16 @ 16 kHz)</option>
                        </select>
                        <p className="text-xs text-muted-foreground">
                            What we send to Asterisk after resampling xAI's 24 kHz PCM16 down.
                        </p>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">AudioSocket Target Sample Rate (Hz)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.target_sample_rate_hz ?? 8000}
                            onChange={(e) => handleChange('target_sample_rate_hz', parseInt(e.target.value, 10) || 8000)}
                        />
                        <p className="text-xs text-muted-foreground">
                            <code>8000</code> for telephony. Higher rates only if AudioSocket is configured wideband.
                        </p>
                    </div>
                </div>
            </div>

            <div>
                <h4 className="font-semibold mb-3">Response Modalities</h4>
                <div className="space-y-2">
                    <div className="flex flex-wrap gap-4">
                        {(['audio', 'text'] as const).map((modality) => {
                            const current: string[] = Array.isArray(config.response_modalities)
                                ? config.response_modalities
                                : ['audio', 'text'];
                            const checked = current.includes(modality);
                            return (
                                <label key={modality} className="inline-flex items-center gap-2 text-sm">
                                    <input
                                        type="checkbox"
                                        checked={checked}
                                        onChange={(e) => {
                                            const next = e.target.checked
                                                ? Array.from(new Set([...current, modality]))
                                                : current.filter((m) => m !== modality);
                                            handleChange('response_modalities', next);
                                        }}
                                    />
                                    {modality}
                                </label>
                            );
                        })}
                    </div>
                    <p className="text-xs text-muted-foreground">
                        Keep both checked for a voice agent (xAI emits transcripts alongside audio chunks).
                        Uncheck <code>audio</code> for text-only research/testing.
                    </p>
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
