import { modelOptions, modelIds, providerDefault, providerVoices } from '../../../config/providerCatalog';
import ConnectionRecoveryFields from './ConnectionRecoveryFields';
import React, { useEffect, useState } from 'react';
import ProviderCredentialsCard, { applyCredentialPatch } from './ProviderCredentialsCard';
import HelpTooltip from '../../ui/HelpTooltip';
import OutputResamplerField from './OutputResamplerField';

interface DeepgramProviderFormProps {
    config: any;
    onChange: (newConfig: any) => void;
    providerKey?: string;
}

const DEEPGRAM_AGENT_LANGUAGES = [
    { value: 'en', label: 'English (en)' },
    { value: 'es', label: 'Spanish (es)' },
    { value: 'de', label: 'German (de)' },
    { value: 'fr', label: 'French (fr)' },
    { value: 'it', label: 'Italian (it)' },
    { value: 'nl', label: 'Dutch (nl)' },
    { value: 'ja', label: 'Japanese (ja)' },
] as const;

const DEEPGRAM_RECOMMENDED_MODELS = new Set([...modelIds('deepgram', 'stt'), ...modelIds('deepgram', 'flux_stt')]);
const DEEPGRAM_ENGLISH_ONLY_MODELS = new Set<string>(['flux-general-en', 'nova-2-phonecall']);
const normalizeAgentLanguage = (value: unknown): string => String(value || 'en').trim().toLowerCase().replace('_', '-').split('-', 1)[0] || 'en';
const allDeepgramVoices = [...providerVoices('deepgram', 'tts'), ...providerVoices('deepgram', 'flux_tts')];
const auraVoiceLanguage = (value: unknown): string | null => {
    const voice = allDeepgramVoices.find(item => item.id === String(value || '').toLowerCase());
    return voice ? voice.language ?? voice.id.split('-').slice(-1)[0] ?? null : null;
};

const DeepgramProviderForm: React.FC<DeepgramProviderFormProps> = ({
    config,
    onChange,
    providerKey,
}) => {
    const handleChange = (field: string, value: any) => {
        onChange({ ...config, [field]: value });
    };
    const [showOutputAutodetectExpert, setShowOutputAutodetectExpert] = useState<boolean>(
        () => config?.allow_output_autodetect !== undefined
    );

    useEffect(() => {
        if (config?.allow_output_autodetect !== undefined) {
            setShowOutputAutodetectExpert(true);
        }
    }, [config?.allow_output_autodetect]);

    const selectedModel = String(config.model || 'nova-3');
    const configuredAgentLanguage = String(config.agent_language || 'en');
    const agentLanguage = normalizeAgentLanguage(configuredAgentLanguage);
    const selectedVersion = config.tts_version ?? providerDefault('deepgram', 'tts_version');
    const familyVoices = providerVoices('deepgram', selectedVersion === 'v2' ? 'flux_tts' : 'tts');
    const agentLanguages = selectedVersion === 'v2'
        ? [...new Set(familyVoices.map(voice => voice.language ?? voice.id.split('-').slice(-1)[0] ?? 'en'))].map(value => ({ value, label: value.toUpperCase() }))
        : DEEPGRAM_AGENT_LANGUAGES;
    const configuredVoice = String(config.tts_model || providerDefault('deepgram', 'tts_model'));
    const configuredVoiceLanguage = auraVoiceLanguage(configuredVoice);
    const legacyModel = !DEEPGRAM_RECOMMENDED_MODELS.has(selectedModel);
    const legacyLanguage = !agentLanguages.some(language => language.value === agentLanguage);
    const modelLanguageMismatch =
        DEEPGRAM_ENGLISH_ONLY_MODELS.has(selectedModel) && agentLanguage !== 'en';
    const voiceLanguageMismatch = configuredVoiceLanguage !== agentLanguage || !familyVoices.some(voice => voice.id === configuredVoice);

    return (
        <div className="space-y-6">
            <div>
                <h4 className="font-semibold mb-3">Credentials</h4>
                <ProviderCredentialsCard
                    providerKey={providerKey}
                    credentialType="api-key"
                    label="Deepgram API Key"
                    placeholder="Token..."
                    envVarFallback="DEEPGRAM_API_KEY"
                    inlineValue={config.api_key}
                    onConfigPatch={patch => applyCredentialPatch(patch, onChange)}
                    helpText={
                        <>
                            Find your key in the{' '}
                            <a
                                href="https://console.deepgram.com/"
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-primary hover:underline"
                            >
                                Deepgram Console
                            </a>
                            . Per-instance keys override the env var fallback.
                        </>
                    }
                />
            </div>

            {/* Base URL Section */}
            <div>
                <h4 className="font-semibold mb-3">API Endpoints</h4>
                <div className="grid grid-cols-1 gap-4">
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">
                                Voice Agent WebSocket URL
                                <span className="text-xs text-muted-foreground ml-2">
                                    (voice_agent_base_url)
                                </span>
                            </label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Voice Agent WebSocket</strong> — endpoint for
                                        Deepgram's all-in-one Voice Agent (Listen + Think + Speak
                                        over a single WS).
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>
                                                US:{' '}
                                                <code>
                                                    wss://agent.deepgram.com/v1/agent/converse
                                                </code>
                                            </li>
                                            <li>
                                                EU:{' '}
                                                <code>
                                                    wss://api.eu.deepgram.com/v1/agent/converse
                                                </code>
                                            </li>
                                        </ul>
                                        Only change for region routing or a proxy.
                                    </>
                                }
                                link="https://developers.deepgram.com/docs/voice-agent"
                                linkText="Voice Agent docs"
                            />
                        </div>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={
                                config.voice_agent_base_url ||
                                'wss://agent.deepgram.com/v1/agent/converse'
                            }
                            onChange={e => handleChange('voice_agent_base_url', e.target.value)}
                            placeholder="wss://agent.deepgram.com/v1/agent/converse"
                        />
                        <p className="text-xs text-muted-foreground">
                            Deepgram Voice Agent WebSocket endpoint for full agent provider. Change
                            for EU region (wss://api.eu.deepgram.com/v1/agent/converse).
                        </p>
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">
                                REST API Base URL
                                <span className="text-xs text-muted-foreground ml-2">
                                    (base_url)
                                </span>
                            </label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>REST API Base URL</strong> — used by Deepgram in
                                        pipeline mode (separate STT/TTS HTTP calls), not the Voice
                                        Agent WS.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>
                                                US: <code>https://api.deepgram.com</code>
                                            </li>
                                            <li>
                                                EU: <code>https://api.eu.deepgram.com</code>
                                            </li>
                                        </ul>
                                    </>
                                }
                                link="https://developers.deepgram.com/reference/deepgram-api-overview"
                                linkText="API reference"
                            />
                        </div>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.base_url || 'https://api.deepgram.com'}
                            onChange={e => handleChange('base_url', e.target.value)}
                            placeholder="https://api.deepgram.com"
                        />
                        <p className="text-xs text-muted-foreground">
                            Deepgram REST API endpoint for STT/TTS in pipeline mode. Change for EU
                            region (https://api.eu.deepgram.com) or proxy.
                        </p>
                    </div>
                </div>
            </div>

            {/* Models Section */}
            <div>
                <h4 className="font-semibold mb-3">Models & Voice</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">STT Model</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Listen (STT) Model</strong> — the intentionally
                                        supported Voice Agent model surface.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>
                                                <code>flux-general-en</code> — conversational
                                                English with built-in EOT detection
                                            </li>
                                            <li>
                                                <code>flux-general-multi</code> — conversational
                                                multilingual with a selected language hint
                                            </li>
                                            <li>
                                                <code>nova-3</code> — recommended general default
                                            </li>
                                            <li>
                                                <code>nova-2-phonecall</code> — legacy telephony
                                                compatibility option
                                            </li>
                                        </ul>
                                        Switching away from a <code>flux-*</code> model clears
                                        Flux-only tuning fields.
                                    </>
                                }
                                link="https://developers.deepgram.com/docs/voice-agent-stt-models"
                                linkText="Voice Agent models"
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={selectedModel}
                            onChange={e => {
                                const nextModel = e.target.value;
                                const isFluxModel = nextModel.startsWith('flux-');
                                if (isFluxModel) {
                                    handleChange('model', nextModel);
                                } else {
                                    onChange({
                                        ...config,
                                        model: nextModel,
                                        eot_threshold: null,
                                        eager_eot_threshold: null,
                                        keyterms: null,
                                    });
                                }
                            }}
                        >
                            <optgroup label="Flux — Conversational (built-in turn detection)">
                                {modelOptions('deepgram', 'flux_stt').map(model => <option key={model.value} value={model.value} disabled={DEEPGRAM_ENGLISH_ONLY_MODELS.has(model.value) && agentLanguage !== 'en'}>{model.label}</option>)}
                            </optgroup>
                            <optgroup label="Nova — Streaming Recognition">
                                {modelOptions('deepgram', 'stt').map(model => <option key={model.value} value={model.value} disabled={DEEPGRAM_ENGLISH_ONLY_MODELS.has(model.value) && agentLanguage !== 'en'}>{model.label}</option>)}
                            </optgroup>
                            {legacyModel && (
                                <option value={selectedModel}>
                                    Current legacy/custom value — {selectedModel}
                                </option>
                            )}
                        </select>
                        <p className="text-xs text-muted-foreground">
                            Flux for conversational turn-taking; Nova-3 for the general
                            compatibility path.
                            <a
                                href="https://developers.deepgram.com/docs/voice-agent-stt-models"
                                target="_blank"
                                rel="noopener noreferrer"
                                className="ml-1 text-blue-500 hover:underline"
                            >
                                Voice Agent Models ↗
                            </a>
                        </p>
                        {legacyModel && (
                            <p className="text-xs text-amber-600" role="alert">
                                This legacy/custom model is preserved unchanged, but is outside the
                                verified AAVA Voice Agent surface.
                            </p>
                        )}
                        {modelLanguageMismatch && (
                            <p className="text-xs text-red-600" role="alert">
                                {selectedModel} is English-only. Select English or use Flux
                                Multilingual/Nova-3 before placing a call.
                            </p>
                        )}
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">
                                STT Language
                                <span className="text-xs text-muted-foreground ml-2">
                                    (stt_language)
                                </span>
                            </label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>STT Language</strong> — default BCP-47 language tag
                                        passed to Deepgram for transcription in pipeline mode.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>
                                                <code>en-US</code>, <code>en-GB</code>,{' '}
                                                <code>es</code>, <code>fr</code>, <code>de</code>,{' '}
                                                <code>multi</code>, etc.
                                            </li>
                                            <li>Must be supported by the selected STT model</li>
                                        </ul>
                                        Pipelines/contexts can override this per-call.
                                    </>
                                }
                                link="https://developers.deepgram.com/docs/models-languages-overview"
                                linkText="Language support"
                            />
                        </div>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.stt_language || 'en-US'}
                            onChange={e => handleChange('stt_language', e.target.value)}
                            placeholder="en-US"
                        />
                        <p className="text-xs text-muted-foreground">
                            Default language for STT in pipeline mode (can be overridden per
                            pipeline/context).
                        </p>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">
                                Agent Language
                                <span className="text-xs text-muted-foreground ml-2">
                                    (agent_language)
                                </span>
                            </label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Agent Language</strong> — language for the full
                                        Voice Agent conversation (Listen + Speak).
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>
                                                Must match the language of your selected TTS voice
                                            </li>
                                            <li>
                                                Aura voices encode language in their model suffix
                                            </li>
                                        </ul>
                                    </>
                                }
                                link="https://developers.deepgram.com/docs/configure-voice-agent"
                                linkText="Configure Voice Agent"
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={configuredAgentLanguage}
                            onChange={e => handleChange('agent_language', e.target.value)}
                        >
                            {agentLanguages.map(({ value, label }) => (
                                <option
                                    key={value}
                                    value={value}
                                    disabled={
                                        DEEPGRAM_ENGLISH_ONLY_MODELS.has(selectedModel) &&
                                        value !== 'en'
                                    }
                                >
                                    {label}
                                </option>
                            ))}
                            {!agentLanguages.some(
                                ({ value }) => value === configuredAgentLanguage
                            ) && (
                                <option value={configuredAgentLanguage}>
                                    Current legacy value — {configuredAgentLanguage}
                                </option>
                            )}
                        </select>
                        <p className="text-xs text-muted-foreground">
                            Supported languages for the selected TTS family. Locale variants are preserved and
                            normalized to their base language at runtime.
                        </p>
                        {legacyLanguage && (
                            <p className="text-xs text-red-600" role="alert">
                                This language is preserved for review but is not supported by the
                                AAVA Deepgram Voice Agent surface.
                            </p>
                        )}
                    </div>

                    <div className="space-y-2">
                        <label htmlFor="deepgram-think-model" className="text-sm font-medium">Managed Reasoning Model</label>
                        <select id="deepgram-think-model" className="w-full p-2 rounded border border-input bg-background" value={config.think_model ?? providerDefault('deepgram', 'think_model')} onChange={event => handleChange('think_model', event.target.value)}>
                            {config.think_model && !modelIds('deepgram', 'llm').includes(config.think_model) && <option value={config.think_model}>{config.think_model} (configured)</option>}
                            {modelOptions('deepgram', 'llm').map(model => <option key={model.value} value={model.value}>{model.label}</option>)}
                        </select>
                        <p className="text-xs text-muted-foreground">Deepgram-managed OpenAI models only. New calls use this setting; test tool calls before rollout.</p>
                    </div>
                    <div className="space-y-2">
                        <label htmlFor="deepgram-tts-version" className="text-sm font-medium">TTS API Version</label>
                        <select id="deepgram-tts-version" className="w-full p-2 rounded border border-input bg-background" value={selectedVersion} onChange={event => handleChange('tts_version', event.target.value)}>
                            <option value="v1">Aura (v1)</option><option value="v2">Flux (v2, opt-in)</option>
                        </select>
                        <p className="text-xs text-muted-foreground">Select a matching voice and language after changing versions. Flux supports raw audio and requires a real-call acceptance test. Early Access languages need numbers and dates written out; non-English voices are unavailable on the Australia endpoint.</p>
                        <label htmlFor="deepgram-tts-speed" className="text-sm font-medium">Speaking Rate</label>
                        <input id="deepgram-tts-speed" type="number" min={selectedVersion === 'v2' ? 0.5 : 0.7} max={1.5} step={0.05} value={config.tts_speed ?? ''} onChange={event => handleChange('tts_speed', event.target.value === '' ? null : Number(event.target.value))} className="w-full p-2 rounded border border-input bg-background" />
                        {selectedVersion === 'v2' && <><label htmlFor="deepgram-tts-expressivity" className="text-sm font-medium">Flux Expressivity (-2 to 2)</label><input id="deepgram-tts-expressivity" type="number" min={-2} max={2} step={1} value={config.tts_expressivity ?? ''} onChange={event => handleChange('tts_expressivity', event.target.value === '' ? null : Number(event.target.value))} className="w-full p-2 rounded border border-input bg-background" /></>}
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">
                                Default Voice Model
                                <span className="text-xs text-muted-foreground ml-2">
                                    (tts_model)
                                </span>
                            </label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Speak (TTS) Voice</strong> — Deepgram Aura voice
                                        that synthesizes the agent's replies. Agents can override
                                        this per agent (Agents page); this value is the fallback
                                        when an agent doesn't set its own voice.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>
                                                <code>aura-2-*</code> — newest generation, best
                                                naturalness
                                            </li>
                                            <li>
                                                <code>aura-*</code> — legacy, still supported
                                            </li>
                                            <li>
                                                Voice locale (<code>-en</code>, <code>-es</code>,
                                                etc.) must match Agent Language
                                            </li>
                                            <li>⭐ featured, 🔄 codeswitching ES↔EN</li>
                                        </ul>
                                    </>
                                }
                                link="https://developers.deepgram.com/docs/tts-models"
                                linkText="All Aura voices"
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={configuredVoice}
                            onChange={e => handleChange('tts_model', e.target.value)}
                        >
                            {voiceLanguageMismatch && (
                                <option value={configuredVoice}>
                                    {config.tts_model
                                        ? 'Current configured value'
                                        : 'Current effective fallback'}{' '}
                                    — {configuredVoice}
                                </option>
                            )}
                            {agentLanguages.map(language => (
                                <optgroup key={language.value} label={language.label} hidden={agentLanguage !== language.value} disabled={agentLanguage !== language.value}>
                                    {familyVoices.filter(voice => (voice.language ?? voice.id.split('-').slice(-1)[0]) === language.value).map(voice => <option key={voice.id} value={voice.id}>{voice.label}</option>)}
                                </optgroup>
                            ))}
                        </select>
                        <p className="text-xs text-muted-foreground">
                            ⭐ = Featured, 🔄 = Codeswitching (ES↔EN). EN (53), ES (17), DE (7), FR
                            (2), IT (10), NL (9), JA (5).
                            <a
                                href="https://developers.deepgram.com/docs/tts-models"
                                target="_blank"
                                rel="noopener noreferrer"
                                className="ml-1 text-blue-500 hover:underline"
                            >
                                All Voices ↗
                            </a>
                        </p>
                        {voiceLanguageMismatch && (
                            <p className="text-xs text-red-600" role="alert">
                                Voice {configuredVoice} does not match agent language{' '}
                                {configuredAgentLanguage}. Select a matching voice before
                                placing a call; AAVA will not silently replace it.
                            </p>
                        )}
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Input Encoding</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Input Encoding</strong> — audio codec of frames
                                        coming FROM Asterisk into the agent.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>
                                                <code>mulaw</code> — standard US telephony (G.711
                                                μ-law)
                                            </li>
                                            <li>
                                                <code>alaw</code> — standard EU telephony (G.711
                                                A-law)
                                            </li>
                                            <li>
                                                <code>linear16</code> — uncompressed PCM, higher
                                                quality
                                            </li>
                                        </ul>
                                        Must match Asterisk's channel codec.
                                    </>
                                }
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.input_encoding || 'mulaw'}
                            onChange={e => handleChange('input_encoding', e.target.value)}
                        >
                            <option value="linear16">Linear16 (PCM)</option>
                            <option value="mulaw">μ-law</option>
                            <option value="alaw">A-law</option>
                        </select>
                        <p className="text-xs text-muted-foreground">
                            Audio format from Asterisk. Use μ-law for standard telephony.
                        </p>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Input Sample Rate (Hz)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Input Sample Rate</strong> — sample rate of audio
                                        coming FROM Asterisk.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>
                                                <code>8000</code> Hz — standard telephony (G.711)
                                            </li>
                                            <li>
                                                <code>16000</code> Hz — wideband (G.722, Opus)
                                            </li>
                                        </ul>
                                        Must match the codec on the Asterisk channel.
                                    </>
                                }
                            />
                        </div>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.input_sample_rate_hz || 8000}
                            onChange={e =>
                                handleChange('input_sample_rate_hz', parseInt(e.target.value))
                            }
                        />
                        <p className="text-xs text-muted-foreground">
                            Sample rate from Asterisk. Standard telephony uses 8000 Hz.
                        </p>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Output Encoding</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Output Encoding</strong> — codec Deepgram returns
                                        TTS audio in.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>
                                                <code>mulaw</code> @ 8 kHz — passes straight to
                                                G.711 telephony (no transcoding)
                                            </li>
                                            <li>
                                                <code>linear16</code> — uncompressed PCM, needs
                                                resample/encode
                                            </li>
                                        </ul>
                                        Match telephony for lowest latency.
                                    </>
                                }
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.output_encoding || 'mulaw'}
                            onChange={e => handleChange('output_encoding', e.target.value)}
                        >
                            <option value="mulaw">μ-law</option>
                            <option value="linear16">Linear16</option>
                            <option value="alaw">A-law</option>
                        </select>
                        <p className="text-xs text-muted-foreground">
                            Audio format from Deepgram TTS. μ-law matches telephony directly.
                        </p>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Output Sample Rate (Hz)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Output Sample Rate</strong> — sample rate of TTS
                                        audio coming FROM Deepgram.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>
                                                <code>8000</code> Hz — narrowband, telephony native
                                            </li>
                                            <li>
                                                <code>16000</code> / <code>24000</code> Hz — higher
                                                fidelity, requires downsampling for G.711
                                            </li>
                                        </ul>
                                    </>
                                }
                            />
                        </div>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.output_sample_rate_hz || 8000}
                            onChange={e =>
                                handleChange('output_sample_rate_hz', parseInt(e.target.value))
                            }
                        />
                        <p className="text-xs text-muted-foreground">
                            Sample rate from Deepgram. 8000 Hz for telephony, 16000 Hz for higher
                            quality.
                        </p>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Target Encoding</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Target Encoding</strong> — final codec written to
                                        the Asterisk channel (after any internal transcoding).
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>
                                                Should match your Asterisk dialplan codec (usually{' '}
                                                <code>mulaw</code> for US, <code>alaw</code> for EU)
                                            </li>
                                        </ul>
                                    </>
                                }
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.target_encoding || 'mulaw'}
                            onChange={e => handleChange('target_encoding', e.target.value)}
                        >
                            <option value="mulaw">μ-law</option>
                            <option value="linear16">Linear16</option>
                            <option value="alaw">A-law</option>
                        </select>
                        <p className="text-xs text-muted-foreground">
                            Final format for playback to caller. Match your Asterisk codec.
                        </p>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Target Sample Rate (Hz)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Target Sample Rate</strong> — final sample rate
                                        written to the Asterisk channel.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>
                                                <code>8000</code> Hz for G.711 (μ-law / A-law)
                                            </li>
                                            <li>
                                                <code>16000</code> Hz for wideband channels
                                            </li>
                                        </ul>
                                    </>
                                }
                            />
                        </div>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.target_sample_rate_hz || 8000}
                            onChange={e =>
                                handleChange('target_sample_rate_hz', parseInt(e.target.value))
                            }
                        />
                        <p className="text-xs text-muted-foreground">
                            Final sample rate for playback. 8000 Hz for standard telephony.
                        </p>
                    </div>
                    <div className="md:col-span-2">
                        <OutputResamplerField
                            value={config.output_resampler}
                            sourceRate={config.output_sample_rate_hz || 8000}
                            targetRate={config.target_sample_rate_hz || 8000}
                            onChange={value => handleChange('output_resampler', value)}
                        />
                    </div>
                </div>

                <div className="space-y-2">
                    <div className="flex items-center gap-1.5">
                        <label className="text-sm font-medium">System Instructions</label>
                        <HelpTooltip
                            content={
                                <>
                                    <strong>System Instructions</strong> — system prompt sent to the
                                    Voice Agent's Think (LLM) stage. Defines persona, scope, and
                                    behavior.
                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                        <li>
                                            Keep it concise — long prompts add latency on every turn
                                        </li>
                                        <li>
                                            Use the prompt to declare tools, refusal policies, and
                                            language
                                        </li>
                                    </ul>
                                </>
                            }
                            link="https://developers.deepgram.com/docs/configure-voice-agent"
                            linkText="Agent config"
                        />
                    </div>
                    <textarea
                        className="w-full p-2 rounded border border-input bg-background min-h-[100px] font-mono text-sm"
                        value={config.instructions || ''}
                        onChange={e => handleChange('instructions', e.target.value)}
                        placeholder="You are a helpful assistant..."
                    />
                </div>

                <div className="space-y-2">
                    <div className="flex items-center gap-1.5">
                        <label className="text-sm font-medium">Greeting</label>
                        <HelpTooltip
                            content={
                                <>
                                    <strong>Greeting</strong> — first utterance the agent speaks
                                    when the call connects, before any user input.
                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                        <li>
                                            Spoken via the configured TTS voice immediately on
                                            session start
                                        </li>
                                        <li>
                                            Leave empty for a silent open (agent waits for caller to
                                            speak)
                                        </li>
                                    </ul>
                                </>
                            }
                        />
                    </div>
                    <input
                        type="text"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.greeting || ''}
                        onChange={e => handleChange('greeting', e.target.value)}
                        placeholder="Hello, how can I help you?"
                    />
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="flex items-center space-x-2">
                        <input
                            type="checkbox"
                            id="enabled"
                            className="rounded border-input"
                            checked={config.enabled ?? true}
                            onChange={e => handleChange('enabled', e.target.checked)}
                        />
                        <label htmlFor="enabled" className="text-sm font-medium">
                            Enabled
                        </label>
                        <HelpTooltip
                            content={
                                <>
                                    <strong>Enabled</strong> — when off, this provider instance is
                                    skipped during pipeline selection and the engine falls back to
                                    another configured provider.
                                </>
                            }
                        />
                    </div>

                    <div className="flex items-center space-x-2">
                        <input
                            type="checkbox"
                            id="continuous_input"
                            className="rounded border-input"
                            checked={config.continuous_input ?? true}
                            onChange={e => handleChange('continuous_input', e.target.checked)}
                        />
                        <label htmlFor="continuous_input" className="text-sm font-medium">
                            Continuous Input
                        </label>
                        <HelpTooltip
                            content={
                                <>
                                    <strong>Continuous Input</strong> — keep streaming Asterisk
                                    audio to Deepgram even while the agent is speaking, enabling
                                    barge-in.
                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                        <li>On: caller can interrupt the agent mid-utterance</li>
                                        <li>Off: input is paused while TTS plays</li>
                                    </ul>
                                </>
                            }
                        />
                    </div>

                    <div className="flex items-center space-x-2">
                        <input
                            type="checkbox"
                            id="vad_turn_detection"
                            className="rounded border-input"
                            checked={config.vad_turn_detection ?? true}
                            onChange={e => handleChange('vad_turn_detection', e.target.checked)}
                        />
                        <label htmlFor="vad_turn_detection" className="text-sm font-medium">
                            VAD Turn Detection
                        </label>
                        <HelpTooltip
                            content={
                                <>
                                    <strong>VAD Turn Detection</strong> — use Deepgram's server-side
                                    Voice Activity Detection / endpointing to decide when the caller
                                    is done speaking.
                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                        <li>
                                            On: Deepgram emits <code>UtteranceEnd</code> and the
                                            agent responds
                                        </li>
                                        <li>
                                            Off: rely on client-side turn-taking (not recommended
                                            for telephony)
                                        </li>
                                        <li>
                                            Flux models have their own EOT detection — see Flux
                                            tuning below
                                        </li>
                                    </ul>
                                </>
                            }
                            link="https://developers.deepgram.com/docs/endpointing"
                            linkText="Endpointing docs"
                        />
                    </div>
                </div>


                {/* Flux tuning — only when a flux-* model is selected */}
                {(config.model || '').startsWith('flux-') && (
                    <div className="space-y-3 border border-blue-300/40 rounded-lg p-3 bg-blue-500/5">
                        <h5 className="text-sm font-semibold">Flux Turn-Detection Tuning</h5>
                        <p className="text-xs text-muted-foreground">
                            Flux ships its own EndOfTurn / EagerEndOfTurn detection. Defaults work
                            for most voice-agent deployments; tune only if you observe early
                            cut-offs or sluggish turn taking. See{' '}
                            <a
                                href="https://developers.deepgram.com/docs/configure-voice-agent"
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-blue-500 hover:underline"
                            >
                                Configure Voice Agent ↗
                            </a>
                            .
                        </p>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <div className="flex items-center gap-1.5">
                                    <label className="text-sm font-medium">
                                        EndOfTurn Threshold (eot_threshold)
                                    </label>
                                    <HelpTooltip
                                        content={
                                            <>
                                                <strong>EndOfTurn Threshold</strong> — confidence
                                                Flux requires to commit "the caller is done
                                                speaking" and let the agent respond.
                                                <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                    <li>
                                                        Range <code>0.5</code>–<code>0.9</code>,
                                                        default <code>0.7</code>
                                                    </li>
                                                    <li>
                                                        Higher = wait longer, fewer early cut-offs
                                                        but more lag
                                                    </li>
                                                    <li>
                                                        Lower = snappier turn-taking, risk of
                                                        interrupting the caller
                                                    </li>
                                                </ul>
                                            </>
                                        }
                                        link="https://developers.deepgram.com/docs/configure-voice-agent"
                                        linkText="Flux tuning"
                                    />
                                </div>
                                <input
                                    type="number"
                                    step="0.05"
                                    min={0.5}
                                    max={0.9}
                                    className="w-full p-2 rounded border border-input bg-background"
                                    value={config.eot_threshold ?? ''}
                                    onChange={e =>
                                        handleChange(
                                            'eot_threshold',
                                            e.target.value === ''
                                                ? null
                                                : parseFloat(e.target.value)
                                        )
                                    }
                                    placeholder="0.7 (default)"
                                />
                                <p className="text-xs text-muted-foreground">
                                    Valid range 0.5–0.9. Higher = wait longer before declaring end
                                    of turn.
                                </p>
                            </div>
                            <div className="space-y-2">
                                <div className="flex items-center gap-1.5">
                                    <label className="text-sm font-medium">
                                        EagerEndOfTurn Threshold (eager_eot_threshold)
                                    </label>
                                    <HelpTooltip
                                        content={
                                            <>
                                                <strong>EagerEndOfTurn Threshold</strong> — earlier,
                                                lower-confidence signal that lets the LLM start
                                                "thinking ahead" before the final EOT fires.
                                                <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                    <li>
                                                        Range <code>0.3</code>–<code>0.9</code>;
                                                        must be strictly less than{' '}
                                                        <code>eot_threshold</code>
                                                    </li>
                                                    <li>Empty = disabled (no eager processing)</li>
                                                    <li>
                                                        Trades extra LLM cost for lower perceived
                                                        latency
                                                    </li>
                                                </ul>
                                            </>
                                        }
                                        link="https://developers.deepgram.com/docs/configure-voice-agent"
                                        linkText="Flux tuning"
                                    />
                                </div>
                                <input
                                    type="number"
                                    step="0.05"
                                    min={0.3}
                                    max={0.9}
                                    className="w-full p-2 rounded border border-input bg-background"
                                    value={config.eager_eot_threshold ?? ''}
                                    onChange={e =>
                                        handleChange(
                                            'eager_eot_threshold',
                                            e.target.value === ''
                                                ? null
                                                : parseFloat(e.target.value)
                                        )
                                    }
                                    placeholder="empty = disabled"
                                />
                                <p className="text-xs text-muted-foreground">
                                    Valid range 0.3–0.9. Must be strictly less than eot_threshold.
                                    Empty = disabled.
                                </p>
                            </div>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">
                                    Keyterms (comma-separated)
                                </label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Keyterms</strong> — domain vocabulary (brand
                                            names, jargon, place names) to bias Flux STT toward.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>
                                                    Comma-separated, e.g.{' '}
                                                    <code>NEMT, dispatcher, Medicaid</code>
                                                </li>
                                                <li>
                                                    Useful for proper nouns that generic models
                                                    mis-transcribe
                                                </li>
                                                <li>Empty to skip</li>
                                            </ul>
                                        </>
                                    }
                                    link="https://developers.deepgram.com/docs/keyterm"
                                    linkText="Keyterm prompting"
                                />
                            </div>
                            <input
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={
                                    Array.isArray(config.keyterms)
                                        ? config.keyterms.join(', ')
                                        : (config.keyterms ?? '')
                                }
                                onChange={e => {
                                    const raw = e.target.value;
                                    const list = raw
                                        .split(',')
                                        .map(s => s.trim())
                                        .filter(s => s.length > 0);
                                    handleChange('keyterms', list.length > 0 ? list : null);
                                }}
                                placeholder="e.g. NEMT, ride, dispatcher"
                            />
                            <p className="text-xs text-muted-foreground">
                                Domain vocabulary to bias Flux STT toward. Leave empty to skip.
                            </p>
                        </div>
                    </div>
                )}

                <div className="space-y-2 border border-amber-300/40 rounded-lg p-3 bg-amber-500/5">
                    <label className="flex items-center gap-2 text-sm font-medium">
                        <input
                            type="checkbox"
                            className="rounded border-input"
                            checked={showOutputAutodetectExpert}
                            onChange={e => setShowOutputAutodetectExpert(e.target.checked)}
                        />
                        Expert Settings
                    </label>
                    <p
                        className={`text-xs ${showOutputAutodetectExpert ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'}`}
                    >
                        {showOutputAutodetectExpert
                            ? 'Warning: output auto-detect can alter runtime audio interpretation and should be used only for provider mismatch scenarios.'
                            : 'Advanced output-detection value is shown and locked until expert mode is enabled.'}
                    </p>
                    <label className="flex items-center gap-2 text-sm font-medium">
                        <input
                            type="checkbox"
                            className="rounded border-input disabled:cursor-not-allowed disabled:opacity-50"
                            checked={config.allow_output_autodetect ?? false}
                            onChange={e =>
                                handleChange('allow_output_autodetect', e.target.checked)
                            }
                            disabled={!showOutputAutodetectExpert}
                        />
                        Allow Output Auto-Detect
                    </label>
                </div>
            </div>
            <ConnectionRecoveryFields config={config} onChange={onChange} />
        </div>
    );
};

export default DeepgramProviderForm;
