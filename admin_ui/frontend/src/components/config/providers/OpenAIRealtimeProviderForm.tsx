import React from 'react';
import ProviderCredentialsCard, { applyCredentialPatch } from './ProviderCredentialsCard';
import HelpTooltip from '../../ui/HelpTooltip';

interface OpenAIRealtimeProviderFormProps {
    config: any;
    onChange: (newConfig: any) => void;
    providerKey?: string;
}

const OpenAIRealtimeProviderForm: React.FC<OpenAIRealtimeProviderFormProps> = ({ config, onChange, providerKey }) => {
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

    const responseModalitiesValue = Array.isArray(config.response_modalities)
        ? config.response_modalities.join(',')
        : (typeof config.response_modalities === 'string' && config.response_modalities
            ? config.response_modalities
            : 'audio');

    return (
        <div className="space-y-6">
            <div>
                <h4 className="font-semibold mb-3">Credentials</h4>
                <ProviderCredentialsCard
                    providerKey={providerKey}
                    credentialType="api-key"
                    label="OpenAI API Key"
                    placeholder="sk-..."
                    envVarFallback="OPENAI_API_KEY"
                    inlineValue={config.api_key}
                    onConfigPatch={(patch) => applyCredentialPatch(patch, onChange)}
                    helpText={
                        <>
                            Find your key at{' '}
                            <a
                                href="https://platform.openai.com/api-keys"
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-primary hover:underline"
                            >
                                platform.openai.com/api-keys
                            </a>
                            . Per-instance keys override the env var fallback.
                        </>
                    }
                />
            </div>

            <div>
                <h4 className="font-semibold mb-3">API Endpoint</h4>
                <div className="space-y-2">
                    <div className="flex items-center gap-1.5">
                        <label className="text-sm font-medium">
                            Realtime Base URL
                            <span className="text-xs text-muted-foreground ml-2">(base_url)</span>
                        </label>
                        <HelpTooltip
                            content={
                                <>
                                    <strong>Realtime Base URL</strong> — WebSocket endpoint the engine connects to.
                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                        <li><code>wss://api.openai.com/v1/realtime</code> — OpenAI default</li>
                                        <li>Override for Azure OpenAI Realtime or a self-hosted compatible gateway</li>
                                        <li>Must be a <code>wss://</code> URL — plain HTTPS will fail the handshake</li>
                                    </ul>
                                </>
                            }
                            link="https://platform.openai.com/docs/guides/realtime"
                            linkText="OpenAI Realtime docs"
                        />
                    </div>
                    <input
                        type="text"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.base_url || 'wss://api.openai.com/v1/realtime'}
                        onChange={(e) => handleChange('base_url', e.target.value)}
                        placeholder="wss://api.openai.com/v1/realtime"
                    />
                    <p className="text-xs text-muted-foreground">
                        WebSocket endpoint for OpenAI Realtime API. Change for Azure OpenAI or compatible services.
                    </p>
                </div>
            </div>

            <div>
                <h4 className="font-semibold mb-3">API Version & Project</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Realtime API Version</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>API Version</strong> — selects the OpenAI Realtime API surface.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>beta</code> — sends the <code>OpenAI-Beta: realtime=v1</code> header; broad compatibility, works with preview models</li>
                                            <li><code>ga</code> — omits the Beta header; may require additional OpenAI account verification, uses GA models like <code>gpt-realtime</code></li>
                                        </ul>
                                        Switching versions auto-selects an appropriate default model.
                                    </>
                                }
                                link="https://platform.openai.com/docs/guides/realtime"
                                linkText="OpenAI Realtime docs"
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.api_version || 'beta'}
                            onChange={(e) => {
                                const apiVersion = e.target.value;
                                const defaultModel = apiVersion === 'ga'
                                    ? 'gpt-realtime'
                                    : 'gpt-4o-realtime-preview-2024-12-17';
                                onChange({ ...config, api_version: apiVersion, model: defaultModel });
                            }}
                        >
                            <option value="beta">Beta (default)</option>
                            <option value="ga">GA</option>
                        </select>
                        <p className="text-xs text-muted-foreground">
                            <strong>Beta</strong> is the default for broad compatibility and uses the <code>OpenAI-Beta</code> header.
                            <strong className="ml-1">GA</strong> removes that header and may require additional OpenAI account verification.
                        </p>
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">
                                Project ID
                                <span className="text-xs text-muted-foreground ml-2">(optional)</span>
                            </label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Project ID</strong> — optional OpenAI project scope, sent as <code>OpenAI-Project</code> header.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>Routes usage and billing to a specific project under your org</li>
                                            <li>Format <code>proj_...</code></li>
                                            <li>Leave blank to use the API key's default project</li>
                                        </ul>
                                    </>
                                }
                                link="https://platform.openai.com/settings/organization/general"
                                linkText="OpenAI project settings"
                            />
                        </div>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.project_id || ''}
                            onChange={(e) => handleChange('project_id', e.target.value || null)}
                            placeholder="proj_..."
                        />
                        <p className="text-xs text-muted-foreground">
                            OpenAI Project ID for usage tracking. Find it at{' '}
                            <a
                                href="https://platform.openai.com/settings/organization/general"
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-blue-500 hover:underline"
                            >
                                platform.openai.com/settings
                            </a>
                        </p>
                    </div>
                </div>
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
                                        <strong>Model</strong> — which realtime model the WebSocket uses.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>gpt-4o-realtime-preview</code> / <code>gpt-realtime</code> — full quality (~5–8¢/min)</li>
                                            <li><code>gpt-4o-mini-realtime-preview</code> / <code>gpt-realtime-mini</code> — lower cost (~3¢/min), good for high-volume</li>
                                            <li>Dated snapshots (e.g. <code>2024-12-17</code>) pin behavior; "Latest" tracks the moving alias</li>
                                        </ul>
                                        Available options depend on the API Version selected above.
                                    </>
                                }
                                link="https://platform.openai.com/docs/guides/realtime"
                                linkText="OpenAI Realtime docs"
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={
                                config.model
                                || ((config.api_version || 'beta') === 'ga'
                                    ? 'gpt-realtime'
                                    : 'gpt-4o-realtime-preview-2024-12-17')
                            }
                            onChange={(e) => handleChange('model', e.target.value)}
                        >
                            {(config.api_version || 'beta') === 'ga' ? (
                                <>
                                    <optgroup label="GA Models">
                                        <option value="gpt-realtime">GPT Realtime</option>
                                        <option value="gpt-realtime-mini">GPT Realtime Mini</option>
                                    </optgroup>
                                </>
                            ) : (
                                <>
                                    <optgroup label="Beta Preview Models">
                                        <option value="gpt-4o-realtime-preview">GPT-4o Realtime (Latest)</option>
                                        <option value="gpt-4o-realtime-preview-2025-06-03">GPT-4o Realtime (2025-06-03)</option>
                                        <option value="gpt-4o-realtime-preview-2024-12-17">GPT-4o Realtime (2024-12-17)</option>
                                        <option value="gpt-4o-realtime-preview-2024-10-01">GPT-4o Realtime (2024-10-01)</option>
                                    </optgroup>
                                    <optgroup label="Beta Mini Models">
                                        <option value="gpt-4o-mini-realtime-preview">GPT-4o Mini Realtime (Latest)</option>
                                        <option value="gpt-4o-mini-realtime-preview-2024-12-17">GPT-4o Mini Realtime (2024-12-17)</option>
                                    </optgroup>
                                </>
                            )}
                        </select>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Voice</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Voice</strong> — speaker identity used for synthesized audio.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>alloy</code>, <code>coral</code>, <code>shimmer</code>, <code>sage</code>, <code>marin</code> — female</li>
                                            <li><code>ash</code>, <code>ballad</code>, <code>echo</code>, <code>verse</code>, <code>cedar</code> — male</li>
                                            <li>Voice identity is consistent across the call; pick one matching the agent's persona</li>
                                            <li>Newer voices (<code>cedar</code>, <code>marin</code>) may only be available on newer models</li>
                                        </ul>
                                    </>
                                }
                                link="https://platform.openai.com/docs/guides/realtime"
                                linkText="OpenAI Realtime docs"
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.voice || 'alloy'}
                            onChange={(e) => handleChange('voice', e.target.value)}
                        >
                            <optgroup label="Realtime Voices">
                                <option value="alloy">Alloy - Female (neutral, balanced)</option>
                                <option value="ash">Ash - Male (clear, direct)</option>
                                <option value="ballad">Ballad - Male (warm, storytelling)</option>
                                <option value="coral">Coral - Female (friendly, conversational)</option>
                                <option value="echo">Echo - Male (soft, calm)</option>
                                <option value="sage">Sage - Female (wise, authoritative)</option>
                                <option value="shimmer">Shimmer - Female (bright, optimistic)</option>
                                <option value="verse">Verse - Male (expressive, dynamic)</option>
                                <option value="cedar">Cedar - Male (warm, natural)</option>
                                <option value="marin">Marin - Female (clear, professional)</option>
                            </optgroup>
                        </select>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Temperature</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Temperature</strong> — randomness of token sampling.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>0.6</code> — task-focused, consistent (recommended for scripted agents)</li>
                                            <li><code>0.8</code> — OpenAI default, balanced</li>
                                            <li><code>1.0+</code> — more creative, less predictable</li>
                                        </ul>
                                        Note: Realtime API constrains the valid range; very low values may be rejected.
                                    </>
                                }
                                link="https://platform.openai.com/docs/guides/realtime"
                                linkText="OpenAI Realtime docs"
                            />
                        </div>
                        <input
                            type="number"
                            step="0.1"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.temperature || 0.8}
                            onChange={(e) => handleChange('temperature', parseFloat(e.target.value))}
                        />
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Max Response Tokens</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>max_response_output_tokens</strong> — cap on a single assistant response.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>4096</code> — default, plenty for normal turns</li>
                                            <li><code>1024</code>–<code>2048</code> — keeps the agent terse on phone calls</li>
                                            <li><code>inf</code> not supported — set a finite ceiling to prevent runaway billing</li>
                                        </ul>
                                        Telephony users typically want shorter responses; lower this to enforce brevity.
                                    </>
                                }
                                link="https://platform.openai.com/docs/guides/realtime"
                                linkText="OpenAI Realtime docs"
                            />
                        </div>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.max_response_output_tokens || 4096}
                            onChange={(e) => handleChange('max_response_output_tokens', parseInt(e.target.value))}
                        />
                    </div>
                </div>

                <div className="space-y-2 mt-4">
                    <div className="flex items-center gap-1.5">
                        <label className="text-sm font-medium">System Instructions</label>
                        <HelpTooltip
                            content={
                                <>
                                    <strong>System Instructions</strong> — the persona / behavior prompt sent at session start.
                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                        <li>Defines role, tone, escalation rules, allowed topics</li>
                                        <li>For phone agents: instruct brevity, ask one question at a time, no markdown</li>
                                        <li>Include explicit handoff/transfer triggers if you wire tools to them</li>
                                        <li>Changes here apply on the next call, not mid-session</li>
                                    </ul>
                                </>
                            }
                            link="https://platform.openai.com/docs/guides/realtime"
                            linkText="OpenAI Realtime docs"
                        />
                    </div>
                    <textarea
                        className="w-full p-2 rounded border border-input bg-background min-h-[100px] font-mono text-sm"
                        value={config.instructions || ''}
                        onChange={(e) => handleChange('instructions', e.target.value)}
                        placeholder="You are a helpful assistant..."
                    />
                </div>

                <div className="space-y-4 mt-4">
                    <h4 className="font-semibold text-sm border-b pb-2">Turn Detection (VAD)</h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Type</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Turn Detection Type</strong> — how the model decides the user is done speaking.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><code>server_vad</code> — OpenAI's server-side voice activity detection (recommended for phone calls)</li>
                                                <li><code>none</code> — push-to-talk; you must explicitly commit audio buffers. Not normal for telephony</li>
                                            </ul>
                                        </>
                                    }
                                    link="https://platform.openai.com/docs/guides/realtime"
                                    linkText="OpenAI Realtime docs"
                                />
                            </div>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.turn_detection?.type || 'server_vad'}
                                onChange={(e) => handleNestedChange('turn_detection', 'type', e.target.value)}
                            >
                                <option value="server_vad">Server VAD</option>
                                <option value="none">None (Push to Talk)</option>
                            </select>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Threshold (0.0 - 1.0)</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>VAD Threshold</strong> — how confident VAD must be that audio is speech.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><code>0.3</code>–<code>0.5</code> — sensitive, picks up quiet callers but more false-starts on background noise</li>
                                                <li><code>0.6</code> — balanced default for telephony</li>
                                                <li><code>0.7</code>–<code>0.9</code> — strict, fewer false triggers but may miss soft speech</li>
                                            </ul>
                                            Tune alongside Silence Duration and Prefix Padding.
                                        </>
                                    }
                                    link="https://platform.openai.com/docs/guides/realtime"
                                    linkText="OpenAI Realtime docs"
                                />
                            </div>
                            <input
                                type="number"
                                step="0.1"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.turn_detection?.threshold || 0.6}
                                onChange={(e) => handleNestedChange('turn_detection', 'threshold', parseFloat(e.target.value))}
                            />
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Silence Duration (ms)</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>silence_duration_ms</strong> — how long the caller must stay silent before VAD declares end-of-turn.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><code>500</code> — snappy responses, but cuts off thinkers ("uhh…")</li>
                                                <li><code>1000</code> — balanced default</li>
                                                <li><code>1500</code>–<code>2000</code> — patient, good for older callers or non-native speakers</li>
                                            </ul>
                                            Lower = lower latency; higher = fewer interruptions.
                                        </>
                                    }
                                    link="https://platform.openai.com/docs/guides/realtime"
                                    linkText="OpenAI Realtime docs"
                                />
                            </div>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.turn_detection?.silence_duration_ms || 1000}
                                onChange={(e) => handleNestedChange('turn_detection', 'silence_duration_ms', parseInt(e.target.value))}
                            />
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Prefix Padding (ms)</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>prefix_padding_ms</strong> — audio captured before VAD detects speech, included in the user buffer.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><code>200</code>–<code>300</code> — typical default; preserves consonants at the start of words</li>
                                                <li>Too low: speech sounds clipped ("ello" instead of "hello")</li>
                                                <li>Too high: more idle audio sent to STT, slightly more cost</li>
                                            </ul>
                                        </>
                                    }
                                    link="https://platform.openai.com/docs/guides/realtime"
                                    linkText="OpenAI Realtime docs"
                                />
                            </div>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.turn_detection?.prefix_padding_ms || 300}
                                onChange={(e) => handleNestedChange('turn_detection', 'prefix_padding_ms', parseInt(e.target.value))}
                            />
                        </div>
                    </div>
                    <p className="text-xs text-muted-foreground">
                        <code>create_response</code> and <code>interrupt_response</code> are managed internally in GA mode.
                    </p>
                </div>
            </div>

            <div className="space-y-4">
                <h4 className="font-semibold text-sm border-b pb-2">Audio Configuration</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Input Encoding</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Input Encoding</strong> — codec for audio arriving FROM Asterisk into the engine.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>ulaw</code> — Asterisk-native telephony codec @ 8 kHz (recommended)</li>
                                            <li><code>pcm16</code> / <code>linear16</code> — uncompressed 16-bit PCM, used when Asterisk has already transcoded</li>
                                        </ul>
                                        Must match what your Asterisk dial-plan / AudioSocket actually sends.
                                    </>
                                }
                                link="https://platform.openai.com/docs/guides/realtime"
                                linkText="OpenAI Realtime docs"
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.input_encoding || 'ulaw'}
                            onChange={(e) => handleChange('input_encoding', e.target.value)}
                        >
                            <option value="ulaw">u-law</option>
                            <option value="pcm16">PCM16</option>
                            <option value="linear16">Linear16</option>
                        </select>
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Input Sample Rate (Hz)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Input Sample Rate</strong> — sample rate of audio arriving from Asterisk.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>8000</code> — standard for <code>ulaw</code> / <code>alaw</code> telephony (PSTN, SIP)</li>
                                            <li><code>16000</code> — wideband HD voice (G.722, Opus)</li>
                                            <li><code>24000</code> — only if Asterisk is already upsampling for you</li>
                                        </ul>
                                        Must match the actual stream — wrong rate causes chipmunk / slowed-down audio.
                                    </>
                                }
                                link="https://platform.openai.com/docs/guides/realtime"
                                linkText="OpenAI Realtime docs"
                            />
                        </div>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.input_sample_rate_hz || 8000}
                            onChange={(e) => handleChange('input_sample_rate_hz', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Output Encoding</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Output Encoding</strong> — codec OpenAI emits to the engine before any local transcoding.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>linear16</code> / <code>pcm16</code> — uncompressed 16-bit PCM @ 24 kHz (OpenAI's native output)</li>
                                            <li><code>ulaw</code> — request OpenAI to encode 8 kHz μ-law directly; saves an engine-side resample</li>
                                        </ul>
                                        Pair with the matching Output Sample Rate.
                                    </>
                                }
                                link="https://platform.openai.com/docs/guides/realtime"
                                linkText="OpenAI Realtime docs"
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.output_encoding || 'linear16'}
                            onChange={(e) => handleChange('output_encoding', e.target.value)}
                        >
                            <option value="linear16">Linear16</option>
                            <option value="pcm16">PCM16</option>
                            <option value="ulaw">u-law</option>
                        </select>
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Output Sample Rate (Hz)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Output Sample Rate</strong> — sample rate of OpenAI's emitted audio.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>24000</code> — OpenAI's native rate for <code>pcm16</code> / <code>linear16</code></li>
                                            <li><code>8000</code> — only when Output Encoding is <code>ulaw</code></li>
                                        </ul>
                                        Mismatched rates produce distorted output — keep this aligned with the encoding above.
                                    </>
                                }
                                link="https://platform.openai.com/docs/guides/realtime"
                                linkText="OpenAI Realtime docs"
                            />
                        </div>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.output_sample_rate_hz || 24000}
                            onChange={(e) => handleChange('output_sample_rate_hz', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Target Encoding</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Target Encoding</strong> — codec the engine sends BACK to Asterisk after any local resample.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>mulaw</code> / <code>ulaw</code> — standard PSTN/SIP (US/Canada)</li>
                                            <li><code>alaw</code> — standard PSTN/SIP (most of EU/world)</li>
                                            <li><code>pcm16</code> / <code>linear16</code> — uncompressed; only if your AudioSocket leg is wideband</li>
                                        </ul>
                                        Must match what Asterisk expects on the return path.
                                    </>
                                }
                                link="https://platform.openai.com/docs/guides/realtime"
                                linkText="OpenAI Realtime docs"
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.target_encoding || 'mulaw'}
                            onChange={(e) => handleChange('target_encoding', e.target.value)}
                        >
                            <option value="mulaw">mu-law</option>
                            <option value="ulaw">u-law (alias)</option>
                            <option value="alaw">A-law</option>
                            <option value="pcm16">PCM16</option>
                            <option value="linear16">Linear16</option>
                        </select>
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Target Sample Rate (Hz)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Target Sample Rate</strong> — sample rate of audio sent back to Asterisk.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>8000</code> — required for <code>ulaw</code> / <code>alaw</code> (standard telephony)</li>
                                            <li><code>16000</code>+ — only for wideband legs (G.722, Opus)</li>
                                        </ul>
                                    </>
                                }
                                link="https://platform.openai.com/docs/guides/realtime"
                                linkText="OpenAI Realtime docs"
                            />
                        </div>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.target_sample_rate_hz || 8000}
                            onChange={(e) => handleChange('target_sample_rate_hz', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Provider Input Encoding</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Provider Input Encoding</strong> — codec the engine sends INTO OpenAI's WebSocket (after upsampling caller audio).
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>linear16</code> / <code>pcm16</code> — uncompressed 16-bit PCM (recommended)</li>
                                            <li>Independent of the codec Asterisk used — the engine transcodes</li>
                                        </ul>
                                    </>
                                }
                                link="https://platform.openai.com/docs/guides/realtime"
                                linkText="OpenAI Realtime docs"
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.provider_input_encoding || 'linear16'}
                            onChange={(e) => handleChange('provider_input_encoding', e.target.value)}
                        >
                            <option value="linear16">Linear16</option>
                            <option value="pcm16">PCM16</option>
                        </select>
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Provider Input Sample Rate (Hz)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Provider Input Sample Rate</strong> — sample rate the engine uses when sending PCM to OpenAI.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>24000</code> — OpenAI's native rate; engine upsamples 8 kHz telephony for best quality</li>
                                            <li><code>16000</code> — acceptable, smaller payloads</li>
                                        </ul>
                                        Match this to what OpenAI's session config expects for the chosen model.
                                    </>
                                }
                                link="https://platform.openai.com/docs/guides/realtime"
                                linkText="OpenAI Realtime docs"
                            />
                        </div>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.provider_input_sample_rate_hz || 24000}
                            onChange={(e) => handleChange('provider_input_sample_rate_hz', parseInt(e.target.value))}
                        />
                    </div>
                </div>
            </div>

            <div className="space-y-4">
                <h4 className="font-semibold text-sm border-b pb-2">Behavior</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="flex items-center space-x-2">
                        <input
                            type="checkbox"
                            id="openai_realtime_egress_pacer_enabled"
                            className="rounded border-input"
                            checked={config.egress_pacer_enabled ?? true}
                            onChange={(e) => handleChange('egress_pacer_enabled', e.target.checked)}
                        />
                        <label htmlFor="openai_realtime_egress_pacer_enabled" className="text-sm font-medium">Egress Pacer</label>
                        <HelpTooltip
                            content={
                                <>
                                    <strong>Egress Pacer</strong> — paces audio frames out to Asterisk at real-time rate instead of bursting.
                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                        <li><strong>Enabled (recommended)</strong> — smooth playback, avoids RTP jitter / buffer overruns</li>
                                        <li>Disable only for debugging or non-realtime sinks</li>
                                    </ul>
                                </>
                            }
                        />
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Egress Pacer Warmup (ms)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Egress Pacer Warmup</strong> — initial buffer the pacer fills before it starts emitting audio.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>320</code> ms — default, ~16 frames of 20 ms audio</li>
                                            <li>Higher: more resilience to network jitter, slightly higher first-word latency</li>
                                            <li>Lower: snappier response start, more risk of underrun on a jittery link</li>
                                        </ul>
                                    </>
                                }
                            />
                        </div>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.egress_pacer_warmup_ms || 320}
                            onChange={(e) => handleChange('egress_pacer_warmup_ms', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Response Modalities</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Response Modalities</strong> — which output streams the model emits.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>audio</code> — voice only (typical phone agent)</li>
                                            <li><code>audio,text</code> — voice + a streamed text transcript (useful for logging, UI display)</li>
                                            <li><code>text</code> — text only, no TTS (rare for phone use)</li>
                                        </ul>
                                    </>
                                }
                                link="https://platform.openai.com/docs/guides/realtime"
                                linkText="OpenAI Realtime docs"
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={responseModalitiesValue}
                            onChange={(e) => handleChange('response_modalities', e.target.value.split(',').map((v) => v.trim()).filter(Boolean))}
                        >
                            <option value="audio">Audio</option>
                            <option value="audio,text">Audio & Text</option>
                            <option value="text">Text</option>
                        </select>
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Greeting</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Greeting</strong> — what the agent says first when the call connects.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>Spoken before the caller says anything (no waiting for VAD)</li>
                                            <li>Keep it short — long greetings frustrate repeat callers</li>
                                            <li>Leave empty to let the model improvise its own opening from the system instructions</li>
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
                    <div className="flex items-center space-x-2">
                        <input
                            type="checkbox"
                            id="openai_realtime_enabled"
                            className="rounded border-input"
                            checked={config.enabled ?? true}
                            onChange={(e) => handleChange('enabled', e.target.checked)}
                        />
                        <label htmlFor="openai_realtime_enabled" className="text-sm font-medium">Enabled</label>
                        <HelpTooltip
                            content={
                                <>
                                    <strong>Enabled</strong> — whether this provider is available for routing.
                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                        <li>Disabled providers stay configured but won't accept calls</li>
                                        <li>Useful for staging credentials before flipping live, or temporary takedowns</li>
                                    </ul>
                                </>
                            }
                        />
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Input Gain Target RMS</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Input Gain Target RMS</strong> — target loudness (root-mean-square, 0.0–1.0) the AGC normalizes caller audio to.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>0</code> — AGC disabled, raw audio forwarded</li>
                                            <li><code>0.05</code>–<code>0.1</code> — typical for quiet callers on mobile</li>
                                        </ul>
                                        Helps soft-spoken callers be heard by VAD/STT without distorting loud ones (capped by Input Gain Max dB).
                                    </>
                                }
                            />
                        </div>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.input_gain_target_rms || 0}
                            onChange={(e) => handleChange('input_gain_target_rms', parseFloat(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Input Gain Max dB</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Input Gain Max dB</strong> — ceiling on AGC amplification, in decibels.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>0</code> — disabled (no amplification cap, or AGC off)</li>
                                            <li><code>12</code>–<code>20</code> dB — typical safety cap; prevents AGC from boosting silence/noise floor</li>
                                        </ul>
                                        Pair with Input Gain Target RMS; this is the brake on the gain knob.
                                    </>
                                }
                            />
                        </div>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.input_gain_max_db || 0}
                            onChange={(e) => handleChange('input_gain_max_db', parseFloat(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Farewell Hangup Delay (seconds)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Farewell Hangup Delay</strong> — how long the engine waits after the agent's final words before tearing down the call.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>Leave blank to inherit the global default (<code>2.5s</code>)</li>
                                            <li><code>1.5</code>–<code>2.5</code>s — typical; covers the tail of TTS plus a beat of silence</li>
                                            <li>Too low: caller hears their last word clipped; too high: awkward dead air before disconnect</li>
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
                    </div>
                </div>
            </div>
        </div>
    );
};

export default OpenAIRealtimeProviderForm;
