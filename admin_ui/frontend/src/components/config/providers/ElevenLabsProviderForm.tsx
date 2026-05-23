import React, { useEffect, useRef } from 'react';
import { Info, Mic } from 'lucide-react';
import ProviderCredentialsCard, { applyCredentialPatch } from './ProviderCredentialsCard';

interface ElevenLabsProviderFormProps {
    config: any;
    onChange: (newConfig: any) => void;
    providerKey?: string;
}

const ElevenLabsProviderForm: React.FC<ElevenLabsProviderFormProps> = ({ config, onChange, providerKey }) => {
    // Latest-config ref for race-free credential patches.
    const configRef = useRef(config);
    useEffect(() => {
        configRef.current = config;
    }, [config]);

    const handleChange = (field: string, value: any) => {
        onChange({ ...config, [field]: value });
    };

    // Determine mode based on type or presence of agent_id
    // type: 'full' indicates Conversational Agent (matches GenericProviderForm pattern)
    // agent_id also indicates Agent mode
    // Otherwise defaults to TTS mode
    const mode = config.mode || ((config.agent_id || config.type === 'full') ? 'agent' : 'tts');

    const handleModeChange = (newMode: 'agent' | 'tts') => {
        if (newMode === 'agent') {
            // Switch to Agent: keep agent_id if exists, clear voice_id
            const { voice_id, model_id, ...rest } = config;
            onChange({ ...rest, mode: 'agent', type: 'elevenlabs_agent' });
        } else {
            // Switch to TTS: keep voice_id if exists, clear agent_id AND
            // any per-instance agent_id_file. Leaving agent_id_file behind
            // would persist a stale credential reference pointing at an
            // agent-id file that's no longer relevant in TTS mode (and may
            // cause the engine to fail provider validation).
            const { agent_id, agent_id_file, ...rest } = config;
            onChange({ ...rest, mode: 'tts', type: 'elevenlabs' });
        }
    };

    return (
        <div className="space-y-6">
            <div>
                <h4 className="font-semibold mb-3">Credentials</h4>
                <div className="space-y-3">
                    <ProviderCredentialsCard
                        providerKey={providerKey}
                        credentialType="api-key"
                        label="ElevenLabs API Key"
                        placeholder="xi-..."
                        envVarFallback="ELEVENLABS_API_KEY"
                        inlineValue={config.api_key}
                        onConfigPatch={(patch) => applyCredentialPatch(configRef, patch, onChange)}
                        helpText={
                            <>
                                Find your key in the{' '}
                                <a
                                    href="https://elevenlabs.io/app/settings/api-keys"
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="text-primary hover:underline"
                                >
                                    ElevenLabs Console
                                </a>
                                .
                            </>
                        }
                    />
                    {mode === 'agent' && (
                        <ProviderCredentialsCard
                            providerKey={providerKey}
                            credentialType="agent-id"
                            label="ElevenLabs Agent ID"
                            placeholder="agent_..."
                            envVarFallback="ELEVENLABS_AGENT_ID"
                            inlineValue={config.agent_id}
                            onConfigPatch={(patch) => applyCredentialPatch(configRef, patch, onChange)}
                            helpText="The Agent ID identifies which Conversational AI agent to use."
                        />
                    )}
                </div>
            </div>

            {/* Mode Selection */}
            <div className="space-y-2">
                <label className="text-sm font-medium">Provider Mode</label>
                <div className="flex gap-4">
                    <label className="flex items-center gap-2 border p-3 rounded-lg cursor-pointer hover:bg-accent has-[:checked]:bg-accent has-[:checked]:border-primary">
                        <input
                            type="radio"
                            name="elevenlabs_mode"
                            value="agent"
                            checked={mode === 'agent'}
                            onChange={() => handleModeChange('agent')}
                            className="w-4 h-4"
                        />
                        <div>
                            <span className="block font-medium text-sm">Conversational Agent</span>
                            <span className="block text-xs text-muted-foreground">End-to-end (STT+LLM+TTS)</span>
                        </div>
                    </label>
                    <label className="flex items-center gap-2 border p-3 rounded-lg cursor-pointer hover:bg-accent has-[:checked]:bg-accent has-[:checked]:border-primary">
                        <input
                            type="radio"
                            name="elevenlabs_mode"
                            value="tts"
                            checked={mode === 'tts'}
                            onChange={() => handleModeChange('tts')}
                            className="w-4 h-4"
                        />
                        <div>
                            <span className="block font-medium text-sm">TTS Engine</span>
                            <span className="block text-xs text-muted-foreground">Text-to-Speech Only</span>
                        </div>
                    </label>
                </div>
            </div>

            {/* Agent Mode Info */}
            {mode === 'agent' && (
                <div className="bg-blue-50/50 dark:bg-blue-900/10 p-4 rounded-md border border-blue-100 dark:border-blue-900/20">
                    <div className="flex items-start gap-3">
                        <Info className="w-5 h-5 text-blue-600 dark:text-blue-400 mt-0.5 flex-shrink-0" />
                        <div className="text-sm text-blue-800 dark:text-blue-300">
                            <p className="font-semibold mb-1">ElevenLabs Conversational AI</p>
                            <p className="text-blue-700 dark:text-blue-400">
                                Uses a pre-configured agent from your ElevenLabs dashboard.
                                Voice, system prompt, and LLM are managed by ElevenLabs.
                            </p>
                        </div>
                    </div>
                </div>
            )}

            {/* TTS Mode Info */}
            {mode === 'tts' && (
                <div className="bg-purple-50/50 dark:bg-purple-900/10 p-4 rounded-md border border-purple-100 dark:border-purple-900/20">
                    <div className="flex items-start gap-3">
                        <Mic className="w-5 h-5 text-purple-600 dark:text-purple-400 mt-0.5 flex-shrink-0" />
                        <div className="text-sm text-purple-800 dark:text-purple-300">
                            <p className="font-semibold mb-1">ElevenLabs TTS</p>
                            <p className="text-purple-700 dark:text-purple-400">
                                Uses ElevenLabs for high-quality speech synthesis.
                                Combine this with other modular providers (e.g., OpenAI LLM, Deepgram STT) in a pipeline.
                            </p>
                        </div>
                    </div>
                </div>
            )}

            {/* Agent Configuration */}
            {mode === 'agent' && (
                <div>
                    <h4 className="font-semibold mb-3">Agent Details</h4>
                    <div className="space-y-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">
                                Agent ID
                                <span className="text-destructive ml-1">*</span>
                            </label>
                            <input
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background font-mono text-sm"
                                value={config.agent_id || ''}
                                onChange={(e) => handleChange('agent_id', e.target.value)}
                                placeholder="${ELEVENLABS_AGENT_ID}"
                            />
                            <p className="text-xs text-muted-foreground">
                                Found in <a href="https://elevenlabs.io/app/agents" target="_blank" rel="noopener noreferrer" className="text-primary underline">Agents Dashboard</a>
                            </p>
                            <p className="text-xs text-amber-600 dark:text-amber-400">
                                <strong>Tip:</strong> Use <code className="bg-muted px-1 rounded">${'{'}ELEVENLABS_AGENT_ID{'}'}</code> and set the actual value in{' '}
                                <a href="/env" className="text-primary underline">System → Environment</a>
                            </p>
                        </div>

                        {/* Tools Hint */}
                        <div className="text-xs text-muted-foreground p-3 bg-muted rounded">
                            <strong>Note:</strong> Ensure client tools (hangup_call, etc.) are defined in the ElevenLabs dashboard for this agent.
                        </div>
                    </div>
                </div>
            )}

            {/* TTS Configuration */}
            {mode === 'tts' && (
                <div>
                    <h4 className="font-semibold mb-3">Voice Settings</h4>
                    <div className="space-y-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">
                                Voice ID
                                <span className="text-destructive ml-1">*</span>
                            </label>
                            <input
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background font-mono text-sm"
                                value={config.voice_id || '21m00Tcm4TlvDq8ikWAM'}
                                onChange={(e) => handleChange('voice_id', e.target.value)}
                                placeholder="e.g. 21m00Tcm4TlvDq8ikWAM"
                            />
                            <p className="text-xs text-muted-foreground">
                                Provide a Voice ID from the <a href="https://elevenlabs.io/app/voice-lab" target="_blank" className="text-primary underline">Voice Lab</a>.
                            </p>
                        </div>

                        <div className="space-y-2">
                            <label className="text-sm font-medium">Model ID</label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.model_id || 'eleven_turbo_v2_5'}
                                onChange={(e) => handleChange('model_id', e.target.value)}
                            >
                                <option value="eleven_turbo_v2_5">Turbo v2.5 (Fastest, English only)</option>
                                <option value="eleven_multilingual_v2">Multilingual v2 (Better quality)</option>
                                <option value="eleven_monolingual_v1">Monolingual v1 (Legacy)</option>
                            </select>
                        </div>

                        <div className="space-y-2">
                            <label className="text-sm font-medium">Stability (0.0 - 1.0)</label>
                            <input
                                type="number"
                                step="0.1"
                                min="0"
                                max="1"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.stability || 0.5}
                                onChange={(e) => handleChange('stability', parseFloat(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Voice consistency. Higher = more stable, lower = more expressive/variable.
                            </p>
                        </div>

                        <div className="space-y-2">
                            <label className="text-sm font-medium">Similarity Boost (0.0 - 1.0)</label>
                            <input
                                type="number"
                                step="0.1"
                                min="0"
                                max="1"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.similarity_boost || 0.75}
                                onChange={(e) => handleChange('similarity_boost', parseFloat(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Voice clarity vs. creativity. Higher = closer to original voice.
                            </p>
                        </div>
                    </div>
                </div>
            )}

            {/* Authentication */}
            <div>
                <h4 className="font-semibold mb-3">Authentication</h4>
                <div className="bg-amber-50/30 dark:bg-amber-900/10 p-3 rounded-md border border-amber-200 dark:border-amber-900/30 mb-3">
                    <p className="text-sm text-amber-800 dark:text-amber-300">
                        <strong>⚠️ Required:</strong> Set <code className="bg-amber-100 dark:bg-amber-900/50 px-1 rounded">ELEVENLABS_API_KEY</code> in your <strong>.env file</strong>.
                    </p>
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2">
                    <label className="text-sm font-medium">Input Sample Rate (Hz)</label>
                    <input
                        type="number"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.input_sample_rate || 16000}
                        onChange={(e) => handleChange('input_sample_rate', parseInt(e.target.value))}
                    />
                    <p className="text-xs text-muted-foreground">
                        Audio sample rate for input. 16000 Hz recommended.
                    </p>
                </div>
                <div className="space-y-2">
                    <label className="text-sm font-medium">Output Sample Rate (Hz)</label>
                    <input
                        type="number"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.output_sample_rate || 16000}
                        onChange={(e) => handleChange('output_sample_rate', parseInt(e.target.value))}
                    />
                    <p className="text-xs text-muted-foreground">
                        TTS output sample rate. 16000 Hz or 22050 Hz typical.
                    </p>
                </div>
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
    );
};

export default ElevenLabsProviderForm;
