import React from 'react';
import ProviderCredentialsCard, { applyCredentialPatch } from './ProviderCredentialsCard';
import HelpTooltip from '../../ui/HelpTooltip';

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
    // Treat undefined/null as named (initial state), but empty string as
    // custom — picking "Custom voice ID" sets voice to '' and we used to
    // collapse straight back to named, making custom unselectable
    // (CodeRabbit on PR #396).
    const voiceMode =
        config.voice === undefined || config.voice === null || isNamedVoice ? 'named' : 'custom';

    return (
        <div className="space-y-6">
            <div>
                <h4 className="font-semibold mb-3">Connection</h4>
                <div className="space-y-2">
                    <div className="flex items-center gap-1.5">
                        <label className="text-sm font-medium">
                            Realtime Base URL
                            <span className="text-xs text-muted-foreground ml-2">(base_url)</span>
                        </label>
                        <HelpTooltip
                            content={
                                <>
                                    <strong>Realtime Base URL</strong> — WebSocket endpoint for xAI's Grok Voice Agent.
                                    Default: <code>wss://api.x.ai/v1/realtime</code>. Override only if you route through a
                                    corporate proxy or xAI publishes a regional endpoint. The <code>?model=</code> query
                                    param is appended automatically.
                                </>
                            }
                            link="https://docs.x.ai/developers/model-capabilities/audio/voice-agent"
                            linkText="xAI Grok docs"
                        />
                    </div>
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
                    onConfigPatch={(patch) => applyCredentialPatch(patch, onChange)}
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
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Model</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Model</strong> — sent as <code>?model=</code> query param on the WebSocket URL.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>grok-voice-latest</code> — recommended default, tracks xAI's current stable voice model</li>
                                            <li><code>grok-voice-think-fast-1.0</code> — flagship reasoning model, higher quality for complex tasks</li>
                                        </ul>
                                        Pricing is currently $3/hr flat (~5¢/min) across models.
                                    </>
                                }
                                link="https://docs.x.ai/developers/model-capabilities/audio/voice-agent"
                                linkText="xAI Grok docs"
                            />
                        </div>
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
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Default Voice</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Default Voice</strong> — which speaker xAI uses for synthesized audio. Agents can override this per agent (Agents page); this value is the fallback when an agent doesn't set its own voice.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>eve</code> — energetic, upbeat</li>
                                            <li><code>ara</code> — warm, friendly</li>
                                            <li><code>rex</code> — confident, clear</li>
                                            <li><code>sal</code> — smooth, balanced</li>
                                            <li><code>leo</code> — authoritative, strong</li>
                                        </ul>
                                        Switch to <em>Custom voice ID</em> to use a voice cloned in the xAI dashboard
                                        (paste the voice ID it returns).
                                    </>
                                }
                                link="https://docs.x.ai/developers/model-capabilities/audio/voice-agent"
                                linkText="xAI voice options"
                            />
                        </div>
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
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">AudioSocket Source Encoding</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>AudioSocket Source Encoding</strong> — what Asterisk's AudioSocket
                                        channel sends us on the inbound leg.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>ulaw</code> — G.711 μ-law, default for Asterisk telephony (PSTN/SIP)</li>
                                            <li><code>slin16</code> — PCM16 at 16 kHz, used for wideband HD voice</li>
                                            <li><code>slin</code> — PCM16 at 8 kHz</li>
                                        </ul>
                                        Match whatever the dialplan's <code>AudioSocket()</code> app emits.
                                    </>
                                }
                            />
                        </div>
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
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">AudioSocket Source Sample Rate (Hz)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>AudioSocket Source Sample Rate</strong> — sample rate of the bytes
                                        AudioSocket sends us.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>8000</code> — for <code>ulaw</code> and <code>slin</code> (narrowband telephony)</li>
                                            <li><code>16000</code> — for <code>slin16</code> (wideband HD voice)</li>
                                        </ul>
                                        Must match the codec in Asterisk's dialplan.
                                    </>
                                }
                            />
                        </div>
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
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Provider Input Encoding</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Provider Input Encoding</strong> — format we declare to xAI in
                                        <code>session.update.audio.input.format</code>.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>ulaw</code> — μ-law direct at 8 kHz, <strong>recommended</strong>. Passes Asterisk's native frames straight through with zero resampling — lower CPU, lower latency, no quality loss from conversion.</li>
                                            <li><code>linear16</code> — PCM16 fallback. We resample 8 kHz μ-law up to 16/24 kHz PCM before sending. Only useful if xAI rejects ulaw for a given model.</li>
                                        </ul>
                                    </>
                                }
                                link="https://docs.x.ai/developers/model-capabilities/audio/voice-agent"
                                linkText="xAI audio formats"
                            />
                        </div>
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
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Provider Input Sample Rate (Hz)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Provider Input Sample Rate</strong> — sample rate declared to xAI for
                                        the audio we send.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>8000</code> — for <code>ulaw</code> (telephony-native, no resample)</li>
                                            <li><code>16000</code> — typical PCM16 rate</li>
                                            <li><code>24000</code> — high-quality PCM16 rate</li>
                                        </ul>
                                        Stick to <code>8000</code> when using μ-law direct.
                                    </>
                                }
                            />
                        </div>
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
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Provider Output Encoding</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Provider Output Encoding</strong> — what xAI sends us. As of 2026-05,
                                        xAI ignores per-session output format declarations and always emits PCM16 at 24 kHz.
                                        Leave on <code>linear16</code> unless xAI's behavior changes. The engine downsamples
                                        to the AudioSocket target encoding below before forwarding to Asterisk.
                                    </>
                                }
                            />
                        </div>
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
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Provider Output Sample Rate (Hz)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Provider Output Sample Rate</strong> — xAI's actual native output rate.
                                        <code>24000</code> Hz is correct as of 2026-05; setting anything else will produce
                                        chipmunk or sub-bass audio because the engine uses this to drive the downsampler.
                                        Only change if xAI publishes a new rate.
                                    </>
                                }
                            />
                        </div>
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
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">AudioSocket Target Encoding</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>AudioSocket Target Encoding</strong> — what we send back to Asterisk
                                        after downsampling xAI's 24 kHz PCM16.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>ulaw</code> — G.711 μ-law, Asterisk default for PSTN/SIP</li>
                                            <li><code>slin</code> — PCM16 at 8 kHz</li>
                                            <li><code>slin16</code> — PCM16 at 16 kHz (only if AudioSocket is wideband)</li>
                                        </ul>
                                        Must match what AudioSocket expects on the playback leg.
                                    </>
                                }
                            />
                        </div>
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
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">AudioSocket Target Sample Rate (Hz)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>AudioSocket Target Sample Rate</strong> — sample rate of audio sent to
                                        Asterisk.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>8000</code> — narrowband telephony (matches <code>ulaw</code>/<code>slin</code>)</li>
                                            <li><code>16000</code> — only if AudioSocket is wideband (<code>slin16</code>)</li>
                                        </ul>
                                        Must match the encoding above.
                                    </>
                                }
                            />
                        </div>
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
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">System Instructions</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>System Instructions</strong> — the system prompt sent to Grok on session
                                        start. Defines the assistant's persona, scope, tone, and guardrails. Leave blank
                                        to fall back to the global LLM prompt configured elsewhere. Keep it focused — for
                                        voice agents, brevity and turn-taking instructions matter more than long
                                        policy text.
                                    </>
                                }
                            />
                        </div>
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
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Initial Greeting</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Initial Greeting</strong> — first utterance the assistant speaks when
                                        the call connects, before the caller says anything. Keep it short and end with
                                        an open question (e.g. <em>"Hello, how can I help you today?"</em>) so the
                                        server VAD has something to detect against. Leave blank to wait silently for
                                        the caller to speak first.
                                    </>
                                }
                            />
                        </div>
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
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Threshold</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>VAD Threshold</strong> — voice activity detection sensitivity (0.1 to 0.9).
                                        Default <code>0.5</code>.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>Lower (e.g. <code>0.3</code>) — picks up quiet/soft callers, but may trigger on background noise</li>
                                            <li>Higher (e.g. <code>0.7</code>) — ignores noisy environments, but may miss soft-spoken callers</li>
                                        </ul>
                                        Tune up for call-center floors, down for quiet office lines.
                                    </>
                                }
                            />
                        </div>
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
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Silence (ms)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Silence Duration</strong> — how long of a silence before xAI considers
                                        the caller's turn done and starts responding. Default <code>200</code> ms.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>Lower (100–200 ms) — snappier, but may cut off slow speakers mid-sentence</li>
                                            <li>Higher (400–800 ms) — more patient, but adds noticeable response latency</li>
                                        </ul>
                                    </>
                                }
                            />
                        </div>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.turn_detection?.silence_duration_ms ?? 200}
                            onChange={(e) => handleNestedChange('turn_detection', 'silence_duration_ms', parseInt(e.target.value, 10) || 200)}
                        />
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Prefix Padding (ms)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Prefix Padding</strong> — how much audio before the detected speech-start
                                        to include when sending the turn to xAI. Default <code>200</code> ms.
                                        Prevents clipping the leading consonant of words like "yes" or "no". Raise
                                        slightly (300–500 ms) if you hear cut-off word beginnings in transcripts.
                                    </>
                                }
                            />
                        </div>
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
                    <div className="flex items-center gap-1.5">
                        <label className="text-sm font-medium">Warn after (seconds)</label>
                        <HelpTooltip
                            content={
                                <>
                                    <strong>Session Cap Warning</strong> — xAI enforces a hard 30-minute (1800 s) cap
                                    per realtime session. The engine logs a structured warning at this elapsed
                                    threshold so you can detect long calls before xAI tears them down mid-sentence.
                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                        <li>Default <code>1680</code> sec = 28 min (2-minute buffer before the cap)</li>
                                        <li>Set to <code>0</code> to disable the warning</li>
                                    </ul>
                                    Long calls should hand off to a human or close out before 30 min.
                                </>
                            }
                            link="https://docs.x.ai/developers/model-capabilities/audio/voice-agent"
                            linkText="xAI session limits"
                        />
                    </div>
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
