import React, { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { Plus, Trash2, AlertTriangle } from 'lucide-react';
import HelpTooltip from '../../ui/HelpTooltip';
import { FormInput, FormSelect, FormLabel, FormSwitch } from '../../ui/FormComponents';
import { Capability, ensureModularKey, isRegisteredProvider, getUnregisteredReason, REGISTERED_PROVIDER_TYPES } from '../../../utils/providerNaming';
import { GOOGLE_LIVE_MODEL_OPTIONS } from '../../../utils/googleLiveModels';

interface GenericProviderFormProps {
    config: any;
    onChange: (newConfig: any) => void;
    isNew?: boolean;
}

const PROVIDER_TYPES = [
    { value: 'full', label: 'Full Agent (STT+LLM+TTS)' },
    { value: 'elevenlabs', label: 'ElevenLabs TTS / Agent' },
    { value: 'modular', label: 'Modular (Single Capability)' },
];

const CAPABILITIES: { value: Capability; label: string }[] = [
    { value: 'stt', label: 'Speech-to-Text (STT)' },
    { value: 'llm', label: 'Large Language Model (LLM)' },
    { value: 'tts', label: 'Text-to-Speech (TTS)' },
];

const GOOGLE_LIVE_SUGGESTED_MODELS = GOOGLE_LIVE_MODEL_OPTIONS.map((modelOption) => modelOption.value);

const PROVIDER_OPTIONS: Record<string, Record<string, string[]>> = {
    deepgram: {
        model: ['nova-2', 'nova-2-general', 'nova-2-meeting', 'enhanced', 'base'],
        stt_model: ['nova-2', 'nova-2-general', 'nova-2-meeting', 'enhanced', 'base'],
        tts_model: ['aura-asteria-en', 'aura-luna-en', 'aura-orion-en', 'aura-arcas-en', 'aura-perseus-en', 'aura-angus-en', 'aura-orpheus-en', 'aura-helios-en', 'aura-zeus-en'],
    },
    openai: {
        model: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo'],
        llm_model: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo'],
        // STT models per OpenAI Speech-to-Text guide.
        stt_model: [
            'whisper-1',
            'gpt-4o-mini-transcribe',
            'gpt-4o-mini-transcribe-2025-12-15',
            'gpt-4o-transcribe',
            'gpt-4o-transcribe-diarize',
        ],
        tts_model: ['gpt-4o-mini-tts', 'gpt-4o-mini-tts-2025-12-15', 'tts-1', 'tts-1-hd'],
        // OpenAI audio.speech voices (validated by API). Keep this aligned with engine validation.
        voice: ['alloy', 'ash', 'ballad', 'coral', 'echo', 'fable', 'nova', 'onyx', 'sage', 'shimmer', 'verse', 'marin', 'cedar'],
        tts_voice: ['alloy', 'ash', 'ballad', 'coral', 'echo', 'fable', 'nova', 'onyx', 'sage', 'shimmer', 'verse', 'marin', 'cedar'],
        response_format: ['wav', 'pcm'],
    },
    groq: {
        stt_model: ['whisper-large-v3-turbo', 'whisper-large-v3'],
        response_format: ['json', 'verbose_json', 'text', 'wav'],
        tts_model: ['canopylabs/orpheus-v1-english', 'canopylabs/orpheus-arabic-saudi'],
        voice: ['autumn', 'diana', 'hannah', 'austin', 'daniel', 'troy', 'fahad', 'sultan', 'lulwa', 'noura'],
    },
    openai_realtime: {
        model: ['gpt-4o-realtime-preview', 'gpt-4o-realtime-preview-2024-10-01'],
        voice: ['alloy', 'echo', 'shimmer', 'ash', 'ballad', 'coral', 'sage', 'verse'],
    },
    google_live: {
        model: GOOGLE_LIVE_SUGGESTED_MODELS,
        llm_model: GOOGLE_LIVE_SUGGESTED_MODELS,
        tts_voice_name: ['Puck', 'Charon', 'Kore', 'Fenrir', 'Aoede', 'Leda', 'Orus', 'Zephyr'],
    },
};

const GenericProviderForm: React.FC<GenericProviderFormProps> = ({ config, onChange, isNew }) => {
    const [customFields, setCustomFields] = useState<{ key: string; value: string }[]>([]);
    const [nameLocked, setNameLocked] = useState<boolean>(false);

    // Initialize custom fields from config on mount
    useEffect(() => {
        const initialFields: { key: string; value: string }[] = [];
        const knownKeys = ['name', 'type', 'base_url', 'base_url_stt', 'base_url_tts', 'base_url_llm', 'capabilities', 'enabled'];

        Object.entries(config).forEach(([key, value]) => {
            if (!knownKeys.includes(key) && typeof value !== 'object') {
                initialFields.push({ key, value: String(value) });
            }
        });

        if (isNew && (config.type === 'full' || !config.type) && !initialFields.some(f => f.key === 'continuous_input')) {
            initialFields.push({ key: 'continuous_input', value: 'true' });
        }

        setCustomFields(initialFields);
        // Lock name for existing modular providers
        if (!isNew && config.capabilities && config.capabilities.length === 1 && (config.type || 'modular') !== 'full') {
            setNameLocked(true);
        }
    }, []); // Run once on mount

    useEffect(() => {
        // Lock name when a modular capability is present (only after save/initial load)
        if (!isNew && (config.type || 'modular') !== 'full' && Array.isArray(config.capabilities) && config.capabilities.length === 1) {
            setNameLocked(true);
        }
        // Ensure full agents always carry continuous_input: true (as config hint, shown in fields section)
        if ((config.type || '') === 'full' && config.continuous_input !== true) {
            updateConfig({ continuous_input: true });
        }
    }, [config.type, config.capabilities]);

    const getBaseConfig = () => {
        const knownKeys = ['name', 'type', 'base_url', 'base_url_stt', 'base_url_tts', 'base_url_llm', 'capabilities', 'enabled'];
        const base: any = {};
        knownKeys.forEach(key => {
            if (config[key] !== undefined) {
                base[key] = config[key];
            }
        });
        return base;
    };

    const updateConfig = (updates: any) => {
        // Start with base config (known keys only)
        const baseConfig = getBaseConfig();
        const updatedConfig = { ...baseConfig, ...updates };

        // Merge custom fields
        customFields.forEach(field => {
            if (field.key) {
                let val: any = field.value;
                if (val === 'true') val = true;
                else if (val === 'false') val = false;
                else if (!isNaN(Number(val)) && val !== '') val = Number(val);
                updatedConfig[field.key] = val;
            }
        });

        onChange(updatedConfig);
    };

    const handleTypeChange = (type: string) => {
        let updates: any = { type };
        let newFields = [...customFields];

        if (type === 'full') {
            updates.capabilities = ['stt', 'llm', 'tts'];
            if (!newFields.some(f => f.key === 'continuous_input')) {
                newFields.unshift({ key: 'continuous_input', value: 'true' });
            }
            setNameLocked(false);
        } else {
            updates.capabilities = [];
            // remove continuous_input from custom fields for modular
            newFields = newFields.filter(f => f.key !== 'continuous_input');
            setNameLocked(false);
        }

        setCustomFields(newFields);
        updateConfig(updates);
    };

    const handleCapabilityChange = (cap: Capability) => {
        // If editing existing modular provider, do not allow capability change
        if (!isNew && config.capabilities && config.capabilities.length === 1 && config.type !== 'full') {
            toast.error('Modular capability cannot be changed after save. Please delete and recreate the provider with the desired capability.');
            return;
        }
        // Require a base name before selecting capability
        if (!config.name || config.name.trim() === '') {
            toast.error('Please enter a provider name before selecting a capability.');
            return;
        }
        const updates: any = { capabilities: [cap] };
        const base = (config.name || '').replace(/_(stt|llm|tts)$/i, '');
        const normalized = ensureModularKey(base || config.name || '', cap);
        updates.name = normalized;
        // Lock name once a capability is chosen (still allow programmatic suffix swap)
        setNameLocked(true);
        updateConfig(updates);
    };

    const handleFieldChange = (index: number, field: 'key' | 'value', value: string) => {
        const newFields = [...customFields];
        newFields[index][field] = value;
        setCustomFields(newFields);

        // Propagate changes to parent immediately
        if (newFields[index].key || field === 'key') { // Update if key exists or we are changing the key
            const baseConfig = getBaseConfig();
            const customProps: any = {};
            newFields.forEach(f => {
                if (f.key) {
                    let v: any = f.value;
                    if (v === 'true') v = true;
                    else if (v === 'false') v = false;
                    else if (!isNaN(Number(v)) && v !== '') v = Number(v);
                    customProps[f.key] = v;
                }
            });
            onChange({ ...baseConfig, ...customProps });
        }
    };

    const addField = () => {
        if (customFields.length < 10) {
            setCustomFields([...customFields, { key: '', value: '' }]);
        }
    };

    const removeField = (index: number) => {
        const fieldToRemove = customFields[index];
        const newFields = customFields.filter((_, i) => i !== index);
        setCustomFields(newFields);

        // Remove key from config
        if (fieldToRemove.key) {
            const newConfig = { ...config };
            delete newConfig[fieldToRemove.key];

            // Re-apply remaining fields to be safe
            newFields.forEach(f => {
                if (f.key) {
                    let v: any = f.value;
                    if (v === 'true') v = true;
                    else if (v === 'false') v = false;
                    else if (!isNaN(Number(v)) && v !== '') v = Number(v);
                    newConfig[f.key] = v;
                }
            });

            onChange(newConfig);
        }
    };

    // Helper to get options for a specific key based on provider name/type
    const getOptionsForKey = (key: string) => {
        // Try to infer provider from name or type
        const name = (config.name || '').toLowerCase();
        let providerKey = '';

        if (name.includes('openai') && name.includes('realtime')) providerKey = 'openai_realtime';
        else if (name.includes('openai')) providerKey = 'openai';
        else if (name.includes('groq')) providerKey = 'groq';
        else if (name.includes('deepgram')) providerKey = 'deepgram';
        else if (name.includes('google') || name.includes('gemini')) providerKey = 'google_live';

        // If we identified a provider, ONLY show options for that provider
        if (providerKey && PROVIDER_OPTIONS[providerKey]) {
            return PROVIDER_OPTIONS[providerKey][key] || null;
        }

        // Only fallback to all options if we couldn't identify the provider
        // This prevents "deepgram" showing "gpt" models if the key is just "model"
        const allOptions = new Set<string>();
        Object.values(PROVIDER_OPTIONS).forEach(p => {
            if (p[key]) {
                p[key].forEach(opt => allOptions.add(opt));
            }
        });

        if (allOptions.size > 0) return Array.from(allOptions);
        return null;
    };

    return (
        <div className="space-y-8">
            {/* Core Identity */}
            <div className="space-y-4 border-b border-border pb-6">
                <div className="flex justify-between items-start">
                    <h4 className="font-semibold flex items-center gap-2">
                        Provider Details
                        <HelpTooltip content="Basic identification for this provider." />
                    </h4>
                    <div className="w-32">
                        <FormSwitch
                            label="Enabled"
                            checked={config.enabled ?? true}
                            onChange={(e) => updateConfig({ enabled: e.target.checked })}
                        />
                    </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <FormInput
                        label="Provider Name"
                        value={config.name || ''}
                        onChange={(e) => updateConfig({ name: (e.target.value || '').toLowerCase() })}
                        placeholder={config.type === 'full' ? "e.g., my-openai-agent" : "e.g., my_provider_stt"}
                        disabled={!isNew || (config.type !== 'full' && nameLocked)}
                        tooltip={config.type === 'full' ? "Unique identifier for this agent." : "Suffix (_stt, _llm, _tts) is enforced after capability selection."}
                    />

                    <FormSelect
                        label="Provider Type"
                        options={PROVIDER_TYPES}
                        value={config.type === 'full' ? 'full' : 'modular'}
                        onChange={(e) => handleTypeChange(e.target.value)}
                        tooltip="Full Agent = all-in-one; Modular = single capability (STT/LLM/TTS)."
                    />
                </div>

                <div className="space-y-2">
                    <FormLabel>Capabilities</FormLabel>
                    <div className="flex gap-4">
                        {config.type === 'full' ? (
                            CAPABILITIES.map(cap => (
                                <label key={cap.value} className="flex items-center space-x-2 opacity-70 cursor-not-allowed">
                                    <input type="checkbox" className="rounded border-input" checked disabled />
                                    <span className="text-sm">{cap.label}</span>
                                </label>
                            ))
                        ) : (
                            CAPABILITIES.map(cap => {
                                const selected = (config.capabilities || []).includes(cap.value);
                                const lockChange = !isNew && (config.capabilities || []).length === 1;
                                return (
                                    <label key={cap.value} className={`flex items-center space-x-2 ${lockChange ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer'}`}>
                                        <input
                                            type="checkbox"
                                            className="rounded border-input text-primary focus:ring-primary"
                                            checked={selected}
                                            disabled={lockChange}
                                            onChange={() => handleCapabilityChange(cap.value)}
                                        />
                                        <span className="text-sm">{cap.label}</span>
                                    </label>
                                );
                            })
                        )}
                    </div>
                    {config.type !== 'full' && (
                        <p className="text-xs text-muted-foreground mt-1">
                            Select exactly one capability. Name will auto-suffix (e.g., <code>_stt</code>) and then lock.
                        </p>
                    )}
                </div>

                {/* Unregistered Provider Warning */}
                {config.type && !isRegisteredProvider(config) && (
                    <div className="bg-amber-500/10 border border-amber-500/30 text-amber-700 dark:text-amber-400 p-4 rounded-md">
                        <div className="flex items-start gap-3">
                            <AlertTriangle className="w-5 h-5 flex-shrink-0 mt-0.5" />
                            <div className="space-y-2">
                                <p className="font-semibold">Unregistered Provider Type</p>
                                <p className="text-sm">{getUnregisteredReason(config)}</p>
                                <p className="text-sm">
                                    This provider can be saved but <strong>will not work in pipelines</strong> until engine support is added.
                                </p>
                                <p className="text-xs text-amber-600 dark:text-amber-500">
                                    Supported types: {REGISTERED_PROVIDER_TYPES.join(', ')}
                                </p>
                            </div>
                        </div>
                    </div>
                )}

                {/* Groq Tool Calling Warning */}
                {(config.name || '').toLowerCase().includes('groq') && (
                    <div className="bg-amber-500/10 border border-amber-500/30 text-amber-700 dark:text-amber-400 p-4 rounded-md">
                        <div className="flex items-start gap-3">
                            <AlertTriangle className="w-5 h-5 flex-shrink-0 mt-0.5" />
                            <div className="space-y-2">
                                <p className="font-semibold">Groq Tool Calling Limitation</p>
                                <p className="text-sm">
                                    Groq does not support function/tool calling reliably and will return errors if tools are enabled.
                                </p>
                                <p className="text-sm">
                                    Tools are allowlisted per <strong>Context</strong>. If this provider is backed by Groq, keep context tools empty.
                                </p>
                            </div>
                        </div>
                    </div>
                )}
            </div>

            {/* Connection Details */}
            <div className="space-y-4 border-b border-border pb-6">
                <h4 className="font-semibold flex items-center gap-2">
                    Connection Settings
                </h4>

                {(config.type || 'modular') === 'full' ? (
                    <div className="space-y-3">
                        <FormInput
                            label="Base URL / WebSocket URL"
                            value={config.base_url || ''}
                            onChange={(e) => updateConfig({ base_url: e.target.value })}
                            placeholder="wss://api.provider.com/v1/realtime"
                            tooltip="Required for full agents. Used for combined STT/LLM/TTS APIs."
                        />
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {(config.capabilities || []).includes('stt') && (
                            <FormInput
                                label="STT Base URL"
                                value={config.base_url_stt || ''}
                                onChange={(e) => updateConfig({ base_url_stt: e.target.value })}
                                placeholder="https://api.provider.com/stt"
                            />
                        )}
                        {(config.capabilities || []).includes('tts') && (
                            <FormInput
                                label="TTS Base URL"
                                value={config.base_url_tts || ''}
                                onChange={(e) => updateConfig({ base_url_tts: e.target.value })}
                                placeholder="https://api.provider.com/tts"
                            />
                        )}
                        {(config.capabilities || []).includes('llm') && (
                            <FormInput
                                label="LLM Base URL"
                                value={config.base_url_llm || ''}
                                onChange={(e) => updateConfig({ base_url_llm: e.target.value })}
                                placeholder="https://api.provider.com/v1"
                            />
                        )}
                        {(config.capabilities || []).length === 0 && (
                            <p className="text-sm text-muted-foreground">Select a capability to configure base URLs.</p>
                        )}
                    </div>
                )}
            </div>

            {/* Dynamic Configuration */}
            <div className="space-y-4">
                <div className="flex justify-between items-center">
                    <h4 className="font-semibold flex items-center gap-2">
                        Configuration Fields
                        <HelpTooltip content="Add up to 10 custom key-value pairs for provider configuration." />
                    </h4>
                    <button
                        type="button"
                        onClick={addField}
                        disabled={customFields.length >= 10}
                        className="text-xs flex items-center gap-1 text-primary hover:underline disabled:opacity-50"
                    >
                        <Plus className="w-3 h-3" /> Add Field
                    </button>
                </div>

                {config.type !== 'full' && (
                    <div className="text-xs text-muted-foreground">
                        {(() => {
                            const cap = (config.capabilities || [])[0];
                            if (cap === 'stt') return 'Examples: model / stt_model (e.g., whisper-1, nova-2)';
                            if (cap === 'llm') return 'Examples: model (e.g., gpt-4o-mini), max_tokens, temperature';
                            if (cap === 'tts') return 'Examples: voice_name / tts_voice, tts_model';
                            return 'Select a capability to see example fields.';
                        })()}
                    </div>
                )}

                <div className="space-y-3">
                    {customFields.length === 0 && (
                        <div className="text-sm text-muted-foreground italic p-2 border border-dashed rounded">
                            No custom fields added. Click "Add Field" to configure specific settings like 'model', 'voice', etc.
                        </div>
                    )}

                    {customFields.map((field, index) => {
                        const options = getOptionsForKey(field.key);
                        return (
                            <div key={index} className="flex gap-3 items-start">
                                <div className="flex-1">
                                    <input
                                        type="text"
                                        className="w-full p-2 text-sm rounded border border-input bg-background font-mono"
                                        placeholder="Key (e.g. model)"
                                        value={field.key}
                                        onChange={(e) => handleFieldChange(index, 'key', e.target.value)}
                                    />
                                </div>
                                <div className="flex-1 flex items-center gap-2">
                                    <input
                                        type="text"
                                        className="w-full p-2 text-sm rounded border border-input bg-background"
                                        placeholder="Value (e.g. gpt-4o)"
                                        value={field.value}
                                        onChange={(e) => handleFieldChange(index, 'value', e.target.value)}
                                    />
                                    {options && (
                                        <HelpTooltip content={
                                            <div className="text-xs">
                                                <p className="font-semibold mb-1">Suggested Values:</p>
                                                <ul className="list-disc list-inside max-h-40 overflow-y-auto">
                                                    {options.map(opt => <li key={opt}>{opt}</li>)}
                                                </ul>
                                            </div>
                                        } />
                                    )}
                                </div>
                                <button
                                    type="button"
                                    onClick={() => removeField(index)}
                                    className="p-2 text-muted-foreground hover:text-destructive transition-colors"
                                >
                                    <Trash2 className="w-4 h-4" />
                                </button>
                            </div>
                        );
                    })}
                </div>

                {config.type === 'full' && (
                    <div className="bg-blue-50/50 dark:bg-blue-900/10 p-3 rounded text-xs text-muted-foreground border border-blue-100 dark:border-blue-900/20">
                        <p className="font-semibold mb-1">Tip: Full Agents usually require:</p>
                        <ul className="list-disc list-inside space-y-1">
                            <li><code>continuous_input</code>: true (Required)</li>
                            <li><code>input_encoding</code>: mulaw</li>
                            <li><code>output_encoding</code>: mulaw</li>
                        </ul>
                    </div>
                )}
            </div>
        </div>
    );
};

export default GenericProviderForm;
