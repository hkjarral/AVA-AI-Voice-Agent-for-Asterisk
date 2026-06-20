import React from 'react';
import { Info, Mic } from 'lucide-react';
import ProviderCredentialsCard, { applyCredentialPatch } from './ProviderCredentialsCard';
import HelpTooltip from '../../ui/HelpTooltip';

interface ElevenLabsProviderFormProps {
    config: any;
    onChange: (newConfig: any) => void;
    providerKey?: string;
}

const ElevenLabsProviderForm: React.FC<ElevenLabsProviderFormProps> = ({ config, onChange, providerKey }) => {
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
                        onConfigPatch={(patch) => applyCredentialPatch(patch, onChange)}
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
                            onConfigPatch={(patch) => applyCredentialPatch(patch, onChange)}
                            helpText="The Agent ID identifies which Conversational AI agent to use."
                        />
                    )}
                </div>
            </div>

            {/* Mode Selection */}
            <div className="space-y-2">
                <div className="flex items-center gap-1.5">
                    <label className="text-sm font-medium">Provider Mode</label>
                    <HelpTooltip
                        content={
                            <>
                                <strong>Provider Mode</strong> — selects how ElevenLabs participates in the call. This is the most important setting on this form: it changes which fields are required and how audio is routed.
                                <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                    <li><strong>Conversational Agent</strong> — full-agent mode (<code>type: elevenlabs_agent</code>). ElevenLabs handles STT + LLM + TTS end-to-end using a pre-built agent from the ElevenLabs dashboard. Requires an <code>agent_id</code>. Voice, system prompt, tools, and model are all configured in the ElevenLabs UI, not here.</li>
                                    <li><strong>TTS Engine</strong> — modular TTS-only slot (<code>type: elevenlabs</code>). ElevenLabs only synthesizes speech; you pair it with a separate STT (e.g. Deepgram) and LLM (e.g. OpenAI) provider. Requires a <code>voice_id</code>. Use this if you want to mix-and-match providers in a pipeline.</li>
                                </ul>
                                Pricing for both modes is roughly 8-10¢/min.
                            </>
                        }
                        link="https://elevenlabs.io/docs/conversational-ai/overview"
                        linkText="Conversational AI docs"
                    />
                </div>
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
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">
                                    Voice ID
                                    <span className="text-destructive ml-1">*</span>
                                </label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Voice ID</strong> — the UUID-like identifier of the ElevenLabs voice used to synthesize speech. Found in the Voice Lab (each voice card has a copy-ID button).
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>Default <code>21m00Tcm4TlvDq8ikWAM</code> is "Rachel" — a stock English voice.</li>
                                                <li>You can also use Voice IDs from voices you've cloned in the ElevenLabs dashboard.</li>
                                                <li>Voice cloning quality and language support depend on your ElevenLabs plan.</li>
                                            </ul>
                                        </>
                                    }
                                    link="https://elevenlabs.io/app/voice-lab"
                                    linkText="Voice Lab"
                                />
                            </div>
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
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Model ID</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Model ID</strong> — which ElevenLabs TTS model synthesizes audio. Latency and quality trade off against each other.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><code>eleven_flash_v2_5</code> — lowest latency (~75 ms), recommended for realtime telephony.</li>
                                                <li><code>eleven_turbo_v2_5</code> — balanced quality + latency, English-focused.</li>
                                                <li><code>eleven_multilingual_v2</code> — 29 languages, highest quality but slower.</li>
                                                <li><code>eleven_monolingual_v1</code> — legacy; prefer Turbo or Flash.</li>
                                            </ul>
                                        </>
                                    }
                                    link="https://elevenlabs.io/docs/models"
                                    linkText="ElevenLabs models"
                                />
                            </div>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.model_id || 'eleven_flash_v2_5'}
                                onChange={(e) => handleChange('model_id', e.target.value)}
                            >
                                <option value="eleven_flash_v2_5">Flash v2.5 (Lowest latency, recommended for telephony)</option>
                                <option value="eleven_turbo_v2_5">Turbo v2.5 (Fastest, English only)</option>
                                <option value="eleven_multilingual_v2">Multilingual v2 (Better quality)</option>
                                <option value="eleven_monolingual_v1">Monolingual v1 (Legacy)</option>
                            </select>
                        </div>

                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Stability (0.0 - 1.0)</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Stability</strong> — controls how consistent the voice sounds across utterances.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><strong>Higher (0.7-1.0)</strong> — very consistent and predictable, but can sound flat/monotone.</li>
                                                <li><strong>Lower (0.0-0.4)</strong> — more expressive and emotional, but may drift or produce artifacts on long output.</li>
                                                <li><strong>0.5</strong> (default) — balanced for most telephony use cases.</li>
                                            </ul>
                                        </>
                                    }
                                    link="https://elevenlabs.io/docs/speech-synthesis/voice-settings"
                                    linkText="Voice settings reference"
                                />
                            </div>
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
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Similarity Boost (0.0 - 1.0)</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Similarity Boost</strong> — how closely the synthesized audio should match the original voice sample (especially relevant for cloned voices).
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><strong>Higher (0.75-1.0)</strong> — sticks tightly to the source voice; cleaner for cloned voices but may amplify recording artifacts.</li>
                                                <li><strong>Lower (0.0-0.5)</strong> — more creative latitude; may sound less like the source.</li>
                                                <li><strong>0.75</strong> (default) — works well for most stock voices.</li>
                                            </ul>
                                        </>
                                    }
                                    link="https://elevenlabs.io/docs/speech-synthesis/voice-settings"
                                    linkText="Voice settings reference"
                                />
                            </div>
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
                    <div className="flex items-center gap-1.5">
                        <label className="text-sm font-medium">Input Sample Rate (Hz)</label>
                        <HelpTooltip
                            content={
                                <>
                                    <strong>Input Sample Rate</strong> — sample rate of the audio sent <em>to</em> ElevenLabs (caller's voice in Agent mode).
                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                        <li><strong>16000 Hz</strong> — recommended for telephony; Asterisk transcodes 8 kHz PCM/μ-law up to 16 kHz for the agent.</li>
                                        <li>Higher rates increase bandwidth without improving STT accuracy on phone-grade audio.</li>
                                        <li>This field is only meaningful in Conversational Agent mode; TTS-only mode does not consume input audio here.</li>
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
                        Audio sample rate for input. 16000 Hz recommended.
                    </p>
                </div>
                <div className="space-y-2">
                    <div className="flex items-center gap-1.5">
                        <label className="text-sm font-medium">Output Sample Rate (Hz)</label>
                        <HelpTooltip
                            content={
                                <>
                                    <strong>Output Sample Rate</strong> — sample rate of synthesized audio returned from ElevenLabs.
                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                        <li><strong>8000 Hz</strong> — native telephony rate (μ-law @ 8 kHz); least transcoding, lowest bandwidth, slightly lower fidelity.</li>
                                        <li><strong>16000 Hz</strong> — good balance; resampled to 8 kHz by Asterisk for the SIP leg.</li>
                                        <li><strong>22050 Hz</strong> or higher — best quality, but extra resampling overhead for a phone call.</li>
                                    </ul>
                                    For voice calls, 16000 Hz is the typical sweet spot.
                                </>
                            }
                        />
                    </div>
                    <input
                        type="number"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.output_sample_rate_hz || 16000}
                        onChange={(e) => handleChange('output_sample_rate_hz', parseInt(e.target.value))}
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
                <HelpTooltip
                    content={
                        <>
                            <strong>Enabled</strong> — when off, this provider entry is loaded but skipped at call time. Useful for keeping a configured provider around without routing traffic to it (e.g. during A/B tests or while debugging another provider).
                        </>
                    }
                />
            </div>

            <div className="space-y-2">
                <div className="flex items-center gap-1.5">
                    <label className="text-sm font-medium">Farewell Hangup Delay (seconds)</label>
                    <HelpTooltip
                        content={
                            <>
                                <strong>Farewell Hangup Delay</strong> — how long to wait after the agent's final goodbye finishes playing before tearing down the call.
                                <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                    <li>Too short — Asterisk cuts off the last syllable of the farewell.</li>
                                    <li>Too long — caller sits in silence wondering if the line dropped.</li>
                                    <li><strong>2.5 s</strong> (global default) — works for most voices; ElevenLabs is slightly slower than realtime engines so 2.5-3.5 s is often safer here.</li>
                                    <li>Leave empty to inherit the global default from System settings.</li>
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
        </div>
    );
};

export default ElevenLabsProviderForm;
