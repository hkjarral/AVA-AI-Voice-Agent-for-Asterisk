import React, { useState, useEffect } from 'react';
import { FormInput, FormLabel, FormSwitch, FormSelect } from '../ui/FormComponents';
import { ensureModularKey, isFullAgentProvider, isRegisteredProvider, capabilityFromKey } from '../../utils/providerNaming';
import { CheckCircle, AlertCircle, Loader2 } from 'lucide-react';

interface LocalAIStatus {
    stt_backend?: string;
    stt_model?: string;
    tts_backend?: string;
    tts_voice?: string;
    llm_model?: string;
    healthy?: boolean;
}

interface PipelineFormProps {
    config: any;
    providers: any;
    onChange: (newConfig: any) => void;
    isNew?: boolean;
}

const parseMarkerList = (value: string) =>
    (value || '')
        .split('\n')
        .map((line) => line.trim())
        .filter((line) => line.length > 0);

const renderMarkerList = (value: any) =>
    (Array.isArray(value) ? value : []).join('\n');

const PipelineForm: React.FC<PipelineFormProps> = ({ config, providers, onChange, isNew }) => {
    const [localConfig, setLocalConfig] = useState<any>({ ...config });
    const [localAIStatus, setLocalAIStatus] = useState<LocalAIStatus | null>(null);
    const [statusLoading, setStatusLoading] = useState(false);
    const [showAdvancedSTT, setShowAdvancedSTT] = useState(false);
    const [showLlmExpert, setShowLlmExpert] = useState<boolean>(
        () => config?.options?.llm?.tools_enabled !== undefined || Boolean(config?.options?.llm?.realtime_model)
    );
    const [showSttExpert, setShowSttExpert] = useState<boolean>(
        () => Array.isArray(config?.options?.stt?.timestamp_granularities) && config.options.stt.timestamp_granularities.length > 0
    );
    const [showTtsExpert, setShowTtsExpert] = useState<boolean>(
        () => config?.options?.tts?.response_format !== undefined || config?.options?.tts?.max_input_chars !== undefined
    );

    // Fetch local AI server status for backend info (AAVA-116)
    useEffect(() => {
        const fetchLocalAIStatus = async () => {
            setStatusLoading(true);
            try {
                const response = await fetch('/api/local-ai/status');
                if (response.ok) {
                    const data = await response.json();
                    setLocalAIStatus(data);
                }
            } catch (error) {
                console.error('Failed to fetch local AI status:', error);
            } finally {
                setStatusLoading(false);
            }
        };
        fetchLocalAIStatus();
    }, []);

    useEffect(() => {
        setLocalConfig({ ...config });
    }, [config]);

    useEffect(() => {
        if (config?.options?.llm?.tools_enabled !== undefined || config?.options?.llm?.realtime_model) {
            setShowLlmExpert(true);
        }
    }, [config?.options?.llm?.tools_enabled, config?.options?.llm?.realtime_model]);

    useEffect(() => {
        if (Array.isArray(config?.options?.stt?.timestamp_granularities) && config.options.stt.timestamp_granularities.length > 0) {
            setShowSttExpert(true);
        }
    }, [config?.options?.stt?.timestamp_granularities]);

    useEffect(() => {
        if (config?.options?.tts?.response_format !== undefined || config?.options?.tts?.max_input_chars !== undefined) {
            setShowTtsExpert(true);
        }
    }, [config?.options?.tts?.response_format, config?.options?.tts?.max_input_chars]);

    const updateConfig = (updates: any) => {
        const newConfig = { ...localConfig, ...updates };
        setLocalConfig(newConfig);
        onChange(newConfig);
    };

    const updateSTTOptions = (updates: any) => {
        const existingOptions = localConfig.options || {};
        const existingSTT = existingOptions.stt || {};
        const nextSTT = { ...existingSTT, ...updates };
        updateConfig({ options: { ...existingOptions, stt: nextSTT } });
    };

    const updateRoleOptions = (role: 'stt' | 'llm' | 'tts', updates: any) => {
        const existingOptions = localConfig.options || {};
        const existingRole = existingOptions[role] || {};
        const nextRole = { ...existingRole, ...updates };
        updateConfig({ options: { ...existingOptions, [role]: nextRole } });
    };

    const setRoleOptions = (role: 'stt' | 'llm' | 'tts', nextRole: any) => {
        const existingOptions = localConfig.options || {};
        const nextOptions = { ...existingOptions };
        const roleObj = (nextRole && typeof nextRole === 'object') ? nextRole : {};
        if (Object.keys(roleObj).length === 0) {
            delete nextOptions[role];
        } else {
            nextOptions[role] = roleObj;
        }
        updateConfig({ options: nextOptions });
    };

    // Helper to filter providers by capability
    // Prefer capabilities array (authoritative). For legacy configs missing capabilities, infer from key suffix.
    // Only show registered providers that have engine adapter support.
    const getProvidersByCapability = (cap: 'stt' | 'llm' | 'tts', selectedProvider?: string) => {
        const base = Object.entries(providers || {})
            .filter(([providerKey, p]: [string, any]) => {
                // Exclude Full Agents from modular slots
                if (isFullAgentProvider(p)) return false;

                // Exclude unregistered providers (no engine adapter)
                if (!isRegisteredProvider(p)) return false;

                // Hide disabled providers from choices (but keep them visible if currently selected).
                if (p.enabled === false) return false;

                const caps = Array.isArray(p.capabilities) ? p.capabilities : [];
                if (caps.length > 0) {
                    return caps.includes(cap);
                }

                // Legacy: infer from provider key suffix (e.g., openai_stt/openai_llm/openai_tts).
                // This keeps pipelines editable even if capabilities haven't been persisted yet.
                return capabilityFromKey(providerKey) === cap;
            })
            .map(([name, p]: [string, any]) => ({
                value: name,
                label: (Array.isArray(p.capabilities) && p.capabilities.length > 0) ? name : `${name} (inferred)`,
                disabled: false
            }));

        // If the current pipeline references a disabled provider, keep it visible as the selected value
        // so users understand why audio may be failing.
        if (selectedProvider && !base.some((p) => p.value === selectedProvider)) {
            const selectedCfg = providers?.[selectedProvider];
            if (selectedCfg && selectedCfg.enabled === false) {
                const caps = Array.isArray(selectedCfg.capabilities) ? selectedCfg.capabilities : [];
                const matches =
                    (caps.length > 0 && caps.includes(cap)) ||
                    (caps.length === 0 && capabilityFromKey(selectedProvider) === cap);
                if (matches) {
                    base.unshift({ value: selectedProvider, label: `${selectedProvider} (Disabled)`, disabled: true });
                }
            }
        }

        return base;
    };

    const sttProviders = getProvidersByCapability('stt', localConfig.stt);
    const llmProviders = getProvidersByCapability('llm', localConfig.llm);
    const ttsProviders = getProvidersByCapability('tts', localConfig.tts);

    const handleProviderChange = (cap: 'stt' | 'llm' | 'tts', value: string) => {
        if (!value) {
            // If a component is cleared, also clear its option overrides (otherwise stale base_url/model can linger).
            const existingOptions = localConfig.options || {};
            const nextOptions = { ...existingOptions };
            if (cap === 'llm' && nextOptions.llm) {
                delete nextOptions.llm;
            }
            updateConfig({ [cap]: '', options: nextOptions });
            return;
        }
        const normalized = ensureModularKey(value, cap);

        // IMPORTANT: When switching LLM providers, clear any pipeline-level LLM overrides.
        // Otherwise, users can end up with an Ollama adapter pointed at an OpenAI base_url (causing 404s).
        const updates: any = { [cap]: normalized };
        if (cap === 'llm' && normalized !== localConfig.llm) {
            const existingOptions = localConfig.options || {};
            const nextOptions = { ...existingOptions };
            if (nextOptions.llm) {
                delete nextOptions.llm;
            }
            updates.options = nextOptions;
        }

        updateConfig(updates);
    };

    const sttKey = String(localConfig.stt || '').toLowerCase();
    const llmKey = String(localConfig.llm || '').toLowerCase();
    const ttsKey = String(localConfig.tts || '').toLowerCase();

    const isOpenAIStt = sttKey.includes('openai');
    const isOpenAILlm = llmKey.includes('openai');
    const isOpenAITts = ttsKey.includes('openai');
    const isGroqStt = sttKey.includes('groq');
    const isGroqTts = ttsKey.includes('groq');
    const isOllamaLlm = llmKey.includes('ollama');

    const timestampGranularities = Array.isArray(localConfig.options?.stt?.timestamp_granularities)
        ? localConfig.options?.stt?.timestamp_granularities
        : [];
    const timestampGranularitiesText = timestampGranularities.join(', ');

    const guardrailEnabledValue =
        localConfig.options?.llm?.hangup_call_guardrail === true
            ? 'true'
            : localConfig.options?.llm?.hangup_call_guardrail === false
                ? 'false'
                : '';

    const guardrailModeValue = String(localConfig.options?.llm?.hangup_call_guardrail_mode || '');
    const guardrailMarkersValue = localConfig.options?.llm?.hangup_call_guardrail_markers?.end_call;
    const guardrailMarkersText = renderMarkerList(guardrailMarkersValue);
    const [guardrailMarkersDraft, setGuardrailMarkersDraft] = useState<string>(guardrailMarkersText);

    useEffect(() => {
        setGuardrailMarkersDraft(guardrailMarkersText);
    }, [guardrailMarkersText]);

    return (
        <div className="space-y-6">
            <div className="space-y-4 border-b border-border pb-6">
                <h4 className="font-semibold">Pipeline Identity</h4>
                <FormInput
                    label="Pipeline Name"
                    value={localConfig.name || ''}
                    onChange={(e) => updateConfig({ name: e.target.value })}
                    placeholder="e.g., english_support"
                    disabled={!isNew}
                    tooltip="Unique identifier for this pipeline."
                />
            </div>

            <div className="space-y-4">
                <h4 className="font-semibold">Components</h4>

                <div className="space-y-2">
                    <FormLabel>Speech-to-Text (STT)</FormLabel>
                    <select
                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                        value={localConfig.stt || ''}
                        onChange={(e) => handleProviderChange('stt', e.target.value)}
                    >
                        <option value="">Select STT Provider...</option>
                        {sttProviders.map(p => (
                            <option key={p.value} value={p.value} disabled={p.disabled}>
                                {p.label} {p.disabled ? '(Disabled)' : ''}
                            </option>
                        ))}
                    </select>
                    {/* AAVA-116: Show active backend for local_stt */}
                    {localConfig.stt?.includes('local') && localAIStatus && (
                        <div className="flex items-center gap-2 text-xs text-muted-foreground bg-muted/50 px-3 py-2 rounded-md">
                            {statusLoading ? (
                                <Loader2 className="h-3 w-3 animate-spin" />
                            ) : localAIStatus.healthy ? (
                                <CheckCircle className="h-3 w-3 text-green-500" />
                            ) : (
                                <AlertCircle className="h-3 w-3 text-yellow-500" />
                            )}
                            <span>
                                Active Backend: <strong className="text-foreground">{localAIStatus.stt_backend || 'Unknown'}</strong>
                                {localAIStatus.stt_model && <span className="text-muted-foreground"> ({localAIStatus.stt_model})</span>}
                            </span>
                        </div>
                    )}
                    {sttProviders.length === 0 && (
                        <p className="text-xs text-destructive">No STT providers available. Create a modular STT provider first.</p>
                    )}
                </div>

                <div className="space-y-3">
                    <FormSwitch
                        id="pipeline-stt-streaming"
                        label="Streaming STT"
                        checked={localConfig.options?.stt?.streaming ?? true}
                        onChange={(e) => updateSTTOptions({ streaming: e.target.checked })}
                        description="Recommended. Enables low-latency, two-way conversation."
                        tooltip="When enabled, supported STT adapters stream audio continuously. When disabled, STT runs in buffered chunk mode."
                    />

                    <div className="flex items-center justify-between">
                        <button
                            type="button"
                            className="text-xs text-primary hover:underline"
                            onClick={() => setShowAdvancedSTT((v) => !v)}
                        >
                            {showAdvancedSTT ? 'Hide Advanced' : 'Show Advanced'}
                        </button>
                        <div className="text-xs text-muted-foreground">
                            Defaults: chunk_ms=160, stream_format=pcm16_16k
                        </div>
                    </div>

                    {showAdvancedSTT && (
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <FormInput
                                label="chunk_ms"
                                type="number"
                                value={localConfig.options?.stt?.chunk_ms ?? 160}
                                onChange={(e) => updateSTTOptions({ chunk_ms: parseInt(e.target.value || '160', 10) })}
                                tooltip="How often we flush accumulated audio frames to the STT streaming sender. 160ms is a good default."
                            />
                            <FormInput
                                label="stream_format"
                                value={localConfig.options?.stt?.stream_format ?? 'pcm16_16k'}
                                onChange={(e) => updateSTTOptions({ stream_format: e.target.value })}
                                tooltip="Input audio format for streaming STT. For Local STT this should usually be pcm16_16k."
                            />
                        </div>
                    )}
                </div>

                <div className="space-y-2">
                    <FormLabel>Large Language Model (LLM)</FormLabel>
                    <select
                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                        value={localConfig.llm || ''}
                        onChange={(e) => handleProviderChange('llm', e.target.value)}
                    >
                        <option value="">Select LLM Provider...</option>
                        {llmProviders.map(p => (
                            <option key={p.value} value={p.value} disabled={p.disabled}>
                                {p.label} {p.disabled ? '(Disabled)' : ''}
                            </option>
                        ))}
                    </select>
                    {llmProviders.length === 0 && (
                        <p className="text-xs text-destructive">No LLM providers available. Create a modular LLM provider first.</p>
                    )}
                </div>

                <div className="space-y-2">
                    <FormLabel>Text-to-Speech (TTS)</FormLabel>
                    <select
                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                        value={localConfig.tts || ''}
                        onChange={(e) => handleProviderChange('tts', e.target.value)}
                    >
                        <option value="">Select TTS Provider...</option>
                        {ttsProviders.map(p => (
                            <option key={p.value} value={p.value} disabled={p.disabled}>
                                {p.label} {p.disabled ? '(Disabled)' : ''}
                            </option>
                        ))}
                    </select>
                    {/* AAVA-116: Show active backend for local_tts */}
                    {localConfig.tts?.includes('local') && localAIStatus && (
                        <div className="flex items-center gap-2 text-xs text-muted-foreground bg-muted/50 px-3 py-2 rounded-md">
                            {statusLoading ? (
                                <Loader2 className="h-3 w-3 animate-spin" />
                            ) : localAIStatus.healthy ? (
                                <CheckCircle className="h-3 w-3 text-green-500" />
                            ) : (
                                <AlertCircle className="h-3 w-3 text-yellow-500" />
                            )}
                            <span>
                                Active Backend: <strong className="text-foreground">{localAIStatus.tts_backend || 'Unknown'}</strong>
                                {localAIStatus.tts_voice && <span className="text-muted-foreground"> ({localAIStatus.tts_voice})</span>}
                            </span>
                        </div>
                    )}
                    {ttsProviders.length === 0 && (
                        <p className="text-xs text-destructive">No TTS providers available. Create a modular TTS provider first.</p>
                    )}
                </div>
            </div>

            <div className="space-y-4 border-t border-border pt-6">
                {(isOpenAILlm || isOllamaLlm) && (
                    <div className="space-y-3 border border-amber-300/40 rounded-lg p-4 bg-amber-500/5">
                        <FormSwitch
                            label="LLM Expert Settings"
                            description="Expose high-impact LLM adapter overrides."
                            checked={showLlmExpert}
                            onChange={(e) => setShowLlmExpert(e.target.checked)}
                            className="mb-0 border-0 p-0 bg-transparent"
                        />
                        <p className={`text-xs ${showLlmExpert ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'}`}>
                            {showLlmExpert
                                ? 'Warning: LLM expert overrides can break tool-calling behavior if they diverge from provider defaults.'
                                : 'Expert values are visible and read-only until LLM expert mode is enabled.'}
                        </p>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <FormSwitch
                                label="LLM Tools Enabled"
                                description="Allow tool calls at the pipeline adapter level."
                                checked={localConfig.options?.llm?.tools_enabled ?? true}
                                onChange={(e) => updateRoleOptions('llm', { tools_enabled: e.target.checked })}
                                disabled={!showLlmExpert}
                            />
                            {isOpenAILlm && (
                                <FormInput
                                    label="OpenAI Realtime Model"
                                    value={localConfig.options?.llm?.realtime_model || ''}
                                    onChange={(e) => updateRoleOptions('llm', { realtime_model: e.target.value })}
                                    placeholder="gpt-4o-realtime-preview-2024-12-17"
                                    tooltip="Adapter-level realtime model override for OpenAI pipeline LLM."
                                    disabled={!showLlmExpert}
                                />
                            )}
                        </div>
                        <div className="mt-2 border-t border-amber-300/30 pt-3 space-y-3">
                            <p className="text-xs text-muted-foreground">
                                Hangup guardrails apply to pipeline LLMs that emit <code>hangup_call</code> too eagerly. These settings are per-pipeline and do not affect full-agent providers like Google Live.
                            </p>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <FormSelect
                                    label="Hangup Call Guardrail"
                                    value={guardrailEnabledValue}
                                    onChange={(e) => {
                                        const v = String(e.target.value || '');
                                        if (!v) {
                                            const next = { ...(localConfig.options?.llm || {}) };
                                            delete next.hangup_call_guardrail;
                                            setRoleOptions('llm', next);
                                            return;
                                        }
                                        updateRoleOptions('llm', { hangup_call_guardrail: v === 'true' });
                                    }}
                                    tooltip="Auto: enabled only for specific adapters (e.g., Ollama) unless explicitly set. When enabled, hangup_call is allowed only if user end-of-call intent is detected from text."
                                    options={[
                                        { value: '', label: 'Auto (default)' },
                                        { value: 'true', label: 'Enabled' },
                                        { value: 'false', label: 'Disabled' },
                                    ]}
                                    disabled={!showLlmExpert}
                                />
                                <FormSelect
                                    label="Hangup Guardrail Mode"
                                    value={guardrailModeValue}
                                    onChange={(e) => {
                                        const v = String(e.target.value || '');
                                        if (!v) {
                                            const next = { ...(localConfig.options?.llm || {}) };
                                            delete next.hangup_call_guardrail_mode;
                                            setRoleOptions('llm', next);
                                            return;
                                        }
                                        updateRoleOptions('llm', { hangup_call_guardrail_mode: v });
                                    }}
                                    tooltip="Auto uses the global hangup policy mode. Relaxed disables the guardrail, Strict forces it on, Normal uses adapter defaults unless explicitly enabled/disabled above."
                                    options={[
                                        { value: '', label: 'Auto (global)' },
                                        { value: 'relaxed', label: 'Relaxed' },
                                        { value: 'normal', label: 'Normal' },
                                        { value: 'strict', label: 'Strict' },
                                    ]}
                                    disabled={!showLlmExpert}
                                />
                            </div>
                            <div className="space-y-2">
                                <FormLabel tooltip="Per-pipeline override list of caller phrases that indicate they want to end the call. Leave empty to use the global defaults from the Hangup tool policy.">
                                    End-Call Intent Markers (Override)
                                </FormLabel>
                                <textarea
                                    className="w-full p-2 rounded border border-input bg-background text-sm min-h-[120px] disabled:cursor-not-allowed disabled:opacity-50"
                                    value={guardrailMarkersDraft}
                                    onChange={(e) => {
                                        setGuardrailMarkersDraft(e.target.value);
                                    }}
                                    onBlur={() => {
                                        const items = parseMarkerList(guardrailMarkersDraft);
                                        if (items.length === 0) {
                                            const next = { ...(localConfig.options?.llm || {}) };
                                            if (next.hangup_call_guardrail_markers && typeof next.hangup_call_guardrail_markers === 'object') {
                                                const nextMarkers = { ...(next.hangup_call_guardrail_markers || {}) };
                                                delete nextMarkers.end_call;
                                                if (Object.keys(nextMarkers).length === 0) {
                                                    delete next.hangup_call_guardrail_markers;
                                                } else {
                                                    next.hangup_call_guardrail_markers = nextMarkers;
                                                }
                                            }
                                            setRoleOptions('llm', next);
                                            return;
                                        }
                                        updateRoleOptions('llm', {
                                            hangup_call_guardrail_markers: {
                                                ...(localConfig.options?.llm?.hangup_call_guardrail_markers || {}),
                                                end_call: items,
                                            },
                                        });
                                    }}
                                    disabled={!showLlmExpert}
                                />
                                <p className="text-xs text-muted-foreground">
                                    One phrase per line. Keep this list short to reduce false positives.
                                </p>
                            </div>
                        </div>
                    </div>
                )}

                {(isOpenAIStt || isGroqStt) && (
                    <div className="space-y-3 border border-amber-300/40 rounded-lg p-4 bg-amber-500/5">
                        <FormSwitch
                            label="STT Expert Settings"
                            description="Expose advanced STT adapter timestamp options."
                            checked={showSttExpert}
                            onChange={(e) => setShowSttExpert(e.target.checked)}
                            className="mb-0 border-0 p-0 bg-transparent"
                        />
                        <p className={`text-xs ${showSttExpert ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'}`}>
                            {showSttExpert
                                ? 'Warning: unsupported timestamp settings can fail transcription requests on some models.'
                                : 'Expert values are visible and read-only until STT expert mode is enabled.'}
                        </p>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <FormInput
                                label="STT Timestamp Granularities"
                                value={timestampGranularitiesText}
                                onChange={(e) =>
                                    updateRoleOptions('stt', {
                                        timestamp_granularities: (e.target.value || '')
                                            .split(',')
                                            .map((v) => v.trim())
                                            .filter(Boolean),
                                    })
                                }
                                placeholder="segment, word"
                                tooltip="Comma-separated; only supported on specific models/endpoints."
                                disabled={!showSttExpert}
                            />
                        </div>
                    </div>
                )}

                {(isOpenAITts || isGroqTts) && (
                    <div className="space-y-3 border border-amber-300/40 rounded-lg p-4 bg-amber-500/5">
                        <FormSwitch
                            label="TTS Expert Settings"
                            description="Expose provider-specific TTS adapter overrides."
                            checked={showTtsExpert}
                            onChange={(e) => setShowTtsExpert(e.target.checked)}
                            className="mb-0 border-0 p-0 bg-transparent"
                        />
                        <p className={`text-xs ${showTtsExpert ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'}`}>
                            {showTtsExpert
                                ? 'Warning: TTS expert overrides can change output encoding/chunking and impact call playback.'
                                : 'Expert values are visible and read-only until TTS expert mode is enabled.'}
                        </p>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            {isOpenAITts && (
                                <FormInput
                                    label="OpenAI TTS Response Format"
                                    value={localConfig.options?.tts?.response_format || ''}
                                    onChange={(e) => updateRoleOptions('tts', { response_format: e.target.value })}
                                    placeholder="wav"
                                    tooltip="Adapter response format (e.g., wav, pcm)."
                                    disabled={!showTtsExpert}
                                />
                            )}
                            {isGroqTts && (
                                <FormInput
                                    label="Groq TTS Max Input Chars"
                                    type="number"
                                    value={localConfig.options?.tts?.max_input_chars ?? 200}
                                    onChange={(e) => updateRoleOptions('tts', { max_input_chars: parseInt(e.target.value || '200', 10) })}
                                    tooltip="Max characters per TTS chunk before adapter splits text."
                                    disabled={!showTtsExpert}
                                />
                            )}
                        </div>
                    </div>
                )}
            </div>

        </div>
    );
};

export default PipelineForm;
