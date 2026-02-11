import React, { useEffect, useState } from 'react';

interface DeepgramProviderFormProps {
    config: any;
    onChange: (newConfig: any) => void;
}

const DeepgramProviderForm: React.FC<DeepgramProviderFormProps> = ({ config, onChange }) => {
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
            {/* Base URL Section */}
            <div>
                <h4 className="font-semibold mb-3">API Endpoints</h4>
                <div className="grid grid-cols-1 gap-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">
                            Voice Agent WebSocket URL
                            <span className="text-xs text-muted-foreground ml-2">(voice_agent_base_url)</span>
                        </label>
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
                        <label className="text-sm font-medium">
                            REST API Base URL
                            <span className="text-xs text-muted-foreground ml-2">(base_url)</span>
                        </label>
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
                        <label className="text-sm font-medium">STT Model</label>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.model || 'nova-2-phonecall'}
                            onChange={(e) => handleChange('model', e.target.value)}
                        >
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
                        <label className="text-sm font-medium">
                            STT Language
                            <span className="text-xs text-muted-foreground ml-2">(stt_language)</span>
                        </label>
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
                        <label className="text-sm font-medium">
                            Agent Language
                            <span className="text-xs text-muted-foreground ml-2">(agent_language)</span>
                        </label>
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
                        <label className="text-sm font-medium">
                            Voice Model
                            <span className="text-xs text-muted-foreground ml-2">(tts_model)</span>
                        </label>
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
                        <label className="text-sm font-medium">Input Encoding</label>
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
                        <label className="text-sm font-medium">Input Sample Rate (Hz)</label>
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
                        <label className="text-sm font-medium">Output Encoding</label>
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
                        <label className="text-sm font-medium">Output Sample Rate (Hz)</label>
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
                        <label className="text-sm font-medium">Target Encoding</label>
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
                        <label className="text-sm font-medium">Target Sample Rate (Hz)</label>
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
                        <label className="text-sm font-medium">Provider Input Encoding</label>
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
                        <label className="text-sm font-medium">Provider Input Sample Rate (Hz)</label>
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
                    <label className="text-sm font-medium">System Instructions</label>
                    <textarea
                        className="w-full p-2 rounded border border-input bg-background min-h-[100px] font-mono text-sm"
                        value={config.instructions || ''}
                        onChange={(e) => handleChange('instructions', e.target.value)}
                        placeholder="You are a helpful assistant..."
                    />
                </div>

                <div className="space-y-2">
                    <label className="text-sm font-medium">Greeting</label>
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
                    </div>
                </div>

                <div className="space-y-2">
                    <label className="text-sm font-medium">Farewell Hangup Delay (seconds)</label>
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

            {/* Authentication Section */}
            <div>
                <h4 className="font-semibold mb-3">Authentication</h4>
                <div className="space-y-2">
                    <label className="text-sm font-medium">API Key (Environment Variable)</label>
                    <input
                        type="text"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.api_key || '${DEEPGRAM_API_KEY}'}
                        onChange={(e) => handleChange('api_key', e.target.value)}
                        placeholder="${DEEPGRAM_API_KEY}"
                    />
                    <p className="text-xs text-muted-foreground">Use {'${VAR_NAME}'} to reference environment variables</p>
                </div>
            </div>
        </div>
    );
};

export default DeepgramProviderForm;
