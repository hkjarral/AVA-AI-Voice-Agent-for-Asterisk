import React from 'react';

interface OpenAIProviderFormProps {
    config: any;
    onChange: (newConfig: any) => void;
}

const OpenAIProviderForm: React.FC<OpenAIProviderFormProps> = ({ config, onChange }) => {
    const handleChange = (field: string, value: any) => {
        onChange({ ...config, [field]: value });
    };

    const name = (config?.name || '').toLowerCase();
    const isSTT = name.includes('stt');
    const isTTS = name.includes('tts');
    const isLLM = name.includes('llm') || (!isSTT && !isTTS);

    return (
        <div className="space-y-6">
            <div className="space-y-4">
                <h4 className="font-semibold text-sm border-b pb-2">Authentication</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">API Key (env or literal)</label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.api_key || '${OPENAI_API_KEY}'}
                            onChange={(e) => handleChange('api_key', e.target.value)}
                            placeholder="${OPENAI_API_KEY}"
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Organization (optional)</label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.organization || ''}
                            onChange={(e) => handleChange('organization', e.target.value)}
                            placeholder="org_123..."
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Project (optional)</label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.project || ''}
                            onChange={(e) => handleChange('project', e.target.value)}
                            placeholder="proj_123..."
                        />
                    </div>
                </div>
            </div>

            {isLLM && (
                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">LLM (Chat Completions)</h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">
                                Chat API Base URL
                                <span className="text-xs text-muted-foreground ml-2">(chat_base_url)</span>
                            </label>
                            <input
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.chat_base_url || 'https://api.openai.com/v1'}
                                onChange={(e) => handleChange('chat_base_url', e.target.value)}
                                placeholder="https://api.openai.com/v1"
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Chat Model</label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.chat_model || 'gpt-4o-mini'}
                                onChange={(e) => handleChange('chat_model', e.target.value)}
                            >
                                <optgroup label="GPT-4o (Latest)">
                                    <option value="gpt-4o">gpt-4o</option>
                                    <option value="gpt-4o-mini">gpt-4o-mini</option>
                                    <option value="gpt-4o-mini-tts">gpt-4o-mini-tts</option>
                                </optgroup>
                                <optgroup label="GPT-4">
                                    <option value="gpt-4">gpt-4</option>
                                    <option value="gpt-4o-2024-08-06">gpt-4o-2024-08-06</option>
                                </optgroup>
                            </select>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Default Modalities</label>
                            <select
                                multiple
                                className="w-full p-2 rounded border border-input bg-background h-24"
                                value={config.default_modalities || ['text']}
                                onChange={(e) => handleChange('default_modalities', Array.from(e.target.selectedOptions, option => option.value))}
                            >
                                <option value="text">Text</option>
                                <option value="audio">Audio</option>
                            </select>
                            <p className="text-xs text-muted-foreground">Hold Ctrl/Cmd to select multiple.</p>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Response Timeout (sec)</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.response_timeout_sec || 5}
                                onChange={(e) => handleChange('response_timeout_sec', parseFloat(e.target.value))}
                            />
                        </div>
                    </div>
                </div>
            )}

            {isSTT && (
                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">STT (audio.transcriptions)</h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">
                                STT API Base URL
                                <span className="text-xs text-muted-foreground ml-2">(stt_base_url)</span>
                            </label>
                            <input
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.stt_base_url || 'https://api.openai.com/v1/audio/transcriptions'}
                                onChange={(e) => handleChange('stt_base_url', e.target.value)}
                                placeholder="https://api.openai.com/v1/audio/transcriptions"
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">STT Model</label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.stt_model || 'whisper-1'}
                                onChange={(e) => handleChange('stt_model', e.target.value)}
                            >
                                <option value="whisper-1">whisper-1 (default)</option>
                                <option value="gpt-4o-mini-transcribe">gpt-4o-mini-transcribe</option>
                                <option value="gpt-4o-mini-transcribe-2025-12-15">gpt-4o-mini-transcribe-2025-12-15</option>
                                <option value="gpt-4o-transcribe">gpt-4o-transcribe</option>
                                <option value="gpt-4o-transcribe-diarize">gpt-4o-transcribe-diarize</option>
                            </select>
                            <p className="text-xs text-muted-foreground">
                                Note: <code>whisper-1</code> supports more <code>response_format</code> options than the GPT-4o transcribe models.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Input Encoding</label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.input_encoding || 'linear16'}
                                onChange={(e) => handleChange('input_encoding', e.target.value)}
                            >
                                <option value="linear16">Linear16</option>
                                <option value="pcm16">PCM16</option>
                                <option value="ulaw">μ-law</option>
                            </select>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Input Sample Rate (Hz)</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.input_sample_rate_hz || 16000}
                                onChange={(e) => handleChange('input_sample_rate_hz', parseInt(e.target.value))}
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Chunk Size (ms)</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.chunk_size_ms || 20}
                                onChange={(e) => handleChange('chunk_size_ms', parseInt(e.target.value))}
                            />
                        </div>
                    </div>
                </div>
            )}

            {isTTS && (
                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">TTS (audio.speech)</h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">
                                TTS API Base URL
                                <span className="text-xs text-muted-foreground ml-2">(tts_base_url)</span>
                            </label>
                            <input
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.tts_base_url || 'https://api.openai.com/v1/audio/speech'}
                                onChange={(e) => handleChange('tts_base_url', e.target.value)}
                                placeholder="https://api.openai.com/v1/audio/speech"
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">TTS Model</label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.tts_model || 'tts-1'}
                                onChange={(e) => handleChange('tts_model', e.target.value)}
                            >
                                <option value="tts-1">tts-1</option>
                                <option value="tts-1-hd">tts-1-hd</option>
                                <option value="gpt-4o-mini-tts">gpt-4o-mini-tts</option>
                                <option value="gpt-4o-mini-tts-2025-12-15">gpt-4o-mini-tts-2025-12-15</option>
                            </select>
                            <p className="text-xs text-muted-foreground">
                                If you see “invalid model ID”, switch to <code>tts-1</code>.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Voice</label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.voice || 'alloy'}
                                onChange={(e) => handleChange('voice', e.target.value)}
                            >
                                <option value="alloy">Alloy</option>
                                <option value="ash">Ash</option>
                                <option value="ballad">Ballad</option>
                                <option value="coral">Coral</option>
                                <option value="echo">Echo</option>
                                <option value="fable">Fable</option>
                                <option value="onyx">Onyx</option>
                                <option value="nova">Nova</option>
                                <option value="sage">Sage</option>
                                <option value="shimmer">Shimmer</option>
                                <option value="verse">Verse</option>
                                <option value="marin">Marin</option>
                                <option value="cedar">Cedar</option>
                            </select>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Target Encoding</label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.target_encoding || 'mulaw'}
                                onChange={(e) => handleChange('target_encoding', e.target.value)}
                            >
                                <option value="mulaw">μ-law</option>
                                <option value="ulaw">ulaw</option>
                                <option value="pcm16">PCM16</option>
                                <option value="linear16">Linear16</option>
                            </select>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Target Sample Rate (Hz)</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.target_sample_rate_hz || 8000}
                                onChange={(e) => handleChange('target_sample_rate_hz', parseInt(e.target.value))}
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Chunk Size (ms)</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.chunk_size_ms || 20}
                                onChange={(e) => handleChange('chunk_size_ms', parseInt(e.target.value))}
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Response Timeout (sec)</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.response_timeout_sec || 5}
                                onChange={(e) => handleChange('response_timeout_sec', parseFloat(e.target.value))}
                            />
                        </div>
                    </div>
                </div>
            )}

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
        </div>
    );
};

export default OpenAIProviderForm;
