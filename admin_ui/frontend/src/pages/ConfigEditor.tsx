import { useState, useEffect } from 'react';
import Editor from '@monaco-editor/react';
import axios from 'axios';
import { toast } from 'sonner';
import { useConfirmDialog } from '../hooks/useConfirmDialog';
import { Save, Download, AlertCircle, Settings, Server, Trash2, RefreshCw, Loader2 } from 'lucide-react';
import yaml from 'js-yaml';
import { sanitizeConfigForSave } from '../utils/configSanitizers';

// Import Config Components
import GeneralConfig from '../components/config/GeneralConfig';
import AsteriskConfig from '../components/config/AsteriskConfig';
import VADConfig from '../components/config/VADConfig';
import StreamingConfig from '../components/config/StreamingConfig';
import LLMConfig from '../components/config/LLMConfig';
import ToolsConfig from '../components/config/ToolsConfig';
import ContextsConfig from '../components/config/ContextsConfig';
import AudioSocketConfig from '../components/config/AudioSocketConfig';
import DeepgramProviderForm from '../components/config/providers/DeepgramProviderForm';
import OpenAIRealtimeProviderForm from '../components/config/providers/OpenAIRealtimeProviderForm';
import GoogleLiveProviderForm from '../components/config/providers/GoogleLiveProviderForm';
import LocalProviderForm from '../components/config/providers/LocalProviderForm';
import OpenAIProviderForm from '../components/config/providers/OpenAIProviderForm';
import ElevenLabsProviderForm from '../components/config/providers/ElevenLabsProviderForm';
import TelnyxProviderForm from '../components/config/providers/TelnyxProviderForm';


const ConfigEditor = () => {
    const { confirm } = useConfirmDialog();
    const [activeTab, setActiveTab] = useState<'general' | 'asterisk' | 'contexts' | 'providers' | 'pipelines' | 'vad' | 'streaming' | 'llm' | 'tools' | 'audiosocket' | 'yaml'>('general');
    const [yamlContent, setYamlContent] = useState('');
    const [parsedConfig, setParsedConfig] = useState<any>({});
    
    // UI State
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [applying, setApplying] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [warning, setWarning] = useState<string | null>(null);
    const [success, setSuccess] = useState<string | null>(null);
    const [pendingApply, setPendingApply] = useState(false);
    const [applyMethod, setApplyMethod] = useState<'hot_reload' | 'restart'>('restart');
    const [applyPlan, setApplyPlan] = useState<Array<{ service: string; method: string; endpoint: string }>>([]);

    // Provider Editing State
    const [editingProvider, setEditingProvider] = useState<string | null>(null);
    const [providerForm, setProviderForm] = useState<any>({});
    const [isNewProvider, setIsNewProvider] = useState(false);
    const [newProviderType, setNewProviderType] = useState('deepgram');

    useEffect(() => {
        fetchConfig();
    }, []);

    const fetchConfig = async () => {
        setLoading(true);
        try {
            const res = await axios.get('/api/config/yaml');
            setYamlContent(res.data.content);

            try {
                const parsed = yaml.load(res.data.content) as any;
                if (!parsed) {
                    setError("Failed to parse configuration: Empty result");
                    return;
                }
                setParsedConfig(parsed);
            } catch (e: any) {
                console.error("Failed to parse YAML", e);
                setError(`Failed to parse YAML: ${e.message}`);
            }
        } catch (err: any) {
            setError('Failed to load configuration');
        } finally {
            setLoading(false);
        }
    };

    const handleConfigChange = (section: string, newSectionConfig: any) => {
        const newConfig = { ...parsedConfig, [section]: newSectionConfig };
        setParsedConfig(newConfig);
    };

    // Helper to merge top-level fields for General Config
    const handleGeneralChange = (newGeneralConfig: any) => {
        const newConfig = { ...parsedConfig, ...newGeneralConfig };
        setParsedConfig(newConfig);
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            setError(null);
            setWarning(null);
            setSuccess(null);
            let contentToSave = '';
            if (activeTab === 'yaml') {
                contentToSave = yamlContent;
                // Also update parsedConfig to match
                try {
                    const parsed = yaml.load(contentToSave) as any;
                    setParsedConfig(parsed);
                } catch (e) {
                    // If invalid YAML, backend might reject it
                }
            } else {
                const sanitized = sanitizeConfigForSave(parsedConfig);
                contentToSave = yaml.dump(sanitized);
                setYamlContent(contentToSave);
            }

            const response = await axios.post('/api/config/yaml', { content: contentToSave });
            setSuccess(response.data?.message || 'Configuration saved successfully');
            setTimeout(() => setSuccess(null), 5000);

            const warnings = response.data?.warnings;
            if (Array.isArray(warnings) && warnings.length > 0) {
                const shown = warnings.slice(0, 6).join('; ');
                const suffix = warnings.length > 6 ? ` (+${warnings.length - 6} more)` : '';
                setWarning(`Saved with warnings: ${shown}${suffix}`);
                setTimeout(() => setWarning(null), 15000);
            }

            const plan = Array.isArray(response.data?.apply_plan) ? response.data.apply_plan : [];
            const recommended = response.data?.recommended_apply_method;
            if (plan.length > 0) {
                setApplyPlan(plan);
                setApplyMethod(recommended === 'hot_reload' ? 'hot_reload' : 'restart');
                setPendingApply(true);
            } else if (response.data?.restart_required) {
                setApplyPlan([{ service: 'ai_engine', method: 'restart', endpoint: '/api/system/containers/ai_engine/restart' }]);
                setApplyMethod('restart');
                setPendingApply(true);
            } else {
                setApplyPlan([]);
                setPendingApply(false);
            }
        } catch (err: any) {
            console.error(err);
            const msg = err.response?.data?.detail || 'Failed to save configuration';
            setError(msg);
            setTimeout(() => setError(null), 10000);
        } finally {
            setSaving(false);
        }
    };

    const restartAiEngine = async (force: boolean) => {
        const endpoint = '/api/system/containers/ai_engine/restart';
        const response = await axios.post(`${endpoint}?force=${force}`);

        if (response.data?.status === 'warning') {
            const confirmForce = await confirm({
                title: 'Force Restart?',
                description: `${response.data.message}\n\nDo you want to force restart anyway? This may disconnect active calls.`,
                confirmText: 'Force Restart',
                variant: 'destructive'
            });
            if (confirmForce) {
                return restartAiEngine(true);
            }
            setWarning(response.data.message);
            setTimeout(() => setWarning(null), 15000);
            return;
        }

        if (response.data?.status === 'degraded') {
            setWarning(`AI Engine restarted but may not be fully healthy: ${response.data.output || 'Health check issue'}. Verify manually.`);
            setTimeout(() => setWarning(null), 15000);
            return;
        }

        setPendingApply(false);
        setApplyPlan([]);
        setSuccess('Changes applied: AI Engine restarted.');
        setTimeout(() => setSuccess(null), 5000);
    };

    const handleApplyChanges = async () => {
        if (!pendingApply || applyPlan.length === 0) return;
        setApplying(true);
        try {
            setError(null);
            const item = applyPlan[0];
            const method = (item?.method || applyMethod) as string;

            if (method === 'hot_reload') {
                const endpoint = item?.endpoint || '/api/system/containers/ai_engine/reload';
                const resp = await axios.post(endpoint);
                if (resp.data?.restart_required || resp.data?.status === 'partial') {
                    setWarning('Hot reload completed but some changes still require an AI Engine restart.');
                    setTimeout(() => setWarning(null), 15000);
                    setApplyPlan([{ service: 'ai_engine', method: 'restart', endpoint: '/api/system/containers/ai_engine/restart' }]);
                    setApplyMethod('restart');
                    setPendingApply(true);
                    return;
                }

                setPendingApply(false);
                setApplyPlan([]);
                setSuccess('Changes applied: AI Engine hot reloaded.');
                setTimeout(() => setSuccess(null), 5000);
                return;
            }

            await restartAiEngine(false);
        } catch (err: any) {
            const msg = err.response?.data?.detail || err.message || 'Failed to apply changes';
            setError(msg);
            setTimeout(() => setError(null), 10000);
        } finally {
            setApplying(false);
        }
    };

    // When switching TO YAML tab, ensure it's up to date with parsedConfig
    const handleTabChange = (tab: any) => {
        if (tab === 'yaml') {
            setYamlContent(yaml.dump(sanitizeConfigForSave(parsedConfig)));
        } else if (activeTab === 'yaml' && tab !== 'yaml') {
            // If switching FROM yaml, parse it back to object
            try {
                const parsed = yaml.load(yamlContent) as any;
                setParsedConfig(parsed);
            } catch (e) {
                toast.error('Invalid YAML, cannot switch to form view. Please fix errors first.');
                return;
            }
        }
        setActiveTab(tab);
    };

    // Provider Handlers
    const handleEditProvider = (name: string) => {
        setEditingProvider(name);
        setProviderForm({ ...parsedConfig.providers?.[name], name });
        setIsNewProvider(false);
    };

    const handleProviderDelete = async (name: string) => {
        const confirmed = await confirm({
            title: 'Delete Provider?',
            description: `Are you sure you want to delete provider "${name}"?`,
            confirmText: 'Delete',
            variant: 'destructive'
        });
        if (!confirmed) return;
        const newProviders = { ...parsedConfig.providers };
        delete newProviders[name];
        setParsedConfig({ ...parsedConfig, providers: newProviders });
    };

    const startNewProvider = () => {
        setEditingProvider('new');
        setProviderForm({ name: '', type: newProviderType });
        setIsNewProvider(true);
    };

    const handleProviderSave = () => {
        // A9: Require provider name before save
        const providerName = isNewProvider ? providerForm.name?.trim() : editingProvider;
        if (!providerName) {
            setError('Provider name is required');
            return;
        }

        const newConfig = { ...parsedConfig };
        if (!newConfig.providers) newConfig.providers = {};

        // Remove name from the config object itself as it's the key
        const { name, ...providerData } = providerForm;
        
        // A3: Persist provider type when saving new providers
        if (isNewProvider && newProviderType) {
            providerData.type = newProviderType;
        }
        
        newConfig.providers[providerName] = providerData;

        setParsedConfig(newConfig);
        setEditingProvider(null);
        setProviderForm({});
    };

    const renderProviderForm = () => {
        // Helper to update provider form state
        const updateForm = (newValues: any) => setProviderForm({ ...providerForm, ...newValues });

        // Common fields (Name)
        const commonFields = (
            <div className="mb-4 space-y-2">
                <label className="text-sm font-medium">Provider Name</label>
                <input
                    type="text"
                    className="w-full p-2 rounded border border-input bg-background"
                    value={providerForm.name || ''}
                    onChange={(e) => setProviderForm({ ...providerForm, name: e.target.value })}
                    disabled={!isNewProvider}
                    placeholder="e.g., my_deepgram"
                />
                {isNewProvider && (
                    <div className="mt-2">
                        <label className="text-sm font-medium">Provider Type</label>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={newProviderType}
                            onChange={(e) => {
                                setNewProviderType(e.target.value);
                                // Reset form but keep name
                                setProviderForm({ name: providerForm.name });
                            }}
                        >
                            <option value="deepgram">Deepgram</option>
                            <option value="elevenlabs">ElevenLabs TTS / Agent</option>
                            <option value="openai_realtime">OpenAI Realtime</option>
                            <option value="google_live">Google Live</option>
                            <option value="local">Local</option>
                            <option value="openai">OpenAI (Standard)</option>
                            <option value="telnyx">Telnyx (LLM)</option>
                        </select>
                    </div>
                )}
            </div>
        );

        const guessType = (data: any) => {
            if (data.type === 'elevenlabs' || data.type === 'elevenlabs_agent' || data.agent_id || data.voice_id) return 'elevenlabs';
            if (data.realtime_base_url || data.turn_detection) return 'openai_realtime';
            if (data.google_live || data.llm_model?.includes('gemini')) return 'google_live';
            if (data.ws_url) return 'local';
            if ((data.chat_base_url || '').includes('telnyx.com')) return 'telnyx';
            if (data.stt_model && !data.ws_url) return 'openai';
            return 'deepgram'; // Default
        };

        const currentType = isNewProvider ? newProviderType : guessType(providerForm);

        let FormComponent = DeepgramProviderForm;
        switch (currentType) {
            case 'elevenlabs':
                FormComponent = ElevenLabsProviderForm;
                break;
            case 'openai_realtime':
                FormComponent = OpenAIRealtimeProviderForm;
                break;
            case 'google_live':
                FormComponent = GoogleLiveProviderForm;
                break;
            case 'local':
                FormComponent = LocalProviderForm;
                break;
            case 'openai':
                FormComponent = OpenAIProviderForm;
                break;
            case 'telnyx':
                FormComponent = TelnyxProviderForm;
                break;
            default:
                FormComponent = DeepgramProviderForm;
        }

        return (
            <div className="space-y-4">
                {commonFields}
                <div className="border-t pt-4">
                    <FormComponent config={providerForm} onChange={updateForm} />
                </div>
            </div>
        );
    };

    const tabs = [
        { id: 'general', label: 'General' },
        { id: 'asterisk', label: 'Asterisk' },
        { id: 'contexts', label: 'Contexts' },
        { id: 'providers', label: 'Providers' },
        { id: 'pipelines', label: 'Pipelines' },
        { id: 'vad', label: 'VAD' },
        { id: 'streaming', label: 'Streaming' },
        { id: 'llm', label: 'LLM' },
        { id: 'tools', label: 'Tools' },
        { id: 'audiosocket', label: 'AudioSocket' },
        { id: 'yaml', label: 'Raw YAML' },
    ];

    return (
        <div className="h-full flex flex-col space-y-4">
            <div className="flex justify-between items-center">
                <h1 className="text-2xl font-bold">Configuration</h1>
                <div className="flex gap-2 items-center">
                    <div className="hidden xl:flex items-center text-xs text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 px-3 py-1.5 rounded-full border border-amber-200 dark:border-amber-900/50 mr-2">
                        <AlertCircle className="w-3.5 h-3.5 mr-1.5" />
                        <span className="font-medium">Warning:</span>
                        <span className="ml-1">Saves overwrite the full config file</span>
                    </div>
                    <button
                        onClick={async () => {
                            try {
                                const response = await axios.get('/api/config/export', { responseType: 'blob' });
                                const url = window.URL.createObjectURL(new Blob([response.data]));
                                const link = document.createElement('a');
                                link.href = url;
                                const date = new Date().toISOString().slice(0, 19).replace(/:/g, '-');
                                link.setAttribute('download', `config-backup-${date}.zip`);
                                document.body.appendChild(link);
                                link.click();
                                link.remove();
                                setSuccess('Configuration exported successfully');
                                setTimeout(() => setSuccess(null), 3000);
                            } catch (err: any) {
                                setError('Failed to export configuration');
                            }
                        }}
                        className="flex items-center px-4 py-2 bg-secondary text-foreground border border-border rounded-md hover:bg-accent"
                    >
                        <Download className="w-4 h-4 mr-2" />
                        Export
                    </button>
                    <button
                        onClick={handleSave}
                        disabled={saving}
                        className="flex items-center px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50"
                    >
                        <Save className="w-4 h-4 mr-2" />
                        Save Changes
                    </button>
                </div>
            </div>

            <div className="bg-secondary rounded-lg p-1 flex overflow-x-auto">
                {tabs.map(tab => (
                    <button
                        key={tab.id}
                        onClick={() => handleTabChange(tab.id)}
                        className={`px-3 py-1 rounded-md text-sm font-medium transition-colors whitespace-nowrap ${activeTab === tab.id ? 'bg-background shadow-sm' : 'hover:bg-background/50'}`}
                    >
                        {tab.label}
                    </button>
                ))}
            </div>

            {error && (
                <div className="p-4 bg-destructive/10 text-destructive rounded-md border border-destructive/20 flex justify-between items-center">
                    <span>{error}</span>
                    <button onClick={() => setError(null)} className="text-destructive hover:opacity-70">×</button>
                </div>
            )}

            {warning && (
                <div className="p-4 bg-yellow-500/10 text-yellow-700 dark:text-yellow-400 rounded-md border border-yellow-500/20 flex justify-between items-center">
                    <span>{warning}</span>
                    <button onClick={() => setWarning(null)} className="hover:opacity-70">×</button>
                </div>
            )}
            
            {success && (
                <div className="p-4 bg-green-500/10 text-green-600 dark:text-green-400 rounded-md border border-green-500/20 flex justify-between items-center">
                    <span>{success}</span>
                    <button onClick={() => setSuccess(null)} className="hover:opacity-70">×</button>
                </div>
            )}
            
            {pendingApply && (
                <div className={`${pendingApply ? 'bg-orange-500/15 border-orange-500/30' : 'bg-yellow-500/10 border-yellow-500/20'} border text-yellow-600 dark:text-yellow-500 p-4 rounded-md flex items-center justify-between`}>
                    <div className="flex items-center">
                        <AlertCircle className="w-5 h-5 mr-2" />
                        {applyMethod === 'hot_reload'
                            ? 'Changes saved. Apply Changes to hot reload AI Engine without a restart.'
                            : 'Changes saved. Restart AI Engine to apply changes.'}
                    </div>
                    <button
                        onClick={handleApplyChanges}
                        disabled={applying || !pendingApply}
                        className={`flex items-center text-xs px-3 py-1.5 rounded transition-colors ${
                            pendingApply
                                ? 'bg-orange-500 text-white hover:bg-orange-600 font-medium'
                                : 'bg-yellow-500/20 hover:bg-yellow-500/30'
                        } disabled:opacity-50`}
                    >
                        {applying ? (
                            <Loader2 className="w-3 h-3 mr-1.5 animate-spin" />
                        ) : (
                            <RefreshCw className="w-3 h-3 mr-1.5" />
                        )}
                        {applying ? 'Applying...' : applyMethod === 'hot_reload' ? 'Apply Changes' : 'Restart AI Engine'}
                    </button>
                </div>
            )}

            <div className="flex-1 overflow-y-auto border border-border rounded-lg p-4 bg-card">
                {loading && (
                    <div className="flex items-center justify-center p-8">
                        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
                    </div>
                )}
                {activeTab === 'general' && (
                    <GeneralConfig
                        config={{
                            active_pipeline: parsedConfig.active_pipeline,
                            default_provider: parsedConfig.default_provider,
                            audio_transport: parsedConfig.audio_transport,
                            config_version: parsedConfig.config_version,
                            downstream_mode: parsedConfig.downstream_mode,
                            barge_in: parsedConfig.barge_in,
                            external_media: parsedConfig.external_media
                        }}
                        onChange={handleGeneralChange}
                    />
                )}
                {activeTab === 'asterisk' && (
                    <AsteriskConfig
                        config={{ ...parsedConfig.asterisk, app_name: parsedConfig.asterisk?.app_name || parsedConfig.app_name }}
                        onChange={(c) => {
                            const { app_name, ...asteriskConfig } = c;
                            handleConfigChange('asterisk', asteriskConfig);
                            if (app_name !== undefined) {
                                handleConfigChange('app_name', app_name);
                            }
                        }}
                    />
                )}
                {activeTab === 'contexts' && (
                    <ContextsConfig
                        config={parsedConfig.contexts || {}}
                        onChange={(c) => handleConfigChange('contexts', c)}
                    />
                )}
                {activeTab === 'vad' && (
                    <VADConfig
                        config={parsedConfig.vad || {}}
                        onChange={(c) => handleConfigChange('vad', c)}
                    />
                )}
                {activeTab === 'streaming' && (
                    <StreamingConfig
                        config={parsedConfig.streaming || {}}
                        onChange={(c) => handleConfigChange('streaming', c)}
                    />
                )}
                {activeTab === 'llm' && (
                    <LLMConfig
                        config={parsedConfig.llm || {}}
                        onChange={(c) => handleConfigChange('llm', c)}
                    />
                )}
                {activeTab === 'tools' && (
                    <ToolsConfig
                        config={parsedConfig.tools || {}}
                        onChange={(c) => handleConfigChange('tools', c)}
                    />
                )}
                {activeTab === 'audiosocket' && (
                    <AudioSocketConfig
                        config={parsedConfig.audiosocket || {}}
                        onChange={(c) => handleConfigChange('audiosocket', c)}
                    />
                )}

                {activeTab === 'providers' && (
                    <div className="space-y-6">
                        <div className="flex justify-between items-center">
                            <div>
                                <h3 className="text-lg font-semibold">AI Providers</h3>
                                <p className="text-sm text-muted-foreground">
                                    Configure connections to external AI services (STT, LLM, TTS)
                                </p>
                            </div>
                            <button
                                onClick={startNewProvider}
                                className="flex items-center px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90"
                            >
                                <Server className="w-4 h-4 mr-2" />
                                Add Provider
                            </button>
                        </div>

                        <div className="grid grid-cols-1 gap-4">
                            {Object.entries(parsedConfig.providers || {}).map(([name, providerData]: [string, any]) => (
                                <div key={name} className="border border-border rounded-lg p-4 bg-card flex justify-between items-center group">
                                    <div>
                                        <h4 className="font-bold text-lg">{name}</h4>
                                        <div className="text-sm text-muted-foreground mt-1">
                                            {providerData.model && <span className="mr-3">Model: {providerData.model}</span>}
                                            {providerData.voice && <span>Voice: {providerData.voice}</span>}
                                        </div>
                                    </div>
                                    <div className="flex space-x-2 opacity-0 group-hover:opacity-100 transition-opacity">
                                        <button
                                            onClick={() => handleEditProvider(name)}
                                            className="p-2 hover:bg-accent rounded-md"
                                        >
                                            <Settings className="w-4 h-4" />
                                        </button>
                                        <button
                                            onClick={() => handleProviderDelete(name)}
                                            className="p-2 hover:bg-destructive/20 text-destructive rounded-md"
                                        >
                                            <Trash2 className="w-4 h-4" />
                                        </button>
                                    </div>
                                </div>
                            ))}
                        </div>

                        {/* Provider Edit Modal */}
                        {editingProvider && (
                            <div className="fixed inset-0 bg-background/80 backdrop-blur-sm flex items-center justify-center p-4 z-50">
                                <div className="bg-card border border-border rounded-lg shadow-lg w-full max-w-3xl max-h-[90vh] overflow-y-auto p-6 space-y-4">
                                    <h2 className="text-xl font-bold">
                                        {isNewProvider ? 'Add Provider' : `Edit Provider: ${editingProvider}`}
                                    </h2>

                                    {renderProviderForm()}

                                    <div className="flex justify-end space-x-2 pt-4 border-t">
                                        <button
                                            onClick={() => setEditingProvider(null)}
                                            className="px-4 py-2 rounded border border-input hover:bg-accent"
                                        >
                                            Cancel
                                        </button>
                                        <button
                                            onClick={handleProviderSave}
                                            className="px-4 py-2 rounded bg-primary text-primary-foreground hover:bg-primary/90"
                                        >
                                            Save Provider
                                        </button>
                                    </div>
                                </div>
                            </div>
                        )}
                    </div>
                )}

                {activeTab === 'pipelines' && (
                    <div className="grid grid-cols-1 gap-4">
                        {Object.keys(parsedConfig.pipelines || {}).length === 0 && (
                            <div className="text-center p-8 text-muted-foreground">
                                No pipelines found.
                            </div>
                        )}
                        {Object.entries(parsedConfig.pipelines || {}).map(([name, config]: [string, any]) => (
                            <div key={name} className="border border-border rounded-lg p-4 bg-background">
                                <h3 className="font-bold text-lg mb-2">{name}</h3>
                                <pre className="text-xs text-muted-foreground overflow-auto bg-secondary/30 p-4 rounded max-h-[300px]">
                                    {JSON.stringify(config, null, 2)}
                                </pre>
                            </div>
                        ))}
                    </div>
                )}

                {activeTab === 'yaml' && (
                    <div className="h-[600px] border border-border rounded-lg overflow-hidden">
                        <Editor
                            height="100%"
                            defaultLanguage="yaml"
                            theme="vs-dark"
                            value={yamlContent}
                            onChange={(value) => setYamlContent(value || '')}
                            options={{
                                minimap: { enabled: false },
                                fontSize: 14,
                            }}
                        />
                    </div>
                )}
            </div>
        </div>
    );
};

export default ConfigEditor;
