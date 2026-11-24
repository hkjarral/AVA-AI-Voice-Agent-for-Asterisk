import { modelOptions, providerDefault } from '../../../config/providerCatalog';
import ConnectionRecoveryFields from './ConnectionRecoveryFields';
import React, { useEffect, useState } from 'react';
import axios from 'axios';
import type { VoiceOption } from '../../../utils/agentVoice';
import ProviderCredentialsCard, { applyCredentialPatch } from './ProviderCredentialsCard';
import HelpTooltip from '../../ui/HelpTooltip';
import OutputResamplerField from './OutputResamplerField';

interface GrokProviderFormProps {
    config: any;
    onChange: (newConfig: any) => void;
    providerKey?: string;
    baseConfig?: {
        speed?: number;
        model?: string;
        reasoning_effort?: string;
        turn_detection?: { silence_duration_ms?: number; threshold?: number; prefix_padding_ms?: number };
    };
}

const GROK_MODELS = modelOptions('grok', 'realtime');

function GrokFieldLabel({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
    return (
        <div className="flex items-center gap-1.5">
            <label htmlFor={id} className="text-sm font-medium">{label}</label>
            <HelpTooltip ariaLabel={`Help for ${label.toLowerCase()}`} content={children} />
        </div>
    );
}

const GrokProviderForm: React.FC<GrokProviderFormProps> = ({ config, onChange, providerKey, baseConfig }) => {
    const inheritedSpeed = config.speed === undefined ? baseConfig?.speed : undefined;
    const inheritedEffort = config.reasoning_effort === undefined ? baseConfig?.reasoning_effort : undefined;
    const selectedModel = config.model ?? baseConfig?.model ?? providerDefault('grok', 'model');
    const fixedOutputRate = ['ulaw', 'alaw'].includes(config.output_encoding);
    const effectiveVad = config.turn_detection === undefined ? baseConfig?.turn_detection : config.turn_detection;
    const handleChange = (field: string, value: any) => {
        onChange({ ...config, [field]: value });
    };

    const setVad = (field: string, value: number) => {
        onChange({
            ...config,
            turn_detection: {
                ...effectiveVad,
                [field]: value,
            },
        });
    };

    const [voices, setVoices] = useState<VoiceOption[] | null>(null);
    const [editingCustom, setEditingCustom] = useState(false);
    useEffect(() => {
        const controller = new AbortController();
        axios.get('/api/config/providers/voice-catalog/grok', { signal: controller.signal })
            .then(({ data }) => {
                if (!controller.signal.aborted) setVoices(Array.isArray(data?.voices) ? data.voices : []);
            })
            .catch(() => {
                if (!controller.signal.aborted) setVoices([]);
            });
        return () => controller.abort();
    }, []);
    const voice = String(config.voice ?? 'eve');
    const builtin = voices?.find(option => option.id === voice.toLowerCase());

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
                            link="https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech"
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
                            <label className="text-sm font-medium" htmlFor="grok-model">Model</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Model</strong> — sent as <code>?model=</code> query param on the WebSocket URL.
                                        Choose the latest alias for automatic upgrades or pin Grok Voice 3.0.
                                    </>
                                }
                                link="https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech"
                                linkText="xAI Grok docs"
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            id="grok-model"
                            value={selectedModel}
                            onChange={(e) => handleChange('model', e.target.value)}
                        >
                            {!GROK_MODELS.some((m) => m.value === selectedModel) && (
                                <option value={selectedModel}>{selectedModel} (configured)</option>
                            )}
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
                            <label htmlFor="grok-default-voice" className="text-sm font-medium">Default Voice</label>
                            <HelpTooltip
                                content={
                                    <>
                                        An explicit per-call voice takes priority over the Agent voice.
                                        Grok uses this default when the Agent voice is empty or a routed call
                                        inherits a recognized voice from another provider. Choose a built-in
                                        voice or paste a cloned voice ID from your xAI workspace.
                                    </>
                                }
                                link="https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech"
                                linkText="xAI voice options"
                            />
                        </div>
                        {voices?.length ? (
                            <select
                                id="grok-default-voice"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={editingCustom ? '' : builtin?.id ?? ''}
                                onChange={(e) => {
                                    setEditingCustom(false);
                                    handleChange('voice', e.target.value);
                                }}
                            >
                                <optgroup label="Built-in voices">
                                    {voices.map(option => (
                                        <option key={option.id} value={option.id}>{option.label}</option>
                                    ))}
                                </optgroup>
                                <option value="">Custom voice ID (cloned voice)</option>
                            </select>
                        ) : null}
                        {(!voices?.length || !builtin || editingCustom) && (
                            <input
                                id={voices?.length ? 'grok-custom-voice' : 'grok-default-voice'}
                                aria-label={voices?.length ? 'Custom voice ID' : 'Default Voice'}
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={voice}
                                disabled={voices === null}
                                onFocus={() => setEditingCustom(true)}
                                onBlur={() => setEditingCustom(false)}
                                onChange={(e) => handleChange('voice', e.target.value)}
                                placeholder="Enter a voice ID"
                            />
                        )}
                        {voices?.length === 0 && (
                            <p className="text-xs text-muted-foreground">Voice catalog unavailable. Enter a built-in or cloned voice ID.</p>
                        )}
                    </div>
                    <div className="space-y-2">
                        <GrokFieldLabel id="grok-speed" label="Playback speed">
                            Set how fast Grok speaks. The range is 0.7× to 1.5×. At 0.8×, the same greeting takes about 25%
                            longer than at 1×. Reset speed uses the base Grok setting or xAI’s default of 1×.
                        </GrokFieldLabel>
                        <div className="flex items-center gap-3">
                            <input id="grok-speed" type="range" min="0.7" max="1.5" step="0.01"
                                value={config.speed ?? inheritedSpeed ?? 1}
                                onChange={(e) => handleChange('speed', e.target.valueAsNumber)} />
                            <span>{config.speed ?? inheritedSpeed ?? 1}×
                                {config.speed == null && (inheritedSpeed == null ? ' (xAI default)' : ' (inherited)')}
                            </span>
                            {config.speed !== undefined && <button type="button" className="text-sm underline"
                                onClick={() => handleChange('speed', undefined)}>Reset speed</button>}
                        </div>
                    </div>
                    <div className="space-y-2">
                        <GrokFieldLabel id="grok-reasoning" label="Reasoning effort">
                            High lets Grok reason before answering. None skips that step. Leave the default selected to use the
                            base Grok setting, or High when no base setting exists.
                        </GrokFieldLabel>
                        <select id="grok-reasoning" className="w-full p-2 rounded border border-input bg-background"
                            value={config.reasoning_effort ?? ''}
                            onChange={(e) => handleChange('reasoning_effort', e.target.value || undefined)}>
                            <option value="">{inheritedEffort ? `Inherited (${inheritedEffort})` : 'xAI default (high)'}</option>
                            <option value="high">High</option>
                            <option value="none">None</option>
                        </select>
                    </div>
                    <div className="space-y-2">
                        <GrokFieldLabel id="grok-language" label="Transcription language hint">
                            Suggest the language callers will speak. Use a language tag such as ja, es-MX or pt-BR. Spanish and
                            Portuguese need a region. With no hint set or inherited, xAI detects the language.
                        </GrokFieldLabel>
                        <input id="grok-language" className="w-full p-2 rounded border border-input bg-background"
                            placeholder="e.g. ja, es-MX, pt-BR" value={config.language_hint ?? ''}
                            onChange={(e) => handleChange('language_hint', e.target.value || undefined)} />
                    </div>
                    <div className="space-y-2">
                        <GrokFieldLabel id="grok-keyterms" label="Transcription keyterms (one per line)">
                            Help Grok recognize names and specialized terms in caller speech. Enter one term per line, up to 100
                            terms of 50 characters each. For example, add a company name callers often mention.
                        </GrokFieldLabel>
                        <textarea id="grok-keyterms" className="w-full p-2 rounded border border-input bg-background" rows={3}
                            placeholder="Up to 100 terms, 50 characters each"
                            value={Array.isArray(config.keyterms) ? config.keyterms.join('\n') : config.keyterms ?? ''}
                            onChange={(e) => handleChange('keyterms', e.target.value || undefined)} />
                    </div>
                    <div className="space-y-2">
                        <GrokFieldLabel id="grok-replacements" label="Pronunciation replacements (JSON object)">
                            Control how Grok says a phrase while keeping its original spelling in the transcript.
                            Enter a JSON object mapping each phrase to its spoken form, for example
                            <code className="ml-1">{'{"SQL": "sequel"}'}</code>.
                        </GrokFieldLabel>
                        <textarea id="grok-replacements" className="w-full p-2 rounded border border-input bg-background" rows={3}
                            placeholder='{"Acme": "Ack-mee"}'
                            value={typeof config.pronunciation_replacements === 'string'
                                ? config.pronunciation_replacements : JSON.stringify(config.pronunciation_replacements ?? undefined, null, 2) ?? ''}
                            onChange={(e) => handleChange('pronunciation_replacements', e.target.value || undefined)} />
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
                                link="https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech"
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
                            <label className="text-sm font-medium" htmlFor="grok-output-encoding">Provider Output Encoding</label>
                            <HelpTooltip
                                content={
                                    <>
                                        AVA requests the selected codec in the xAI session and converts
                                        the returned audio to the Asterisk target format.
                                    </>
                                }
                            />
                        </div>
                        <select id="grok-output-encoding"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.output_encoding || 'linear16'}
                            onChange={(e) => onChange({ ...config, output_encoding: e.target.value,
                                output_sample_rate_hz: e.target.value === 'linear16' ? 24000 : 8000 })}
                        >
                            <option value="linear16">PCM16 (linear16) — AVA default</option>
                            <option value="ulaw">μ-law (8 kHz)</option>
                            <option value="alaw">A-law (8 kHz)</option>
                        </select>
                        <p className="text-xs text-muted-foreground">
                            PCM16 at 24 kHz is the AVA telephony baseline. Verify alternate output formats on a test call.
                        </p>
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium" htmlFor="grok-output-rate">Provider Output Sample Rate (Hz)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        The provider uses this rate to interpret PCM audio before resampling.
                                        G.711 uses 8000 Hz.
                                    </>
                                }
                            />
                        </div>
                        <input id="grok-output-rate"
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.output_sample_rate_hz ?? (fixedOutputRate ? 8000 : 24000)}
                            onChange={(e) => handleChange('output_sample_rate_hz', parseInt(e.target.value, 10) || 24000)}
                        />
                        <p className="text-xs text-muted-foreground">
                            AVA defaults to <code>24000</code> Hz for PCM16.
                        </p>
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">AudioSocket Target Encoding</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>AudioSocket Target Encoding</strong> — what we send back to Asterisk
                                        after converting the provider output.
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
                            Encoding used for caller playback.
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
                    <div className="md:col-span-2">
                        <OutputResamplerField
                            value={config.output_resampler}
                            sourceRate={config.output_sample_rate_hz || 24000}
                            targetRate={config.target_sample_rate_hz || 8000}
                            onChange={(value) => handleChange('output_resampler', value)}
                        />
                    </div>
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
                                        caller knows when to respond. A blank provider greeting uses the configured fallback greeting.
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
                <p className="text-xs text-muted-foreground mb-3">
                    AVA forwards these legacy VAD settings. xAI does not document their effect for Grok.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div className="space-y-2">
                        <GrokFieldLabel id="grok-threshold" label="Threshold">
                            The speech detector’s sensitivity setting. Higher thresholds typically require a stronger signal to
                            count as speech. AVA forwards this legacy setting to xAI.
                        </GrokFieldLabel>
                        <input id="grok-threshold"
                            type="number"
                            step="0.1"
                            min="0.1"
                            max="0.9"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={effectiveVad?.threshold ?? 0.5}
                            onChange={(e) => setVad('threshold', parseFloat(e.target.value) || 0.5)}
                        />
                    </div>
                    <div className="space-y-2">
                        <GrokFieldLabel id="grok-silence" label="Silence (ms)">
                            The requested pause in milliseconds before the detector treats a caller’s turn as finished. xAI does
                            not document how Grok applies this legacy setting.
                        </GrokFieldLabel>
                        <input id="grok-silence"
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={effectiveVad?.silence_duration_ms ?? 600}
                            onChange={(e) => setVad('silence_duration_ms', e.target.value === '' ? 600 : e.target.valueAsNumber)}
                        />
                    </div>
                    <div className="space-y-2">
                        <GrokFieldLabel id="grok-prefix" label="Prefix Padding (ms)">
                            The requested amount of audio to keep from just before speech is detected, in milliseconds. It helps
                            capture the start of a word when supported.
                        </GrokFieldLabel>
                        <input id="grok-prefix"
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={effectiveVad?.prefix_padding_ms ?? 300}
                            onChange={(e) => setVad('prefix_padding_ms', e.target.value === '' ? 300 : e.target.valueAsNumber)}
                        />
                    </div>
                </div>
            </div>

            <div>
                <h4 className="font-semibold mb-3">Long-session warning</h4>
                <div className="space-y-2">
                    <div className="flex items-center gap-1.5">
                        <label className="text-sm font-medium">Warn after (seconds)</label>
                        <HelpTooltip
                            content={
                                <>
                                    AVA logs one warning after the configured number of seconds. The call continues.
                                </>
                            }
                            link="https://docs.x.ai/developers/models/speech-to-speech"
                            linkText="xAI pricing and session limits"
                        />
                    </div>
                    <input
                        type="number"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.session_warn_after_seconds ?? 1680}
                        onChange={(e) => handleChange('session_warn_after_seconds', parseInt(e.target.value, 10) || 0)}
                    />
                    <p className="text-xs text-muted-foreground">
                        AVA defaults to 1680 seconds (28 minutes). Set to 0 to disable the warning.
                    </p>
                </div>
            </div>
            <ConnectionRecoveryFields config={config} onChange={onChange} />
        </div>
    );
};

export default GrokProviderForm;
