import { modelOptions, modelIds, providerDefault, voiceIdsForModel } from '../../../config/providerCatalog';
import React from 'react';
import HelpTooltip from '../../ui/HelpTooltip';
import OutputResamplerField from './OutputResamplerField';

interface OpenAIProviderFormProps {
    config: any;
    onChange: (newConfig: any) => void;
    /** Unused here; accepted for prop-shape parity with full-agent forms. */
    providerKey?: string;
}

const OpenAIProviderForm: React.FC<OpenAIProviderFormProps> = ({ config, onChange }) => {
    const handleChange = (field: string, value: any) => {
        onChange({ ...config, [field]: value });
    };

    const ttsModel = config.tts_model ?? providerDefault('openai', 'tts_model');
    const voices = voiceIdsForModel('openai', ttsModel);
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
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">API Key (env or literal)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>OpenAI API Key</strong> — credential for platform.openai.com.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>Recommended: env var reference like <code>${'${OPENAI_API_KEY}'}</code></li>
                                            <li>Generate at platform.openai.com/api-keys</li>
                                            <li>Avoid committing literal keys to YAML</li>
                                        </ul>
                                    </>
                                }
                                link="https://platform.openai.com/api-keys"
                                linkText="Get API key"
                            />
                        </div>
                        <input
                            type="password"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.api_key || '${OPENAI_API_KEY}'}
                            onChange={(e) => handleChange('api_key', e.target.value)}
                            placeholder="${OPENAI_API_KEY}"
                        />
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Organization (optional)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Organization ID</strong> — sent as the <code>OpenAI-Organization</code> header to route usage/billing.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>Format: <code>org_…</code></li>
                                            <li>Only needed if your key belongs to multiple orgs</li>
                                        </ul>
                                    </>
                                }
                                link="https://platform.openai.com/docs/api-reference/authentication"
                                linkText="API auth docs"
                            />
                        </div>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.organization || ''}
                            onChange={(e) => handleChange('organization', e.target.value)}
                            placeholder="org_123..."
                        />
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Project (optional)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Project ID</strong> — sent as the <code>OpenAI-Project</code> header for per-project usage tracking.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>Format: <code>proj_…</code></li>
                                            <li>Required for project-scoped API keys</li>
                                        </ul>
                                    </>
                                }
                                link="https://platform.openai.com/docs/api-reference/authentication"
                                linkText="Projects docs"
                            />
                        </div>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.project || ''}
                            onChange={(e) => handleChange('project', e.target.value)}
                            placeholder="proj_123..."
                        />
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Realtime API Version</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Realtime API Version</strong> — controls whether the engine sends the <code>OpenAI-Beta</code> header on Realtime calls.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><strong>Beta</strong> (default): broadest compatibility</li>
                                            <li><strong>GA</strong>: drops the beta header; may require additional account verification</li>
                                        </ul>
                                        Mostly cosmetic for modular STT/LLM/TTS slots — only the Realtime full-agent honors this.
                                    </>
                                }
                                link="https://platform.openai.com/docs/guides/realtime"
                                linkText="Realtime API"
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.api_version || 'ga'}
                            onChange={(e) => handleChange('api_version', e.target.value)}
                        >
                            <option value="ga">GA (default)</option>
                            <option value="beta">Beta (sunset 2026-05-12 — will not connect)</option>
                        </select>
                        <p className="text-xs text-muted-foreground">
                            <strong>GA</strong> is the only supported value as of 2026-05-12. The <code>beta</code> option is retained so operators with legacy YAML overrides see their pinned state — OpenAI rejects beta sessions with <code>beta_api_shape_disabled</code>.
                        </p>
                    </div>
                </div>
            </div>

            {isLLM && (
                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">LLM (Chat Completions)</h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">
                                    Chat API Base URL
                                    <span className="text-xs text-muted-foreground ml-2">(chat_base_url)</span>
                                </label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Chat API Base URL</strong> — root URL for the Chat Completions endpoint.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>Default: <code>https://api.openai.com/v1</code></li>
                                                <li>Override for Azure OpenAI, OpenRouter, LM Studio, or local proxies</li>
                                                <li>Must speak the OpenAI Chat Completions wire format</li>
                                            </ul>
                                        </>
                                    }
                                    link="https://platform.openai.com/docs/api-reference/chat"
                                    linkText="Chat API docs"
                                />
                            </div>
                            <input
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.chat_base_url || 'https://api.openai.com/v1'}
                                onChange={(e) => handleChange('chat_base_url', e.target.value)}
                                placeholder="https://api.openai.com/v1"
                            />
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Chat Model</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Chat Model</strong> — which OpenAI model generates responses.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><code>gpt-6-luna</code> — current default; tool calling uses reasoning effort none</li>
                                                <li><code>gpt-4o</code> — higher quality, more expensive</li>
                                                <li>Explicit compatible gateway model names remain preserved.</li>
                                            </ul>
                                            Standard pay-per-token pricing.
                                        </>
                                    }
                                    link="https://platform.openai.com/docs/models"
                                    linkText="Model list & pricing"
                                />
                            </div>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.chat_model || providerDefault('openai', 'chat_model')}
                                onChange={(e) => handleChange('chat_model', e.target.value)}
                            >
                                {config.chat_model && !modelIds('openai', 'llm').includes(config.chat_model) && <option value={config.chat_model}>{config.chat_model} (configured)</option>}
                                {modelOptions('openai', 'llm').map(model => <option key={model.value} value={model.value}>{model.label}</option>)}
                            </select>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Default Modalities</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Default Modalities</strong> — what kind of output the model is asked to produce.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><code>text</code> — standard chat output; default for modular LLM slot</li>
                                                <li><code>audio</code> — only meaningful on audio-capable models (the <code>gpt-realtime</code> family)</li>
                                            </ul>
                                            For a modular LLM slot (text-only, no audio), leave as <code>text</code>.
                                        </>
                                    }
                                    link="https://platform.openai.com/docs/api-reference/chat/create#chat-create-modalities"
                                    linkText="Modalities param"
                                />
                            </div>
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
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Response Timeout (sec)</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Response Timeout</strong> — max wait for the LLM to return a complete response.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>Default <code>5s</code></li>
                                                <li>Raise for long-context prompts or slower models like <code>gpt-4</code></li>
                                                <li>Streamed tokens don't count against the per-token deadline; this is whole-response budget</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.response_timeout_sec || 5}
                                onChange={(e) => handleChange('response_timeout_sec', parseFloat(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Max wait time for LLM response. Increase for complex prompts.
                            </p>
                        </div>
                    </div>
                </div>
            )}

            {isSTT && (
                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">STT (audio.transcriptions)</h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">
                                    STT API Base URL
                                    <span className="text-xs text-muted-foreground ml-2">(stt_base_url)</span>
                                </label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>STT API Base URL</strong> — full endpoint for the audio.transcriptions API.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>Default: <code>https://api.openai.com/v1/audio/transcriptions</code></li>
                                                <li>Override for Azure OpenAI Whisper deployment, on-prem Whisper, or proxies</li>
                                            </ul>
                                        </>
                                    }
                                    link="https://platform.openai.com/docs/api-reference/audio/createTranscription"
                                    linkText="Transcription API"
                                />
                            </div>
                            <input
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.stt_base_url || 'https://api.openai.com/v1/audio/transcriptions'}
                                onChange={(e) => handleChange('stt_base_url', e.target.value)}
                                placeholder="https://api.openai.com/v1/audio/transcriptions"
                            />
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">STT Model</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>STT Model</strong> — which transcription model to call.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><code>whisper-1</code> — classic Whisper, broadest <code>response_format</code> support</li>
                                                <li><code>gpt-4o-mini-transcribe</code> / <code>gpt-4o-transcribe</code> — newer, often more accurate but fewer format options</li>
                                            </ul>
                                            If a <code>response_format</code> error appears, fall back to <code>whisper-1</code>.
                                        </>
                                    }
                                    link="https://platform.openai.com/docs/guides/speech-to-text"
                                    linkText="STT guide"
                                />
                            </div>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.stt_model || providerDefault('openai', 'stt_model')}
                                onChange={(e) => handleChange('stt_model', e.target.value)}
                            >
                                {config.stt_model && !modelIds('openai', 'stt').includes(config.stt_model) && <option value={config.stt_model}>{config.stt_model} (configured)</option>}
                                {modelOptions('openai', 'stt').map(model => <option key={model.value} value={model.value}>{model.label}</option>)}
                            </select>
                            <p className="text-xs text-muted-foreground">
                                Note: <code>whisper-1</code> supports more <code>response_format</code> options than the GPT-4o transcribe models.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Input Encoding</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Input Encoding</strong> — codec of the audio Asterisk forwards to the engine for transcription.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><code>linear16</code> — recommended for Whisper (16-bit PCM)</li>
                                                <li><code>pcm16</code> — same family, alternate label</li>
                                                <li><code>ulaw</code> — direct from telephony, engine resamples</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.input_encoding || 'linear16'}
                                onChange={(e) => handleChange('input_encoding', e.target.value)}
                            >
                                <option value="linear16">Linear16</option>
                                <option value="pcm16">PCM16</option>
                                <option value="ulaw">μ-law</option>
                            </select>
                            <p className="text-xs text-muted-foreground">
                                Audio format for STT. Linear16 recommended for Whisper.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Input Sample Rate (Hz)</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Input Sample Rate</strong> — sample rate of the audio sent for transcription.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><code>16000 Hz</code> — optimal for Whisper</li>
                                                <li><code>8000 Hz</code> — raw telephony; engine usually upsamples</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.input_sample_rate_hz || 16000}
                                onChange={(e) => handleChange('input_sample_rate_hz', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Sample rate for STT. 16000 Hz optimal for Whisper models.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Chunk Size (ms)</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Chunk Size</strong> — duration of each audio frame fed into the STT buffer.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>Default <code>20ms</code> — standard SIP/RTP frame</li>
                                                <li>Smaller = lower latency, more overhead</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.chunk_size_ms || 20}
                                onChange={(e) => handleChange('chunk_size_ms', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Audio chunk duration. 20ms is standard for real-time.
                            </p>
                        </div>
                    </div>
                </div>
            )}

            {isTTS && (
                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">TTS</h4>
                    <p className="text-xs text-muted-foreground">Speech API models retire January 6, 2027. Realtime models use a separate WebSocket workflow; switching requires real-call acceptance.</p>
                    <label htmlFor="openai-tts-transport" className="text-sm font-medium">TTS API Workflow</label>
                    <select id="openai-tts-transport" value={config.tts_transport ?? providerDefault('openai', 'tts_transport')} onChange={event => handleChange('tts_transport', event.target.value)} className="w-full p-2 rounded border border-input bg-background">
                        <option value="auto">Auto (by model)</option><option value="speech">Speech API</option><option value="realtime">Realtime API</option>
                    </select>
                    <label htmlFor="openai-tts-realtime-url" className="text-sm font-medium">Realtime WebSocket URL</label>
                    <input id="openai-tts-realtime-url" value={config.tts_realtime_base_url ?? 'wss://api.openai.com/v1/realtime'} onChange={event => handleChange('tts_realtime_base_url', event.target.value)} className="w-full p-2 rounded border border-input bg-background" />
                    <label htmlFor="openai-tts-realtime-timeout" className="text-sm font-medium">Realtime Synthesis Timeout (sec)</label>
                    <input id="openai-tts-realtime-timeout" type="number" min={1} max={120} value={config.tts_realtime_timeout_sec ?? providerDefault('openai', 'tts_realtime_timeout_sec')} onChange={event => handleChange('tts_realtime_timeout_sec', Number(event.target.value))} className="w-full p-2 rounded border border-input bg-background" />
                    <p className="text-xs text-muted-foreground">Total generation budget for each text fragment. Separate from the legacy Speech API response timeout.</p>
                    <label htmlFor="openai-tts-instructions" className="text-sm font-medium">Speech Instructions</label>
                    <input id="openai-tts-instructions" value={config.tts_instructions ?? ''} onChange={event => handleChange('tts_instructions', event.target.value)} className="w-full p-2 rounded border border-input bg-background" />
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">
                                    TTS API Base URL
                                    <span className="text-xs text-muted-foreground ml-2">(tts_base_url)</span>
                                </label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>TTS API Base URL</strong> — full endpoint for the audio.speech API.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>Default: <code>https://api.openai.com/v1/audio/speech</code></li>
                                                <li>Override for Azure OpenAI TTS deployments or proxies</li>
                                            </ul>
                                        </>
                                    }
                                    link="https://platform.openai.com/docs/api-reference/audio/createSpeech"
                                    linkText="Speech API"
                                />
                            </div>
                            <input
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.tts_base_url || 'https://api.openai.com/v1/audio/speech'}
                                onChange={(e) => handleChange('tts_base_url', e.target.value)}
                                placeholder="https://api.openai.com/v1/audio/speech"
                            />
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">TTS Model</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>TTS Model</strong> — which OpenAI speech model synthesizes audio.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><code>tts-1</code> — fastest, lowest cost, standard fidelity</li>
                                                <li><code>tts-1-hd</code> — higher fidelity, slower</li>
                                                <li><code>gpt-4o-mini-tts</code> — newer; accepts instructions for tone/style</li>
                                            </ul>
                                            The selected model must match the Speech or Realtime API workflow.
                                        </>
                                    }
                                    link="https://platform.openai.com/docs/guides/text-to-speech"
                                    linkText="TTS guide"
                                />
                            </div>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.tts_model || providerDefault('openai', 'tts_model')}
                                onChange={(e) => handleChange('tts_model', e.target.value)}
                            >
                                {config.tts_model && !modelIds('openai', 'tts').includes(config.tts_model) && <option value={config.tts_model}>{config.tts_model} (configured)</option>}
                                {modelOptions('openai', 'tts').map(model => <option key={model.value} value={model.value}>{model.label}</option>)}
                            </select>
                            <p className="text-xs text-muted-foreground">
                                Legacy Speech models remain selectable until their announced retirement.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Voice</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Voice</strong> — which preset voice OpenAI synthesizes.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>Classic: <code>alloy</code>, <code>echo</code>, <code>fable</code>, <code>onyx</code>, <code>nova</code>, <code>shimmer</code></li>
                                                <li>Newer (gpt-4o-mini-tts): <code>ash</code>, <code>ballad</code>, <code>coral</code>, <code>sage</code>, <code>verse</code>, <code>marin</code>, <code>cedar</code></li>
                                                <li>Voice availability depends on the selected model.</li>
                                            </ul>
                                        </>
                                    }
                                    link="https://platform.openai.com/docs/guides/text-to-speech#voice-options"
                                    linkText="Voice samples"
                                />
                            </div>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.voice || 'alloy'}
                                onChange={(e) => handleChange('voice', e.target.value)}
                            >
                                {config.voice && !voices.includes(config.voice) && <option value={config.voice}>{config.voice} (configured; verify model compatibility)</option>}
                                {voices.map(voice => <option key={voice} value={voice}>{voice}</option>)}
                            </select>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Target Encoding</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Target Encoding</strong> — final codec the engine outputs to Asterisk after decoding OpenAI's MP3/PCM response.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><code>mulaw</code>/<code>ulaw</code> — telephony standard</li>
                                                <li><code>pcm16</code>/<code>linear16</code> — 16-bit PCM</li>
                                            </ul>
                                            Match your Asterisk channel codec.
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
                                <option value="ulaw">ulaw</option>
                                <option value="pcm16">PCM16</option>
                                <option value="linear16">Linear16</option>
                            </select>
                            <p className="text-xs text-muted-foreground">
                                Final format for playback. Match your Asterisk codec.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Target Sample Rate (Hz)</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Target Sample Rate</strong> — final playback sample rate.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><code>8000 Hz</code> — telephony / PSTN</li>
                                                <li><code>16000 Hz</code> — wideband HD voice</li>
                                                <li><code>24000 Hz</code> — OpenAI's native synthesis rate; best fidelity</li>
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
                                Final sample rate. 8000 Hz for standard telephony.
                            </p>
                        </div>
                        <div className="md:col-span-2">
                            <OutputResamplerField
                                value={config.output_resampler}
                                sourceRate={config.source_format?.sample_rate || config.source_sample_rate_hz || 24000}
                                targetRate={config.target_sample_rate_hz || 8000}
                                onChange={(value) => handleChange('output_resampler', value)}
                            />
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Chunk Size (ms)</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Chunk Size</strong> — duration of each audio frame the engine yields downstream.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>Default <code>20ms</code> — standard SIP/RTP frame</li>
                                                <li>Smaller frames = lower time-to-first-audio</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.chunk_size_ms || 20}
                                onChange={(e) => handleChange('chunk_size_ms', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Audio chunk duration. 20ms is standard for real-time.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Response Timeout (sec)</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Response Timeout</strong> — max wait for the full TTS audio response.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>Default <code>5s</code></li>
                                                <li>Raise for very long passages or <code>tts-1-hd</code></li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.response_timeout_sec || 5}
                                onChange={(e) => handleChange('response_timeout_sec', parseFloat(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Max wait time for TTS response. Increase for longer text.
                            </p>
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
