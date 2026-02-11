import React, { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import HelpTooltip from '../../ui/HelpTooltip';

interface GoogleLiveProviderFormProps {
    config: any;
    onChange: (newConfig: any) => void;
}

const DEFAULT_LIVE_MODEL = 'gemini-2.5-flash-native-audio-preview-12-2025';
const LEGACY_LIVE_MODEL_MAP: Record<string, string> = {
    'gemini-2.5-flash-native-audio-latest': DEFAULT_LIVE_MODEL,
    'gemini-live-2.5-flash-preview': DEFAULT_LIVE_MODEL,
    'gemini-2.0-flash-live-001': DEFAULT_LIVE_MODEL,
    'gemini-2.0-flash-live-001-preview-09-2025': DEFAULT_LIVE_MODEL,
    'gemini-2.5-flash-preview-native-audio-dialog': DEFAULT_LIVE_MODEL,
    'gemini-2.5-flash-exp-native-audio-thinking-dialog': DEFAULT_LIVE_MODEL,
};
const SUPPORTED_LIVE_MODELS = [
    'gemini-2.5-flash-native-audio-preview-12-2025',
    'gemini-2.5-flash-native-audio-preview-09-2025',
    'gemini-live-2.5-flash-native-audio',
    'gemini-live-2.5-flash-preview-native-audio-09-2025',
];

const GoogleLiveProviderForm: React.FC<GoogleLiveProviderFormProps> = ({ config, onChange }) => {
    const handleChange = (field: string, value: any) => {
        onChange({ ...config, [field]: value });
    };

    const expertStorageKey = `providers.google_live.expert.keepalive.v1`;
    const [expertEnabled, setExpertEnabled] = useState<boolean>(() => {
        try {
            return window.localStorage.getItem(expertStorageKey) === 'true';
        } catch {
            return false;
        }
    });

    useEffect(() => {
        try {
            window.localStorage.setItem(expertStorageKey, expertEnabled ? 'true' : 'false');
        } catch {
            // ignore
        }
    }, [expertEnabled]);

    const selectedModel = (() => {
        const raw = (config.llm_model || '').toString().trim();
        if (!raw) {
            return DEFAULT_LIVE_MODEL;
        }
        if (raw in LEGACY_LIVE_MODEL_MAP) {
            return LEGACY_LIVE_MODEL_MAP[raw];
        }
        if (raw.includes('native-audio')) {
            return raw;
        }
        return DEFAULT_LIVE_MODEL;
    })();

    return (
        <div className="space-y-6">
            {/* Base URL Section */}
            <div>
                <h4 className="font-semibold mb-3">API Endpoint</h4>
                <div className="space-y-2">
                    <label className="text-sm font-medium">
                        WebSocket Endpoint
                        <span className="text-xs text-muted-foreground ml-2">(websocket_endpoint)</span>
                    </label>
                    <input
                        type="text"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.websocket_endpoint || 'wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent'}
                        onChange={(e) => handleChange('websocket_endpoint', e.target.value)}
                        placeholder="wss://generativelanguage.googleapis.com/ws/..."
                    />
                    <p className="text-xs text-muted-foreground">
                        Google Live bidirectional endpoint. Keep `v1beta` unless Google publishes a stable `v1` Live WS path.
                    </p>
                </div>
            </div>

            {/* Models & Voice Section */}
            <div>
                <h4 className="font-semibold mb-3">Models & Voice</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">LLM Model</label>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={selectedModel}
                            onChange={(e) => handleChange('llm_model', e.target.value)}
                        >
                            <optgroup label="Gemini Developer API">
                                <option value="gemini-2.5-flash-native-audio-preview-12-2025">Gemini 2.5 Flash Native Audio (Dec 2025)</option>
                                <option value="gemini-2.5-flash-native-audio-preview-09-2025">Gemini 2.5 Flash Native Audio (Sep 2025)</option>
                            </optgroup>
                            <optgroup label="Vertex AI Live API">
                                <option value="gemini-live-2.5-flash-native-audio">Gemini Live 2.5 Flash Native Audio (GA)</option>
                                <option value="gemini-live-2.5-flash-preview-native-audio-09-2025">Gemini Live 2.5 Flash Native Audio (Preview 09-2025)</option>
                            </optgroup>
                            {!SUPPORTED_LIVE_MODELS.includes(selectedModel) && (
                                <optgroup label="Custom">
                                    <option value={selectedModel}>{selectedModel}</option>
                                </optgroup>
                            )}
                        </select>
                        <p className="text-xs text-muted-foreground">
                            Includes official Gemini Developer API and Vertex AI Live model names.
                            <a href="https://ai.google.dev/gemini-api/docs/live-guide" target="_blank" rel="noopener noreferrer" className="ml-1 text-blue-500 hover:underline">API Docs ↗</a>
                        </p>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">TTS Voice Name</label>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.tts_voice_name || 'Aoede'}
                            onChange={(e) => handleChange('tts_voice_name', e.target.value)}
                        >
                            <optgroup label="Female">
                                <option value="Aoede">Aoede</option>
                                <option value="Kore">Kore</option>
                                <option value="Leda">Leda</option>
                            </optgroup>
                            <optgroup label="Male">
                                <option value="Puck">Puck</option>
                                <option value="Charon">Charon</option>
                                <option value="Fenrir">Fenrir</option>
                                <option value="Orus">Orus</option>
                                <option value="Zephyr">Zephyr</option>
                            </optgroup>
                        </select>
                        <p className="text-xs text-muted-foreground">
                            Multilingual voices - auto-switches between 24 languages without configuration.
                            <a href="https://firebase.google.com/docs/ai-logic/live-api/configuration" target="_blank" rel="noopener noreferrer" className="ml-1 text-blue-500 hover:underline">Voice Docs ↗</a>
                        </p>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Temperature</label>
                        <input
                            type="number"
                            step="0.1"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.llm_temperature || 0.7}
                            onChange={(e) => handleChange('llm_temperature', parseFloat(e.target.value))}
                        />
                        <p className="text-xs text-muted-foreground">
                            Controls randomness (0.0-2.0). Lower = more focused, higher = more creative.
                        </p>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Max Output Tokens</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.llm_max_output_tokens || 8192}
                            onChange={(e) => handleChange('llm_max_output_tokens', parseInt(e.target.value))}
                        />
                        <p className="text-xs text-muted-foreground">
                            Maximum tokens in response. Higher allows longer answers but increases latency.
                        </p>
                    </div>
                </div>

                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">Advanced Sampling</h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Top P</label>
                            <input
                                type="number"
                                step="0.01"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.llm_top_p || 0.95}
                                onChange={(e) => handleChange('llm_top_p', parseFloat(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Nucleus sampling (0.0-1.0). Considers tokens comprising top P probability mass.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Top K</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.llm_top_k || 40}
                                onChange={(e) => handleChange('llm_top_k', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Limits to top K most likely tokens. Lower = more focused responses.
                            </p>
                        </div>
                    </div>
                </div>

                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">Audio Configuration</h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Input Encoding</label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.input_encoding || 'ulaw'}
                                onChange={(e) => handleChange('input_encoding', e.target.value)}
                            >
                                <option value="ulaw">μ-law</option>
                                <option value="pcm16">PCM16</option>
                                <option value="linear16">Linear16</option>
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
                                value={config.output_encoding || 'linear16'}
                                onChange={(e) => handleChange('output_encoding', e.target.value)}
                            >
                                <option value="linear16">Linear16</option>
                                <option value="pcm16">PCM16</option>
                                <option value="ulaw">μ-law</option>
                            </select>
                            <p className="text-xs text-muted-foreground">
                                Audio format from Google API. Linear16 provides best quality.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Output Sample Rate (Hz)</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.output_sample_rate_hz || 24000}
                                onChange={(e) => handleChange('output_sample_rate_hz', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Sample rate from Google. 24000 Hz is native for Gemini audio.
                            </p>
                        </div>
                        <div className="space-y-2">
                        <label className="text-sm font-medium">Target Encoding</label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.target_encoding || 'ulaw'}
                                onChange={(e) => handleChange('target_encoding', e.target.value)}
                            >
                                <option value="ulaw">μ-law</option>
                                <option value="pcm16">PCM16</option>
                                <option value="linear16">Linear16</option>
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
                                <option value="linear16">Linear16</option>
                                <option value="pcm16">PCM16</option>
                            </select>
                            <p className="text-xs text-muted-foreground">
                                Format sent to Google API. Linear16 is required by Gemini.
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
                                Sample rate for Google API input. 16000 Hz is optimal for Gemini STT.
                            </p>
                        </div>
                    </div>
                </div>

                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">Transcription & Modalities</h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Greeting</label>
                            <input
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.greeting || ''}
                                onChange={(e) => handleChange('greeting', e.target.value)}
                                placeholder="Hi! I'm powered by Google Gemini Live API."
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Response Modalities</label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.response_modalities || 'audio'}
                                onChange={(e) => handleChange('response_modalities', e.target.value)}
                            >
                                <option value="audio">Audio Only</option>
                                <option value="text">Text Only</option>
                                <option value="audio_text">Audio & Text</option>
                            </select>
                        </div>
                        <div className="flex items-center space-x-2">
                            <input
                                type="checkbox"
                                id="enable_input_transcription"
                                className="rounded border-input"
                                checked={config.enable_input_transcription ?? true}
                                onChange={(e) => handleChange('enable_input_transcription', e.target.checked)}
                            />
                            <label htmlFor="enable_input_transcription" className="text-sm font-medium">Enable Input Transcription</label>
                        </div>
                        <div className="flex items-center space-x-2">
                            <input
                                type="checkbox"
                                id="enable_output_transcription"
                                className="rounded border-input"
                                checked={config.enable_output_transcription ?? true}
                                onChange={(e) => handleChange('enable_output_transcription', e.target.checked)}
                            />
                            <label htmlFor="enable_output_transcription" className="text-sm font-medium">Enable Output Transcription</label>
                        </div>
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
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Input Gain Target RMS</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.input_gain_target_rms || 0}
                                onChange={(e) => handleChange('input_gain_target_rms', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">Optional normalization target for inbound audio.</p>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Input Gain Max dB</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.input_gain_max_db || 0}
                                onChange={(e) => handleChange('input_gain_max_db', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">Optional max gain applied during normalization.</p>
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
                    </div>
                </div>

                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">Hangup Fallback Tuning</h4>
                    <p className="text-xs text-muted-foreground">
                        Used when Google Live does not emit a reliable turn-complete event after a hangup farewell.
                    </p>
                    <div className="space-y-3 border border-amber-300/40 rounded-lg p-3 bg-amber-500/5">
                        <div className="flex items-center space-x-2">
                            <input
                                type="checkbox"
                                id="hangup_markers_enabled"
                                className="rounded border-input"
                                checked={config.hangup_markers_enabled ?? false}
                                onChange={(e) => handleChange('hangup_markers_enabled', e.target.checked)}
                            />
                            <label htmlFor="hangup_markers_enabled" className="text-sm font-medium">Enable Marker-Based Hangup Heuristics</label>
                        </div>
                        <p className="text-xs text-muted-foreground">
                            Advanced: uses transcript marker matching (end_call / assistant_farewell) to arm <code>cleanup_after_tts</code> when a toolCall is missing.
                            Recommended off for production; rely on <code>hangup_call</code> to end calls gracefully.
                        </p>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Audio Idle Timeout (sec)</label>
                            <input
                                type="number"
                                step="0.05"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.hangup_fallback_audio_idle_sec ?? 1.25}
                                onChange={(e) => handleChange('hangup_fallback_audio_idle_sec', e.target.value ? parseFloat(e.target.value) : null)}
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Minimum Armed Time (sec)</label>
                            <input
                                type="number"
                                step="0.05"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.hangup_fallback_min_armed_sec ?? 0.8}
                                onChange={(e) => handleChange('hangup_fallback_min_armed_sec', e.target.value ? parseFloat(e.target.value) : null)}
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">No Audio Timeout (sec)</label>
                            <input
                                type="number"
                                step="0.1"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.hangup_fallback_no_audio_timeout_sec ?? 4.0}
                                onChange={(e) => handleChange('hangup_fallback_no_audio_timeout_sec', e.target.value ? parseFloat(e.target.value) : null)}
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Turn Complete Timeout (sec)</label>
                            <input
                                type="number"
                                step="0.1"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.hangup_fallback_turn_complete_timeout_sec ?? 2.5}
                                onChange={(e) => handleChange('hangup_fallback_turn_complete_timeout_sec', e.target.value ? parseFloat(e.target.value) : null)}
                            />
                        </div>
                    </div>
                </div>

                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">Expert Settings</h4>
                    <div className="space-y-3 border border-amber-300/40 rounded-lg p-3 bg-amber-500/5">
                        <div className="flex items-center justify-between gap-3">
                            <div className="flex items-start gap-2">
                                <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5" />
                                <div>
                                    <div className="flex items-center gap-2">
                                        <span className="text-sm font-medium">WebSocket Keepalive (Advanced)</span>
                                        <HelpTooltip content="These settings control provider-level WebSocket keepalive behavior. Only change if you are troubleshooting disconnects. Some Google Live accounts/models may close the connection (1008) when keepalives are enabled." />
                                    </div>
                                    <p className="text-xs text-muted-foreground">
                                        Warning: enabling keepalive can materially change connection stability. Validate with real test calls before production.
                                    </p>
                                </div>
                            </div>
                            <input
                                type="checkbox"
                                className="rounded border-input"
                                checked={expertEnabled}
                                onChange={(e) => {
                                    setExpertEnabled(e.target.checked);
                                }}
                            />
                        </div>

                        <div className={`grid grid-cols-1 md:grid-cols-3 gap-4 ${expertEnabled ? '' : 'opacity-60 pointer-events-none'}`}>
                            <div className="space-y-2">
                                <label className="text-sm font-medium flex items-center gap-1">
                                    Keepalive Enabled
                                    <HelpTooltip content="Sends protocol-level WebSocket ping frames when the connection is idle. If disabled, the provider only relies on normal audio traffic to keep the session alive." />
                                </label>
                                <input
                                    type="checkbox"
                                    className="rounded border-input"
                                    checked={config.ws_keepalive_enabled ?? false}
                                    onChange={(e) => handleChange('ws_keepalive_enabled', e.target.checked)}
                                    disabled={!expertEnabled}
                                />
                                <p className="text-xs text-muted-foreground">
                                    Default: off. Turn on only if you see idle disconnects.
                                </p>
                            </div>

                            <div className="space-y-2">
                                <label className="text-sm font-medium flex items-center gap-1">
                                    Keepalive Interval (sec)
                                    <HelpTooltip content="How often to send ping frames (when idle). Lower values increase ping traffic; higher values reduce traffic but may not prevent idle timeouts." />
                                </label>
                                <input
                                    type="number"
                                    step="0.5"
                                    className="w-full p-2 rounded border border-input bg-background"
                                    value={config.ws_keepalive_interval_sec ?? 15.0}
                                    onChange={(e) => handleChange('ws_keepalive_interval_sec', e.target.value ? parseFloat(e.target.value) : null)}
                                    disabled={!expertEnabled}
                                />
                            </div>

                            <div className="space-y-2">
                                <label className="text-sm font-medium flex items-center gap-1">
                                    Idle Threshold (sec)
                                    <HelpTooltip content="Only send keepalive pings if we haven't sent any realtime audio to Google in the last N seconds. Prevents pinging while audio is actively flowing." />
                                </label>
                                <input
                                    type="number"
                                    step="0.5"
                                    className="w-full p-2 rounded border border-input bg-background"
                                    value={config.ws_keepalive_idle_sec ?? 5.0}
                                    onChange={(e) => handleChange('ws_keepalive_idle_sec', e.target.value ? parseFloat(e.target.value) : null)}
                                    disabled={!expertEnabled}
                                />
                            </div>
                        </div>
                    </div>
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
                        value={config.api_key || '${GOOGLE_API_KEY}'}
                        onChange={(e) => handleChange('api_key', e.target.value)}
                        placeholder="${GOOGLE_API_KEY}"
                    />
                </div>
            </div>
        </div>
    );
};

export default GoogleLiveProviderForm;
