import React from 'react';
import { ExternalLink, Info } from 'lucide-react';

interface AzureProviderFormProps {
    config: any;
    onChange: (newConfig: any) => void;
}

const AZURE_OUTPUT_FORMATS = [
    { value: 'riff-8khz-16bit-mono-pcm', label: 'RIFF 8 kHz 16-bit PCM (recommended for telephony)' },
    { value: 'riff-16khz-16bit-mono-pcm', label: 'RIFF 16 kHz 16-bit PCM' },
    { value: 'riff-24khz-16bit-mono-pcm', label: 'RIFF 24 kHz 16-bit PCM' },
    { value: 'raw-8khz-8bit-mono-mulaw', label: 'Raw 8 kHz 8-bit μ-law' },
    { value: 'raw-8khz-8bit-mono-alaw', label: 'Raw 8 kHz 8-bit A-law' },
    { value: 'raw-8khz-16bit-mono-pcm', label: 'Raw 8 kHz 16-bit PCM' },
    { value: 'raw-16khz-16bit-mono-pcm', label: 'Raw 16 kHz 16-bit PCM' },
    { value: 'audio-24khz-160kbitrate-mono-mp3', label: 'MP3 24 kHz 160 kbps' },
];

const AZURE_COMMON_REGIONS = [
    'eastus', 'eastus2', 'westus', 'westus2', 'westus3',
    'centralus', 'northcentralus', 'southcentralus',
    'northeurope', 'westeurope',
    'uksouth', 'francecentral', 'germanywestcentral',
    'eastasia', 'southeastasia', 'japaneast', 'koreacentral',
    'australiaeast', 'brazilsouth', 'canadacentral',
];

const AzureProviderForm: React.FC<AzureProviderFormProps> = ({ config, onChange }) => {
    const handleChange = (field: string, value: any) => {
        onChange({ ...config, [field]: value });
    };

    // Determine mode from capabilities
    const caps: string[] = Array.isArray(config.capabilities) ? config.capabilities : [];
    const isStt = caps.includes('stt');
    const isTts = caps.includes('tts');

    const variant = config.variant || 'realtime';

    return (
        <div className="space-y-6">

            {/* Info Banner */}
            <div className="bg-blue-50/50 dark:bg-blue-900/10 p-4 rounded-md border border-blue-100 dark:border-blue-900/20">
                <div className="flex items-start gap-3">
                    <Info className="w-5 h-5 text-blue-600 dark:text-blue-400 mt-0.5 flex-shrink-0" />
                    <div className="text-sm text-blue-800 dark:text-blue-300">
                        <p className="font-semibold mb-1">Microsoft Azure Speech Service</p>
                        <p className="text-blue-700 dark:text-blue-400">
                            Modular STT and TTS via Azure Cognitive Services REST API.
                            Configure your resource region and set <code className="bg-blue-100 dark:bg-blue-900/50 px-1 rounded">AZURE_SPEECH_KEY</code> in your environment.
                        </p>
                        <a
                            href="https://portal.azure.com/#create/Microsoft.CognitiveServicesSpeechServices"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 text-blue-600 dark:text-blue-400 hover:underline mt-1 text-xs"
                        >
                            <ExternalLink className="w-3 h-3" /> Create Azure Speech Resource
                        </a>
                    </div>
                </div>
            </div>

            {/* Authentication */}
            <div>
                <h4 className="font-semibold mb-3">Authentication</h4>
                <div className="bg-amber-50/30 dark:bg-amber-900/10 p-3 rounded-md border border-amber-200 dark:border-amber-900/30 mb-3">
                    <p className="text-sm text-amber-800 dark:text-amber-300">
                        <strong>⚠️ Required:</strong> Set <code className="bg-amber-100 dark:bg-amber-900/50 px-1 rounded">AZURE_SPEECH_KEY</code> in your <strong>.env file</strong>.
                        Never put your API key directly in YAML.
                    </p>
                </div>
            </div>

            {/* Region */}
            <div className="space-y-2">
                <label className="text-sm font-medium">
                    Azure Region <span className="text-destructive ml-1">*</span>
                </label>
                <div className="flex gap-2">
                    <select
                        className="flex-1 p-2 rounded border border-input bg-background"
                        value={AZURE_COMMON_REGIONS.includes(config.region || 'eastus') ? (config.region || 'eastus') : '__custom__'}
                        onChange={(e) => {
                            if (e.target.value !== '__custom__') {
                                handleChange('region', e.target.value);
                            }
                        }}
                    >
                        {AZURE_COMMON_REGIONS.map(r => (
                            <option key={r} value={r}>{r}</option>
                        ))}
                        <option value="__custom__">Custom...</option>
                    </select>
                    <input
                        type="text"
                        className="flex-1 p-2 rounded border border-input bg-background font-mono text-sm"
                        value={config.region || 'eastus'}
                        onChange={(e) => handleChange('region', e.target.value)}
                        placeholder="eastus"
                    />
                </div>
                <p className="text-xs text-muted-foreground">
                    The region your Azure Speech resource is deployed in. Both the dropdown and text field are synced — use whichever is convenient.
                </p>
            </div>

            {/* STT-specific settings */}
            {isStt && (
                <div>
                    <h4 className="font-semibold mb-3">Speech-to-Text Settings</h4>
                    <div className="space-y-4">

                        {/* STT Variant */}
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Recognition Variant</label>
                            <div className="flex gap-3">
                                <label className="flex items-center gap-2 border p-3 rounded-lg cursor-pointer hover:bg-accent has-[:checked]:bg-accent has-[:checked]:border-primary flex-1">
                                    <input
                                        type="radio"
                                        name="azure_stt_variant"
                                        value="realtime"
                                        checked={variant === 'realtime'}
                                        onChange={() => handleChange('variant', 'realtime')}
                                        className="w-4 h-4"
                                    />
                                    <div>
                                        <span className="block font-medium text-sm">Real-Time</span>
                                        <span className="block text-xs text-muted-foreground">Low-latency, one-shot REST API</span>
                                    </div>
                                </label>
                                <label className="flex items-center gap-2 border p-3 rounded-lg cursor-pointer hover:bg-accent has-[:checked]:bg-accent has-[:checked]:border-primary flex-1">
                                    <input
                                        type="radio"
                                        name="azure_stt_variant"
                                        value="fast"
                                        checked={variant === 'fast'}
                                        onChange={() => handleChange('variant', 'fast')}
                                        className="w-4 h-4"
                                    />
                                    <div>
                                        <span className="block font-medium text-sm">Fast Transcription</span>
                                        <span className="block text-xs text-muted-foreground">Higher accuracy, multipart upload</span>
                                    </div>
                                </label>
                            </div>
                            <p className="text-xs text-muted-foreground">
                                The <code>azure_stt</code> pipeline alias will route to <code>azure_stt_{variant}</code>.
                                You can also directly reference <code>azure_stt_fast</code> or <code>azure_stt_realtime</code> in pipelines.
                            </p>
                        </div>

                        {/* Language */}
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Recognition Language</label>
                            <input
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.language || 'en-US'}
                                onChange={(e) => handleChange('language', e.target.value)}
                                placeholder="en-US"
                            />
                            <p className="text-xs text-muted-foreground">
                                BCP-47 locale code (e.g., <code>en-US</code>, <code>es-ES</code>, <code>fr-FR</code>).{' '}
                                <a
                                    href="https://learn.microsoft.com/azure/ai-services/speech-service/language-support?tabs=stt"
                                    target="_blank" rel="noopener noreferrer"
                                    className="text-primary underline"
                                >
                                    Supported languages
                                </a>
                            </p>
                        </div>

                        {/* Advanced: Custom endpoints */}
                        <details className="border border-border rounded-md">
                            <summary className="p-3 text-sm font-medium cursor-pointer hover:bg-accent">Advanced: Custom Endpoints</summary>
                            <div className="p-3 space-y-3 border-t border-border">
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Real-Time STT Endpoint URL (optional)</label>
                                    <input
                                        type="text"
                                        className="w-full p-2 rounded border border-input bg-background font-mono text-sm"
                                        value={config.realtime_stt_base_url || ''}
                                        onChange={(e) => handleChange('realtime_stt_base_url', e.target.value || null)}
                                        placeholder={`https://${config.region || 'eastus'}.stt.speech.microsoft.com/...`}
                                    />
                                    <p className="text-xs text-muted-foreground">Leave empty to auto-generate from region.</p>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Fast Transcription Endpoint URL (optional)</label>
                                    <input
                                        type="text"
                                        className="w-full p-2 rounded border border-input bg-background font-mono text-sm"
                                        value={config.fast_stt_base_url || ''}
                                        onChange={(e) => handleChange('fast_stt_base_url', e.target.value || null)}
                                        placeholder={`https://${config.region || 'eastus'}.api.cognitive.microsoft.com/...`}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Fast Transcription API Version (optional)</label>
                                    <input
                                        type="text"
                                        className="w-full p-2 rounded border border-input bg-background font-mono text-sm"
                                        value={config.api_version || ''}
                                        onChange={(e) => handleChange('api_version', e.target.value || null)}
                                        placeholder="2024-11-15"
                                    />
                                    <p className="text-xs text-muted-foreground">The API version for the fast transcription endpoint.</p>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Real-Time VAD End Silence Timeout (ms)</label>
                                    <input
                                        type="number"
                                        min="50"
                                        step="50"
                                        className="w-full p-2 rounded border border-input bg-background"
                                        value={config.vad_silence_timeout_ms ?? 300}
                                        onChange={(e) => handleChange('vad_silence_timeout_ms', parseInt(e.target.value))}
                                    />
                                    <p className="text-xs text-muted-foreground">Azure SDK end silence timeout. Default is 300ms since Asterisk handles primary TalkDetect.</p>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Real-Time VAD Initial Silence Timeout (ms)</label>
                                    <input
                                        type="number"
                                        min="1000"
                                        step="500"
                                        className="w-full p-2 rounded border border-input bg-background"
                                        value={config.vad_initial_silence_timeout_ms ?? 5000}
                                        onChange={(e) => handleChange('vad_initial_silence_timeout_ms', parseInt(e.target.value))}
                                    />
                                    <p className="text-xs text-muted-foreground">How long Azure waits for speech to begin before stopping. Default is 5000ms.</p>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Request Timeout (seconds)</label>
                                    <input
                                        type="number"
                                        step="0.5"
                                        min="1"
                                        className="w-full p-2 rounded border border-input bg-background"
                                        value={config.request_timeout_sec ?? 15.0}
                                        onChange={(e) => handleChange('request_timeout_sec', parseFloat(e.target.value))}
                                    />
                                </div>
                            </div>
                        </details>
                    </div>
                </div>
            )}

            {/* TTS-specific settings */}
            {isTts && (
                <div>
                    <h4 className="font-semibold mb-3">Text-to-Speech Settings</h4>
                    <div className="space-y-4">

                        {/* Voice Name */}
                        <div className="space-y-2">
                            <label className="text-sm font-medium">
                                Neural Voice Name <span className="text-destructive ml-1">*</span>
                            </label>
                            <input
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.voice_name || 'en-US-JennyNeural'}
                                onChange={(e) => handleChange('voice_name', e.target.value)}
                                placeholder="en-US-JennyNeural"
                            />
                            <p className="text-xs text-muted-foreground">
                                Full Azure neural voice name.{' '}
                                <a
                                    href="https://learn.microsoft.com/azure/ai-services/speech-service/language-support?tabs=tts"
                                    target="_blank" rel="noopener noreferrer"
                                    className="text-primary underline"
                                >
                                    Browse voice gallery
                                </a>
                                {' '}— e.g., <code>es-ES-AlvaroNeural</code>, <code>fr-FR-DeniseNeural</code>.
                            </p>
                        </div>

                        {/* Base language (xml:lang on <speak>) */}
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Base Language <span className="text-muted-foreground text-xs font-normal">(optional)</span></label>
                            <input
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.language || ''}
                                onChange={(e) => handleChange('language', e.target.value || null)}
                                placeholder="Auto (derived from voice name, e.g. en-US)"
                            />
                            <p className="text-xs text-muted-foreground">
                                BCP-47 locale for the SSML <code>xml:lang</code> attribute on <code>&lt;speak&gt;</code>.
                                Leave empty to auto-derive from the voice name (e.g. <code>zh-CN-XiaochenMultilingualNeural</code> → <code>zh-CN</code>).
                            </p>
                        </div>

                        {/* Multilingual lang tag (<lang xml:lang="...">) */}
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Multilingual Target Language <span className="text-muted-foreground text-xs font-normal">(optional)</span></label>
                            <input
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.lang_tag || ''}
                                onChange={(e) => handleChange('lang_tag', e.target.value || null)}
                                placeholder="e.g. es-MX, en-US, fr-FR"
                            />
                            <p className="text-xs text-muted-foreground">
                                For multilingual neural voices. When set, the text is wrapped in{' '}
                                <code>&lt;lang xml:lang="…"&gt;</code> inside the SSML, telling Azure which language to speak —
                                even if the voice's native locale is different.
                                Example: voice <code>zh-CN-XiaochenMultilingualNeural</code> + lang tag <code>es-MX</code>{' '}
                                will speak Spanish with a multilingual Chinese voice.{' '}
                                <a
                                    href="https://learn.microsoft.com/azure/ai-services/speech-service/speech-synthesis-markup-voice#multilingual-voices"
                                    target="_blank" rel="noopener noreferrer"
                                    className="text-primary underline"
                                >
                                    Learn more
                                </a>
                            </p>
                        </div>

                        {/* Output Format */}
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Output Audio Format</label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.output_format || 'raw-8khz-16bit-mono-pcm'}
                                onChange={(e) => handleChange('output_format', e.target.value)}
                            >
                                {AZURE_OUTPUT_FORMATS.map(f => (
                                    <option key={f.value} value={f.value}>{f.label}</option>
                                ))}
                            </select>
                            <p className="text-xs text-muted-foreground">
                                This is the format Azure returns. The engine will decode and resample to the target encoding below.
                            </p>
                        </div>

                        {/* Target Encoding + Sample Rate */}
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Target Encoding</label>
                                <select
                                    className="w-full p-2 rounded border border-input bg-background"
                                    value={config.target_encoding || 'mulaw'}
                                    onChange={(e) => handleChange('target_encoding', e.target.value)}
                                >
                                    <option value="mulaw">μ-law (mulaw) — telephony standard</option>
                                    <option value="pcm">PCM 16-bit LE</option>
                                    <option value="slin16">SLIN 16 kHz</option>
                                </select>
                                <p className="text-xs text-muted-foreground">Encoding the Asterisk channel expects.</p>
                            </div>
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Target Sample Rate (Hz)</label>
                                <select
                                    className="w-full p-2 rounded border border-input bg-background"
                                    value={config.target_sample_rate_hz ?? 8000}
                                    onChange={(e) => handleChange('target_sample_rate_hz', parseInt(e.target.value))}
                                >
                                    <option value={8000}>8000 Hz (telephony)</option>
                                    <option value={16000}>16000 Hz</option>
                                    <option value={22050}>22050 Hz</option>
                                    <option value={24000}>24000 Hz</option>
                                </select>
                            </div>
                        </div>

                        {/* Streaming */}
                        <div className="flex items-start gap-3 p-3 border rounded-md">
                            <input
                                type="checkbox"
                                id="azure_tts_streaming"
                                className="rounded border-input mt-0.5"
                                checked={config.streaming !== false}
                                onChange={(e) => handleChange('streaming', e.target.checked)}
                            />
                            <div>
                                <label htmlFor="azure_tts_streaming" className="text-sm font-medium cursor-pointer">
                                    Streaming (chunked response)
                                </label>
                                <p className="text-xs text-muted-foreground mt-0.5">
                                    Yield audio chunks as they arrive instead of waiting for the full synthesis.
                                    Significantly reduces time-to-first-audio.{' '}
                                    <a
                                        href="https://learn.microsoft.com/azure/ai-services/speech-service/how-to-lower-speech-synthesis-latency"
                                        target="_blank" rel="noopener noreferrer"
                                        className="text-primary underline"
                                    >
                                        Learn more
                                    </a>
                                </p>
                            </div>
                        </div>

                        {/* Prosody (SSML) */}
                        <details className="border border-border rounded-md">
                            <summary className="p-3 text-sm font-medium cursor-pointer hover:bg-accent">Voice Prosody (pitch &amp; rate)</summary>
                            <div className="p-3 space-y-3 border-t border-border">
                                <p className="text-xs text-muted-foreground">
                                    Controls injected into the SSML <code>&lt;prosody&gt;</code> tag.
                                    Leave blank to use the voice's default.
                                    Use <a href="https://learn.microsoft.com/azure/ai-services/speech-service/speech-synthesis-markup-voice#adjust-prosody" target="_blank" rel="noopener noreferrer" className="text-primary underline">Azure SSML prosody syntax</a>.
                                </p>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <div className="space-y-2">
                                        <label className="text-sm font-medium">Pitch</label>
                                        <select
                                            className="w-full p-2 rounded border border-input bg-background"
                                            value={['x-low', 'low', 'medium', 'high', 'x-high', 'default'].includes(config.prosody_pitch || '') ? (config.prosody_pitch || '') : '__custom__'}
                                            onChange={(e) => {
                                                if (e.target.value !== '__custom__') handleChange('prosody_pitch', e.target.value || null);
                                            }}
                                        >
                                            <option value="">Default (voice default)</option>
                                            <option value="x-low">x-low</option>
                                            <option value="low">low</option>
                                            <option value="medium">medium</option>
                                            <option value="high">high</option>
                                            <option value="x-high">x-high</option>
                                            <option value="__custom__">Custom value...</option>
                                        </select>
                                        <input
                                            type="text"
                                            className="w-full p-2 rounded border border-input bg-background font-mono text-sm"
                                            value={config.prosody_pitch || ''}
                                            onChange={(e) => handleChange('prosody_pitch', e.target.value || null)}
                                            placeholder="e.g. +10%, -5%, high"
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <label className="text-sm font-medium">Speaking Rate</label>
                                        <select
                                            className="w-full p-2 rounded border border-input bg-background"
                                            value={['x-slow', 'slow', 'medium', 'fast', 'x-fast', 'default'].includes(config.prosody_rate || '') ? (config.prosody_rate || '') : '__custom__'}
                                            onChange={(e) => {
                                                if (e.target.value !== '__custom__') handleChange('prosody_rate', e.target.value || null);
                                            }}
                                        >
                                            <option value="">Default (voice default)</option>
                                            <option value="x-slow">x-slow</option>
                                            <option value="slow">slow</option>
                                            <option value="medium">medium</option>
                                            <option value="fast">fast</option>
                                            <option value="x-fast">x-fast</option>
                                            <option value="__custom__">Custom value...</option>
                                        </select>
                                        <input
                                            type="text"
                                            className="w-full p-2 rounded border border-input bg-background font-mono text-sm"
                                            value={config.prosody_rate || ''}
                                            onChange={(e) => handleChange('prosody_rate', e.target.value || null)}
                                            placeholder="e.g. +20%, 0.8, slow"
                                        />
                                    </div>
                                </div>
                            </div>
                        </details>

                        {/* Advanced: Chunk + Timeout + Custom URL */}
                        <details className="border border-border rounded-md">
                            <summary className="p-3 text-sm font-medium cursor-pointer hover:bg-accent">Advanced: Streaming &amp; Timeouts</summary>
                            <div className="p-3 space-y-3 border-t border-border">
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <div className="space-y-2">
                                        <label className="text-sm font-medium">Chunk Size (ms)</label>
                                        <input
                                            type="number"
                                            min="10"
                                            max="200"
                                            className="w-full p-2 rounded border border-input bg-background"
                                            value={config.chunk_size_ms ?? 20}
                                            onChange={(e) => handleChange('chunk_size_ms', parseInt(e.target.value))}
                                        />
                                        <p className="text-xs text-muted-foreground">
                                            Audio chunk size yielded per frame. 20ms is the standard for telephony.
                                        </p>
                                    </div>
                                    <div className="space-y-2">
                                        <label className="text-sm font-medium">Request Timeout (seconds)</label>
                                        <input
                                            type="number"
                                            step="0.5"
                                            min="1"
                                            className="w-full p-2 rounded border border-input bg-background"
                                            value={config.request_timeout_sec ?? 15.0}
                                            onChange={(e) => handleChange('request_timeout_sec', parseFloat(e.target.value))}
                                        />
                                    </div>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Custom TTS Endpoint URL (optional)</label>
                                    <input
                                        type="text"
                                        className="w-full p-2 rounded border border-input bg-background font-mono text-sm"
                                        value={config.tts_base_url || ''}
                                        onChange={(e) => handleChange('tts_base_url', e.target.value || null)}
                                        placeholder={`https://${config.region || 'eastus'}.tts.speech.microsoft.com/cognitiveservices/v1`}
                                    />
                                    <p className="text-xs text-muted-foreground">Leave empty to auto-generate from region.</p>
                                </div>
                            </div>
                        </details>
                    </div>
                </div>
            )}

            {/* Enabled toggle */}
            <div className="flex items-center space-x-2 pt-2">
                <input
                    type="checkbox"
                    id="azure_enabled"
                    className="rounded border-input"
                    checked={config.enabled ?? true}
                    onChange={(e) => handleChange('enabled', e.target.checked)}
                />
                <label htmlFor="azure_enabled" className="text-sm font-medium">Enabled</label>
            </div>
        </div>
    );
};

export default AzureProviderForm;
