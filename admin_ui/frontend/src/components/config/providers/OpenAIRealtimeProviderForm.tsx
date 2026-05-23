import React from 'react';
import ProviderCredentialsCard, { applyCredentialPatch } from './ProviderCredentialsCard';

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
                    <label className="text-sm font-medium">
                        Realtime Base URL
                        <span className="text-xs text-muted-foreground ml-2">(base_url)</span>
                    </label>
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
                        <label className="text-sm font-medium">Realtime API Version</label>
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
                        <label className="text-sm font-medium">
                            Project ID
                            <span className="text-xs text-muted-foreground ml-2">(optional)</span>
                        </label>
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
                        <label className="text-sm font-medium">Model</label>
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
                        <label className="text-sm font-medium">Voice</label>
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
                        <label className="text-sm font-medium">Temperature</label>
                        <input
                            type="number"
                            step="0.1"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.temperature || 0.8}
                            onChange={(e) => handleChange('temperature', parseFloat(e.target.value))}
                        />
                    </div>

                    <div className="space-y-2">
                        <label className="text-sm font-medium">Max Response Tokens</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.max_response_output_tokens || 4096}
                            onChange={(e) => handleChange('max_response_output_tokens', parseInt(e.target.value))}
                        />
                    </div>
                </div>

                <div className="space-y-2 mt-4">
                    <label className="text-sm font-medium">System Instructions</label>
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
                            <label className="text-sm font-medium">Type</label>
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
                            <label className="text-sm font-medium">Threshold (0.0 - 1.0)</label>
                            <input
                                type="number"
                                step="0.1"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.turn_detection?.threshold || 0.6}
                                onChange={(e) => handleNestedChange('turn_detection', 'threshold', parseFloat(e.target.value))}
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Silence Duration (ms)</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.turn_detection?.silence_duration_ms || 1000}
                                onChange={(e) => handleNestedChange('turn_detection', 'silence_duration_ms', parseInt(e.target.value))}
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Prefix Padding (ms)</label>
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
                        <label className="text-sm font-medium">Input Encoding</label>
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
                        <label className="text-sm font-medium">Input Sample Rate (Hz)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.input_sample_rate_hz || 8000}
                            onChange={(e) => handleChange('input_sample_rate_hz', parseInt(e.target.value))}
                        />
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
                            <option value="ulaw">u-law</option>
                        </select>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Output Sample Rate (Hz)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.output_sample_rate_hz || 24000}
                            onChange={(e) => handleChange('output_sample_rate_hz', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Target Encoding</label>
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
                        <label className="text-sm font-medium">Target Sample Rate (Hz)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.target_sample_rate_hz || 8000}
                            onChange={(e) => handleChange('target_sample_rate_hz', parseInt(e.target.value))}
                        />
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
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Provider Input Sample Rate (Hz)</label>
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
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Egress Pacer Warmup (ms)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.egress_pacer_warmup_ms || 320}
                            onChange={(e) => handleChange('egress_pacer_warmup_ms', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Response Modalities</label>
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
                        <label className="text-sm font-medium">Greeting</label>
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
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Input Gain Target RMS</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.input_gain_target_rms || 0}
                            onChange={(e) => handleChange('input_gain_target_rms', parseFloat(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Input Gain Max dB</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.input_gain_max_db || 0}
                            onChange={(e) => handleChange('input_gain_max_db', parseFloat(e.target.value))}
                        />
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
                    </div>
                </div>
            </div>
        </div>
    );
};

export default OpenAIRealtimeProviderForm;
