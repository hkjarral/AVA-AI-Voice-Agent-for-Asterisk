import React, { useState, useEffect } from 'react';
import { FormInput, FormLabel } from '../ui/FormComponents';
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

const PipelineForm: React.FC<PipelineFormProps> = ({ config, providers, onChange, isNew }) => {
    const [localConfig, setLocalConfig] = useState<any>({ ...config });
    const [localAIStatus, setLocalAIStatus] = useState<LocalAIStatus | null>(null);
    const [statusLoading, setStatusLoading] = useState(false);

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

    const updateConfig = (updates: any) => {
        const newConfig = { ...localConfig, ...updates };
        setLocalConfig(newConfig);
        onChange(newConfig);
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
            updateConfig({ [cap]: '' });
            return;
        }
        const normalized = ensureModularKey(value, cap);
        updateConfig({ [cap]: normalized });
    };

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

        </div>
    );
};

export default PipelineForm;
