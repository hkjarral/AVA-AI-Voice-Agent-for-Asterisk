import React, { useEffect, useState } from 'react';
import ProviderCredentialsCard, { applyCredentialPatch } from './ProviderCredentialsCard';
import HelpTooltip from '../../ui/HelpTooltip';

interface DeepgramProviderFormProps {
    config: any;
    onChange: (newConfig: any) => void;
    providerKey?: string;
}

const DeepgramProviderForm: React.FC<DeepgramProviderFormProps> = ({ config, onChange, providerKey }) => {
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
                    onConfigPatch={(patch) => applyCredentialPatch(patch, onChange)}
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
                                <span className="text-xs text-muted-foreground ml-2">(voice_agent_base_url)</span>
                            </label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Voice Agent WebSocket</strong> — endpoint for Deepgram's all-in-one Voice Agent (Listen + Think + Speak over a single WS).
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>US: <code>wss://agent.deepgram.com/v1/agent/converse</code></li>
                                            <li>EU: <code>wss://agent.eu.deepgram.com/v1/agent/converse</code></li>
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
                            value={config.voice_agent_base_url || 'wss://agent.deepgram.com/v1/agent/converse'}
                            onChange={(e) => handleChange('voice_agent_base_url', e.target.value)}
                            placeholder="wss://agent.deepgram.com/v1/agent/converse"
                        />
                        <p className="text-xs text-muted-foreground">
                            Deepgram Voice Agent WebSocket endpoint for full agent provider. Change for EU region (wss://agent.eu.deepgram.com/v1/agent/converse).
                        </p>
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">
                                REST API Base URL
                                <span className="text-xs text-muted-foreground ml-2">(base_url)</span>
                            </label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>REST API Base URL</strong> — used by Deepgram in pipeline mode (separate STT/TTS HTTP calls), not the Voice Agent WS.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>US: <code>https://api.deepgram.com</code></li>
                                            <li>EU: <code>https://api.eu.deepgram.com</code></li>
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
                            onChange={(e) => handleChange('base_url', e.target.value)}
                            placeholder="https://api.deepgram.com"
                        />
                        <p className="text-xs text-muted-foreground">
                            Deepgram REST API endpoint for STT/TTS in pipeline mode. Change for EU region (https://api.eu.deepgram.com) or proxy.
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
                                        <strong>Listen (STT) Model</strong> — which Deepgram STT model the Voice Agent (or pipeline) uses for transcription.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>flux-general-en</code> — beta, sub-200ms turn-taking with built-in EOT detection (best for voice agents)</li>
                                            <li><code>nova-3</code> — recommended general default, 47+ languages</li>
                                            <li><code>nova-2-phonecall</code> — tuned for telephony audio</li>
                                            <li><code>nova-2-medical</code> / <code>nova-2-finance</code> — domain-specific</li>
                                        </ul>
                                        Switching away from a <code>flux-*</code> model clears Flux-only tuning fields.
                                    </>
                                }
                                link="https://developers.deepgram.com/docs/models-languages-overview"
                                linkText="STT models"
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.model || 'nova-3'}
                            onChange={(e) => {
                                const nextModel = e.target.value;
                                const isFluxModel = nextModel.startsWith('flux-');
                                if (isFluxModel) {
                                    handleChange('model', nextModel);
                                } else {
                                    // Clear Flux-only tuning fields when switching away from a flux-* model
                                    // so stale values don't persist into Nova/other model configs.
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
                                <option value="flux-general-en">Flux General — English (recommended for voice agents)</option>
                                <option value="flux-general-multi">Flux General — Multilingual</option>
                            </optgroup>
                            <optgroup label="Nova-3 Multilingual (47+ languages)">
                                <option value="nova-3">Nova-3 General — EN, ES, FR, DE, HI, RU, PT, JA, IT, NL +37 more</option>
                                <option value="nova-3-medical">Nova-3 Medical — English only</option>
                            </optgroup>
                            <optgroup label="Nova-2 Multilingual (36+ languages)">
                                <option value="nova-2">Nova-2 General — EN, ES, FR, DE, JA, KO, ZH, PT, IT +27 more</option>
                            </optgroup>
                            <optgroup label="Nova-2 English Optimized">
                                <option value="nova-2-phonecall">Nova-2 Phone Call — English (telephony optimized)</option>
                                <option value="nova-2-meeting">Nova-2 Meeting — English (meetings/conferences)</option>
                                <option value="nova-2-voicemail">Nova-2 Voicemail — English</option>
                                <option value="nova-2-finance">Nova-2 Finance — English (financial terms)</option>
                                <option value="nova-2-conversationalai">Nova-2 Conversational AI — English (voice agents)</option>
                                <option value="nova-2-video">Nova-2 Video — English</option>
                                <option value="nova-2-medical">Nova-2 Medical — English (medical terminology)</option>
                                <option value="nova-2-drivethru">Nova-2 Drive-thru — English (noisy environments)</option>
                                <option value="nova-2-automotive">Nova-2 Automotive — English (in-car)</option>
                                <option value="nova-2-atc">Nova-2 Air Traffic Control — English (aviation)</option>
                            </optgroup>
                            <optgroup label="Nova Legacy (English)">
                                <option value="nova">Nova General</option>
                                <option value="nova-phonecall">Nova Phone Call</option>
                                <option value="nova-drivethru">Nova Drive-thru</option>
                                <option value="nova-medical">Nova Medical</option>
                                <option value="nova-voicemail">Nova Voicemail</option>
                            </optgroup>
                            <optgroup label="Other Models">
                                <option value="enhanced">Enhanced — Legacy multilingual</option>
                                <option value="base">Base — Legacy</option>
                                <option value="whisper-cloud">Whisper Cloud — Multilingual</option>
                            </optgroup>
                        </select>
                        <p className="text-xs text-muted-foreground">
                            Nova-3/Nova-2 General for multilingual; specialized models for English use-cases.
                            <a href="https://developers.deepgram.com/docs/models-languages-overview" target="_blank" rel="noopener noreferrer" className="ml-1 text-blue-500 hover:underline">Language Support ↗</a>
                        </p>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">
                                STT Language
                                <span className="text-xs text-muted-foreground ml-2">(stt_language)</span>
                            </label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>STT Language</strong> — default BCP-47 language tag passed to Deepgram for transcription in pipeline mode.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>en-US</code>, <code>en-GB</code>, <code>es</code>, <code>fr</code>, <code>de</code>, <code>multi</code>, etc.</li>
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
                            onChange={(e) => handleChange('stt_language', e.target.value)}
                            placeholder="en-US"
                        />
                        <p className="text-xs text-muted-foreground">
                            Default language for STT in pipeline mode (can be overridden per pipeline/context).
                        </p>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">
                                Agent Language
                                <span className="text-xs text-muted-foreground ml-2">(agent_language)</span>
                            </label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Agent Language</strong> — language for the full Voice Agent conversation (Listen + Speak).
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>Must match the language of your selected TTS voice</li>
                                            <li>Aura-2 voices are language-tagged (e.g. <code>-en</code>, <code>-es</code>, <code>-de</code>)</li>
                                        </ul>
                                    </>
                                }
                                link="https://developers.deepgram.com/docs/configure-voice-agent"
                                linkText="Configure Voice Agent"
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.agent_language || 'en'}
                            onChange={(e) => handleChange('agent_language', e.target.value)}
                        >
                            <optgroup label="English">
                                <option value="en">English (en)</option>
                                <option value="en-US">English US (en-US)</option>
                                <option value="en-GB">English UK (en-GB)</option>
                                <option value="en-AU">English AU (en-AU)</option>
                                <option value="en-IN">English IN (en-IN)</option>
                            </optgroup>
                            <optgroup label="Spanish">
                                <option value="es">Spanish (es)</option>
                                <option value="es-419">Spanish LATAM (es-419)</option>
                            </optgroup>
                            <optgroup label="European">
                                <option value="fr">French (fr)</option>
                                <option value="de">German (de)</option>
                                <option value="it">Italian (it)</option>
                                <option value="pt">Portuguese (pt)</option>
                                <option value="pt-BR">Portuguese BR (pt-BR)</option>
                                <option value="nl">Dutch (nl)</option>
                                <option value="pl">Polish (pl)</option>
                                <option value="uk">Ukrainian (uk)</option>
                                <option value="ru">Russian (ru)</option>
                                <option value="sv">Swedish (sv)</option>
                                <option value="da">Danish (da)</option>
                                <option value="no">Norwegian (no)</option>
                                <option value="fi">Finnish (fi)</option>
                                <option value="cs">Czech (cs)</option>
                                <option value="el">Greek (el)</option>
                                <option value="tr">Turkish (tr)</option>
                            </optgroup>
                            <optgroup label="Asian">
                                <option value="ja">Japanese (ja)</option>
                                <option value="zh">Chinese (zh)</option>
                                <option value="ko">Korean (ko)</option>
                                <option value="hi">Hindi (hi)</option>
                                <option value="id">Indonesian (id)</option>
                                <option value="ms">Malay (ms)</option>
                                <option value="th">Thai (th)</option>
                                <option value="vi">Vietnamese (vi)</option>
                            </optgroup>
                            <optgroup label="Other">
                                <option value="he">Hebrew (he)</option>
                                <option value="ar">Arabic (ar)</option>
                            </optgroup>
                        </select>
                        <p className="text-xs text-muted-foreground">
                            Language for Voice Agent conversation. Must match your TTS voice language.
                        </p>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">
                                Voice Model
                                <span className="text-xs text-muted-foreground ml-2">(tts_model)</span>
                            </label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Speak (TTS) Voice</strong> — Deepgram Aura voice that synthesizes the agent's replies.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>aura-2-*</code> — newest generation, best naturalness</li>
                                            <li><code>aura-*</code> — legacy, still supported</li>
                                            <li>Voice locale (<code>-en</code>, <code>-es</code>, etc.) must match Agent Language</li>
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
                            value={config.tts_model || 'aura-2-thalia-en'}
                            onChange={(e) => handleChange('tts_model', e.target.value)}
                        >
                            <optgroup label="🇺🇸 English - Aura-2 Female">
                                <option value="aura-2-thalia-en">Thalia (EN)</option>
                                <option value="aura-2-asteria-en">Asteria (EN)</option>
                                <option value="aura-2-luna-en">Luna (EN)</option>
                                <option value="aura-2-athena-en">Athena (EN)</option>
                                <option value="aura-2-hera-en">Hera (EN)</option>
                                <option value="aura-2-andromeda-en">Andromeda (EN)</option>
                                <option value="aura-2-aurora-en">Aurora (EN)</option>
                                <option value="aura-2-callista-en">Callista (EN)</option>
                                <option value="aura-2-cora-en">Cora (EN)</option>
                                <option value="aura-2-cordelia-en">Cordelia (EN)</option>
                                <option value="aura-2-delia-en">Delia (EN)</option>
                                <option value="aura-2-electra-en">Electra (EN)</option>
                                <option value="aura-2-harmonia-en">Harmonia (EN)</option>
                                <option value="aura-2-helena-en">Helena (EN)</option>
                                <option value="aura-2-iris-en">Iris (EN)</option>
                                <option value="aura-2-juno-en">Juno (EN)</option>
                                <option value="aura-2-minerva-en">Minerva (EN)</option>
                                <option value="aura-2-ophelia-en">Ophelia (EN)</option>
                                <option value="aura-2-pandora-en">Pandora (EN)</option>
                                <option value="aura-2-phoebe-en">Phoebe (EN)</option>
                                <option value="aura-2-selene-en">Selene (EN)</option>
                                <option value="aura-2-theia-en">Theia (EN)</option>
                                <option value="aura-2-vesta-en">Vesta (EN)</option>
                                <option value="aura-2-amalthea-en">Amalthea (EN)</option>
                            </optgroup>
                            <optgroup label="🇺🇸 English - Aura-2 Male">
                                <option value="aura-2-orion-en">Orion (EN)</option>
                                <option value="aura-2-arcas-en">Arcas (EN)</option>
                                <option value="aura-2-orpheus-en">Orpheus (EN)</option>
                                <option value="aura-2-zeus-en">Zeus (EN)</option>
                                <option value="aura-2-apollo-en">Apollo (EN)</option>
                                <option value="aura-2-aries-en">Aries (EN)</option>
                                <option value="aura-2-atlas-en">Atlas (EN)</option>
                                <option value="aura-2-draco-en">Draco (EN)</option>
                                <option value="aura-2-hermes-en">Hermes (EN)</option>
                                <option value="aura-2-hyperion-en">Hyperion (EN)</option>
                                <option value="aura-2-janus-en">Janus (EN)</option>
                                <option value="aura-2-jupiter-en">Jupiter (EN)</option>
                                <option value="aura-2-mars-en">Mars (EN)</option>
                                <option value="aura-2-neptune-en">Neptune (EN)</option>
                                <option value="aura-2-odysseus-en">Odysseus (EN)</option>
                                <option value="aura-2-pluto-en">Pluto (EN)</option>
                                <option value="aura-2-saturn-en">Saturn (EN)</option>
                            </optgroup>
                            <optgroup label="🇪🇸 Spanish - Aura-2 (17 voices)">
                                <option value="aura-2-celeste-es">Celeste (ES) ⭐</option>
                                <option value="aura-2-estrella-es">Estrella (ES) ⭐</option>
                                <option value="aura-2-nestor-es">Nestor (ES) ⭐</option>
                                <option value="aura-2-diana-es">Diana (ES) 🔄</option>
                                <option value="aura-2-javier-es">Javier (ES) 🔄</option>
                                <option value="aura-2-selena-es">Selena (ES) 🔄</option>
                                <option value="aura-2-aquila-es">Aquila (ES) 🔄</option>
                                <option value="aura-2-carina-es">Carina (ES) 🔄</option>
                                <option value="aura-2-agustina-es">Agustina (ES)</option>
                                <option value="aura-2-antonia-es">Antonia (ES)</option>
                                <option value="aura-2-gloria-es">Gloria (ES)</option>
                                <option value="aura-2-olivia-es">Olivia (ES)</option>
                                <option value="aura-2-silvia-es">Silvia (ES)</option>
                                <option value="aura-2-sirio-es">Sirio (ES)</option>
                                <option value="aura-2-alvaro-es">Alvaro (ES)</option>
                                <option value="aura-2-luciano-es">Luciano (ES)</option>
                                <option value="aura-2-valerio-es">Valerio (ES)</option>
                            </optgroup>
                            <optgroup label="🇩🇪 German - Aura-2 (7 voices)">
                                <option value="aura-2-julius-de">Julius (DE) ⭐</option>
                                <option value="aura-2-viktoria-de">Viktoria (DE) ⭐</option>
                                <option value="aura-2-elara-de">Elara (DE)</option>
                                <option value="aura-2-aurelia-de">Aurelia (DE)</option>
                                <option value="aura-2-lara-de">Lara (DE)</option>
                                <option value="aura-2-fabian-de">Fabian (DE)</option>
                                <option value="aura-2-kara-de">Kara (DE)</option>
                            </optgroup>
                            <optgroup label="🇫🇷 French - Aura-2 (2 voices)">
                                <option value="aura-2-agathe-fr">Agathe (FR) ⭐</option>
                                <option value="aura-2-hector-fr">Hector (FR) ⭐</option>
                            </optgroup>
                            <optgroup label="🇮🇹 Italian - Aura-2 (10 voices)">
                                <option value="aura-2-livia-it">Livia (IT) ⭐</option>
                                <option value="aura-2-dionisio-it">Dionisio (IT) ⭐</option>
                                <option value="aura-2-melia-it">Melia (IT)</option>
                                <option value="aura-2-elio-it">Elio (IT)</option>
                                <option value="aura-2-flavio-it">Flavio (IT)</option>
                                <option value="aura-2-maia-it">Maia (IT)</option>
                                <option value="aura-2-cinzia-it">Cinzia (IT)</option>
                                <option value="aura-2-cesare-it">Cesare (IT)</option>
                                <option value="aura-2-perseo-it">Perseo (IT)</option>
                                <option value="aura-2-demetra-it">Demetra (IT)</option>
                            </optgroup>
                            <optgroup label="🇳🇱 Dutch - Aura-2 (9 voices)">
                                <option value="aura-2-rhea-nl">Rhea (NL) ⭐</option>
                                <option value="aura-2-sander-nl">Sander (NL) ⭐</option>
                                <option value="aura-2-beatrix-nl">Beatrix (NL) ⭐</option>
                                <option value="aura-2-daphne-nl">Daphne (NL)</option>
                                <option value="aura-2-cornelia-nl">Cornelia (NL)</option>
                                <option value="aura-2-hestia-nl">Hestia (NL)</option>
                                <option value="aura-2-lars-nl">Lars (NL)</option>
                                <option value="aura-2-roman-nl">Roman (NL)</option>
                                <option value="aura-2-leda-nl">Leda (NL)</option>
                            </optgroup>
                            <optgroup label="🇯🇵 Japanese - Aura-2 (5 voices)">
                                <option value="aura-2-fujin-ja">Fujin (JA) ⭐</option>
                                <option value="aura-2-izanami-ja">Izanami (JA) ⭐</option>
                                <option value="aura-2-uzume-ja">Uzume (JA)</option>
                                <option value="aura-2-ebisu-ja">Ebisu (JA)</option>
                                <option value="aura-2-ama-ja">Ama (JA)</option>
                            </optgroup>
                            <optgroup label="🇺🇸 English - Aura Legacy">
                                <option value="aura-asteria-en">Asteria (EN Legacy)</option>
                                <option value="aura-luna-en">Luna (EN Legacy)</option>
                                <option value="aura-stella-en">Stella (EN Legacy)</option>
                                <option value="aura-athena-en">Athena (EN Legacy)</option>
                                <option value="aura-hera-en">Hera (EN Legacy)</option>
                                <option value="aura-orion-en">Orion (EN Legacy)</option>
                                <option value="aura-arcas-en">Arcas (EN Legacy)</option>
                                <option value="aura-perseus-en">Perseus (EN Legacy)</option>
                                <option value="aura-angus-en">Angus (EN Legacy)</option>
                                <option value="aura-orpheus-en">Orpheus (EN Legacy)</option>
                                <option value="aura-helios-en">Helios (EN Legacy)</option>
                                <option value="aura-zeus-en">Zeus (EN Legacy)</option>
                            </optgroup>
                        </select>
                        <p className="text-xs text-muted-foreground">
                            ⭐ = Featured, 🔄 = Codeswitching (ES↔EN). EN (53), ES (17), DE (7), FR (2), IT (10), NL (9), JA (5).
                            <a href="https://developers.deepgram.com/docs/tts-models" target="_blank" rel="noopener noreferrer" className="ml-1 text-blue-500 hover:underline">All Voices ↗</a>
                        </p>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Input Encoding</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Input Encoding</strong> — audio codec of frames coming FROM Asterisk into the agent.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>mulaw</code> — standard US telephony (G.711 μ-law)</li>
                                            <li><code>alaw</code> — standard EU telephony (G.711 A-law)</li>
                                            <li><code>linear16</code> — uncompressed PCM, higher quality</li>
                                        </ul>
                                        Must match Asterisk's channel codec.
                                    </>
                                }
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.input_encoding || 'linear16'}
                            onChange={(e) => handleChange('input_encoding', e.target.value)}
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
                                        <strong>Input Sample Rate</strong> — sample rate of audio coming FROM Asterisk.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>8000</code> Hz — standard telephony (G.711)</li>
                                            <li><code>16000</code> Hz — wideband (G.722, Opus)</li>
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
                            onChange={(e) => handleChange('input_sample_rate_hz', parseInt(e.target.value))}
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
                                        <strong>Output Encoding</strong> — codec Deepgram returns TTS audio in.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>mulaw</code> @ 8 kHz — passes straight to G.711 telephony (no transcoding)</li>
                                            <li><code>linear16</code> — uncompressed PCM, needs resample/encode</li>
                                        </ul>
                                        Match telephony for lowest latency.
                                    </>
                                }
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.output_encoding || 'mulaw'}
                            onChange={(e) => handleChange('output_encoding', e.target.value)}
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
                                        <strong>Output Sample Rate</strong> — sample rate of TTS audio coming FROM Deepgram.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>8000</code> Hz — narrowband, telephony native</li>
                                            <li><code>16000</code> / <code>24000</code> Hz — higher fidelity, requires downsampling for G.711</li>
                                        </ul>
                                    </>
                                }
                            />
                        </div>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.output_sample_rate_hz || 8000}
                            onChange={(e) => handleChange('output_sample_rate_hz', parseInt(e.target.value))}
                        />
                        <p className="text-xs text-muted-foreground">
                            Sample rate from Deepgram. 8000 Hz for telephony, 16000 Hz for higher quality.
                        </p>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Target Encoding</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Target Encoding</strong> — final codec written to the Asterisk channel (after any internal transcoding).
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>Should match your Asterisk dialplan codec (usually <code>mulaw</code> for US, <code>alaw</code> for EU)</li>
                                        </ul>
                                    </>
                                }
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.target_encoding || 'mulaw'}
                            onChange={(e) => handleChange('target_encoding', e.target.value)}
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
                                        <strong>Target Sample Rate</strong> — final sample rate written to the Asterisk channel.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>8000</code> Hz for G.711 (μ-law / A-law)</li>
                                            <li><code>16000</code> Hz for wideband channels</li>
                                        </ul>
                                    </>
                                }
                            />
                        </div>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.target_sample_rate_hz || 8000}
                            onChange={(e) => handleChange('target_sample_rate_hz', parseInt(e.target.value))}
                        />
                        <p className="text-xs text-muted-foreground">
                            Final sample rate for playback. 8000 Hz for standard telephony.
                        </p>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Provider Input Encoding</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Provider Input Encoding</strong> — codec the engine sends TO Deepgram (after any internal upsampling from telephony).
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>linear16</code> — recommended for best STT accuracy</li>
                                            <li><code>mulaw</code> — saves bandwidth, slightly lower accuracy</li>
                                        </ul>
                                    </>
                                }
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.provider_input_encoding || 'linear16'}
                            onChange={(e) => handleChange('provider_input_encoding', e.target.value)}
                        >
                            <option value="linear16">Linear16 (PCM)</option>
                            <option value="mulaw">μ-law</option>
                        </select>
                        <p className="text-xs text-muted-foreground">
                            Format sent to Deepgram. Linear16 recommended for best STT accuracy.
                        </p>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Provider Input Sample Rate (Hz)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Provider Input Sample Rate</strong> — sample rate sent TO Deepgram for STT.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>16000</code> Hz — optimal for Nova / Flux models</li>
                                            <li><code>8000</code> Hz — telephony-native, skips upsampling</li>
                                        </ul>
                                    </>
                                }
                            />
                        </div>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.provider_input_sample_rate_hz || 16000}
                            onChange={(e) => handleChange('provider_input_sample_rate_hz', parseInt(e.target.value))}
                        />
                        <p className="text-xs text-muted-foreground">
                            Sample rate for Deepgram input. 16000 Hz optimal for Nova models.
                        </p>
                    </div>
                </div>

                <div className="space-y-2">
                    <div className="flex items-center gap-1.5">
                        <label className="text-sm font-medium">System Instructions</label>
                        <HelpTooltip
                            content={
                                <>
                                    <strong>System Instructions</strong> — system prompt sent to the Voice Agent's Think (LLM) stage. Defines persona, scope, and behavior.
                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                        <li>Keep it concise — long prompts add latency on every turn</li>
                                        <li>Use the prompt to declare tools, refusal policies, and language</li>
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
                        onChange={(e) => handleChange('instructions', e.target.value)}
                        placeholder="You are a helpful assistant..."
                    />
                </div>

                <div className="space-y-2">
                    <div className="flex items-center gap-1.5">
                        <label className="text-sm font-medium">Greeting</label>
                        <HelpTooltip
                            content={
                                <>
                                    <strong>Greeting</strong> — first utterance the agent speaks when the call connects, before any user input.
                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                        <li>Spoken via the configured TTS voice immediately on session start</li>
                                        <li>Leave empty for a silent open (agent waits for caller to speak)</li>
                                    </ul>
                                </>
                            }
                        />
                    </div>
                    <input
                        type="text"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.greeting || ''}
                        onChange={(e) => handleChange('greeting', e.target.value)}
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
                            onChange={(e) => handleChange('enabled', e.target.checked)}
                        />
                        <label htmlFor="enabled" className="text-sm font-medium">Enabled</label>
                        <HelpTooltip
                            content={
                                <>
                                    <strong>Enabled</strong> — when off, this provider instance is skipped during pipeline selection and the engine falls back to another configured provider.
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
                            onChange={(e) => handleChange('continuous_input', e.target.checked)}
                        />
                        <label htmlFor="continuous_input" className="text-sm font-medium">Continuous Input</label>
                        <HelpTooltip
                            content={
                                <>
                                    <strong>Continuous Input</strong> — keep streaming Asterisk audio to Deepgram even while the agent is speaking, enabling barge-in.
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
                            onChange={(e) => handleChange('vad_turn_detection', e.target.checked)}
                        />
                        <label htmlFor="vad_turn_detection" className="text-sm font-medium">VAD Turn Detection</label>
                        <HelpTooltip
                            content={
                                <>
                                    <strong>VAD Turn Detection</strong> — use Deepgram's server-side Voice Activity Detection / endpointing to decide when the caller is done speaking.
                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                        <li>On: Deepgram emits <code>UtteranceEnd</code> and the agent responds</li>
                                        <li>Off: rely on client-side turn-taking (not recommended for telephony)</li>
                                        <li>Flux models have their own EOT detection — see Flux tuning below</li>
                                    </ul>
                                </>
                            }
                            link="https://developers.deepgram.com/docs/endpointing"
                            linkText="Endpointing docs"
                        />
                    </div>
                </div>

                <div className="space-y-2">
                    <div className="flex items-center gap-1.5">
                        <label className="text-sm font-medium">Farewell Hangup Delay (seconds)</label>
                        <HelpTooltip
                            content={
                                <>
                                    <strong>Farewell Hangup Delay</strong> — wait this long after the agent finishes its final TTS playback before hanging up the channel.
                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                        <li>Prevents clipping the last word</li>
                                        <li>Leave empty to use the global default (2.5s)</li>
                                    </ul>
                                </>
                            }
                        />
                    </div>
                    <input
                        type="number"
                        step="0.5"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.farewell_hangup_delay_sec ?? ''}
                        onChange={(e) => handleChange('farewell_hangup_delay_sec', e.target.value ? parseFloat(e.target.value) : null)}
                        placeholder="Use global default (2.5s)"
                    />
                    <p className="text-xs text-muted-foreground">
                        Seconds to wait after farewell audio before hanging up. Leave empty to use global default.
                    </p>
                </div>

                {/* Flux tuning — only when a flux-* model is selected */}
                {(config.model || '').startsWith('flux-') && (
                    <div className="space-y-3 border border-blue-300/40 rounded-lg p-3 bg-blue-500/5">
                        <h5 className="text-sm font-semibold">Flux Turn-Detection Tuning</h5>
                        <p className="text-xs text-muted-foreground">
                            Flux ships its own EndOfTurn / EagerEndOfTurn detection. Defaults work for most voice-agent
                            deployments; tune only if you observe early cut-offs or sluggish turn taking.
                            See{' '}
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
                                    <label className="text-sm font-medium">EndOfTurn Threshold (eot_threshold)</label>
                                    <HelpTooltip
                                        content={
                                            <>
                                                <strong>EndOfTurn Threshold</strong> — confidence Flux requires to commit "the caller is done speaking" and let the agent respond.
                                                <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                    <li>Range <code>0.5</code>–<code>0.9</code>, default <code>0.7</code></li>
                                                    <li>Higher = wait longer, fewer early cut-offs but more lag</li>
                                                    <li>Lower = snappier turn-taking, risk of interrupting the caller</li>
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
                                    onChange={(e) =>
                                        handleChange(
                                            'eot_threshold',
                                            e.target.value === '' ? null : parseFloat(e.target.value),
                                        )
                                    }
                                    placeholder="0.7 (default)"
                                />
                                <p className="text-xs text-muted-foreground">
                                    Valid range 0.5–0.9. Higher = wait longer before declaring end of turn.
                                </p>
                            </div>
                            <div className="space-y-2">
                                <div className="flex items-center gap-1.5">
                                    <label className="text-sm font-medium">EagerEndOfTurn Threshold (eager_eot_threshold)</label>
                                    <HelpTooltip
                                        content={
                                            <>
                                                <strong>EagerEndOfTurn Threshold</strong> — earlier, lower-confidence signal that lets the LLM start "thinking ahead" before the final EOT fires.
                                                <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                    <li>Range <code>0.3</code>–<code>0.9</code>; must be strictly less than <code>eot_threshold</code></li>
                                                    <li>Empty = disabled (no eager processing)</li>
                                                    <li>Trades extra LLM cost for lower perceived latency</li>
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
                                    onChange={(e) =>
                                        handleChange(
                                            'eager_eot_threshold',
                                            e.target.value === '' ? null : parseFloat(e.target.value),
                                        )
                                    }
                                    placeholder="empty = disabled"
                                />
                                <p className="text-xs text-muted-foreground">
                                    Valid range 0.3–0.9. Must be strictly less than eot_threshold. Empty = disabled.
                                </p>
                            </div>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Keyterms (comma-separated)</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Keyterms</strong> — domain vocabulary (brand names, jargon, place names) to bias Flux STT toward.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>Comma-separated, e.g. <code>NEMT, dispatcher, Medicaid</code></li>
                                                <li>Useful for proper nouns that generic models mis-transcribe</li>
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
                                value={Array.isArray(config.keyterms) ? config.keyterms.join(', ') : (config.keyterms ?? '')}
                                onChange={(e) => {
                                    const raw = e.target.value;
                                    const list = raw
                                        .split(',')
                                        .map((s) => s.trim())
                                        .filter((s) => s.length > 0);
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
                            onChange={(e) => setShowOutputAutodetectExpert(e.target.checked)}
                        />
                        Expert Settings
                    </label>
                    <p className={`text-xs ${showOutputAutodetectExpert ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'}`}>
                        {showOutputAutodetectExpert
                            ? 'Warning: output auto-detect can alter runtime audio interpretation and should be used only for provider mismatch scenarios.'
                            : 'Advanced output-detection value is shown and locked until expert mode is enabled.'}
                    </p>
                    <label className="flex items-center gap-2 text-sm font-medium">
                        <input
                            type="checkbox"
                            className="rounded border-input disabled:cursor-not-allowed disabled:opacity-50"
                            checked={config.allow_output_autodetect ?? false}
                            onChange={(e) => handleChange('allow_output_autodetect', e.target.checked)}
                            disabled={!showOutputAutodetectExpert}
                        />
                        Allow Output Auto-Detect
                    </label>
                </div>
            </div>
        </div>
    );
};

export default DeepgramProviderForm;
