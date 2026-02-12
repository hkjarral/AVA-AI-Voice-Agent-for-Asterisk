import { FormInput, FormSelect, FormLabel } from '../ui/FormComponents';
import { isFullAgentProvider } from '../../utils/providerNaming';
import { ChevronDown, ChevronRight, Search, Phone, Webhook, Lock } from 'lucide-react';
import { useState } from 'react';
import HelpTooltip from '../ui/HelpTooltip';

interface ContextFormProps {
    config: any;
    providers: any;
    pipelines?: any;
    availableTools?: string[];
    toolEnabledMap?: Record<string, boolean>;
    toolCatalogByName?: Record<string, any>;
    availableProfiles?: string[];
    defaultProfileName?: string;
    httpTools?: Record<string, any>;
    onChange: (newConfig: any) => void;
    isNew?: boolean;
}

const ContextForm = ({ config, providers, pipelines, availableTools, toolEnabledMap, toolCatalogByName, availableProfiles, defaultProfileName, httpTools, onChange, isNew }: ContextFormProps) => {
    const [expandedPhases, setExpandedPhases] = useState<Record<string, boolean>>({
        pre_call: false,
        in_call: true,
        post_call: false,
    });

    const togglePhase = (phase: string) => {
        setExpandedPhases(prev => ({ ...prev, [phase]: !prev[phase] }));
    };

    const updateConfig = (field: string, value: any) => {
        onChange({ ...config, [field]: value });
    };

    const updateConfigPatch = (patch: Record<string, any>) => {
        onChange({ ...config, ...patch });
    };

    const getHttpToolsByPhase = (phase: 'pre_call' | 'post_call' | 'in_call') => {
        if (!httpTools) return [];
        return Object.entries(httpTools)
            .filter(([_, tool]) => tool?.phase === phase && tool?.enabled !== false)
            .map(([name, tool]) => ({ name, ...tool }));
    };

    const handlePhaseToolToggle = (phase: 'pre_call_tools' | 'post_call_tools' | 'in_call_http_tools', toolName: string) => {
        const currentTools = config[phase] || [];
        const newTools = currentTools.includes(toolName)
            ? currentTools.filter((t: string) => t !== toolName)
            : [...currentTools, toolName];
        updateConfig(phase, newTools);
    };

    const handleGlobalToolDisable = (phase: 'disable_global_pre_call_tools' | 'disable_global_post_call_tools' | 'disable_global_in_call_tools', toolName: string) => {
        const currentDisabled = config[phase] || [];
        const newDisabled = currentDisabled.includes(toolName)
            ? currentDisabled.filter((t: string) => t !== toolName)
            : [...currentDisabled, toolName];
        updateConfig(phase, newDisabled);
    };

    const isGlobalToolDisabled = (phase: 'pre_call' | 'post_call' | 'in_call', toolName: string) => {
        const key = phase === 'pre_call' ? 'disable_global_pre_call_tools' 
            : phase === 'post_call' ? 'disable_global_post_call_tools'
            : 'disable_global_in_call_tools';
        return (config[key] || []).includes(toolName);
    };

    const fallbackTools = [
        'transfer',
        'attended_transfer',
        'cancel_transfer',
        'live_agent_transfer',
        'hangup_call',
        'leave_voicemail',
        'send_email_summary',
        'request_transcript',
        'check_extension_status',
    ];
    const toolOptionsBase = (availableTools && availableTools.length > 0) ? availableTools : fallbackTools;
    const selectedTools = Array.isArray(config.tools) ? config.tools : [];
    const toolOptions = Array.from(new Set([...toolOptionsBase, ...selectedTools])).sort();

    const fallbackProfiles = [
        'telephony_responsive',
        'telephony_ulaw_8k',
        'openai_realtime_24k',
        'wideband_pcm_16k'
    ];
    const profileOptions = (availableProfiles && availableProfiles.length > 0) ? availableProfiles : fallbackProfiles;
    const defaultProfileLabel = defaultProfileName ? `Default (${defaultProfileName})` : 'Default (from profiles.default)';

    const handleToolToggle = (tool: string) => {
        if (toolEnabledMap && toolEnabledMap[tool] === false) return;
        const currentTools = config.tools || [];
        const newTools = currentTools.includes(tool)
            ? currentTools.filter((t: string) => t !== tool)
            : [...currentTools, tool];
        updateConfig('tools', newTools);
    };

    const displayToolName = (tool: string) => {
        if (tool === 'transfer') return 'blind_transfer';
        return tool;
    };

    const toolDescription = (tool: string) => {
        const canonical = displayToolName(tool);
        const fromCatalog = toolCatalogByName?.[canonical]?.description || toolCatalogByName?.[tool]?.description;
        if (typeof fromCatalog === 'string' && fromCatalog.trim()) return fromCatalog.trim();
        return '';
    };

    const isToolDisabled = (tool: string) => {
        if (!toolEnabledMap) return false;
        return toolEnabledMap[tool] === false;
    };

    const pipelineOptions = Object.entries(pipelines || {}).map(([name, _]: [string, any]) => ({
        value: `pipeline:${name}`,
        label: `[Pipeline] ${name}`,
    }));

    const providerOptions = Object.entries(providers || {})
        .filter(([_, p]: [string, any]) => isFullAgentProvider(p))
        .map(([name, p]: [string, any]) => ({
            value: `provider:${name}`,
            label: `[Provider] ${name}${p.enabled === false ? ' (Disabled)' : ''}`,
        }));

    const overrideValue = config.pipeline
        ? `pipeline:${config.pipeline}`
        : (config.provider ? `provider:${config.provider}` : '');

    const handleOverrideChange = (raw: string) => {
        if (!raw) {
            updateConfigPatch({ provider: '', pipeline: '' });
            return;
        }
        if (raw.startsWith('pipeline:')) {
            updateConfigPatch({ pipeline: raw.slice('pipeline:'.length), provider: '' });
            return;
        }
        if (raw.startsWith('provider:')) {
            updateConfigPatch({ provider: raw.slice('provider:'.length), pipeline: '' });
        }
    };

    return (
        <div className="space-y-6">
            <FormInput
                label="Context Name"
                value={config.name || ''}
                onChange={(e) => updateConfig('name', e.target.value)}
                disabled={!isNew}
                placeholder="e.g., demo_support"
            />

            <FormInput
                label="Greeting"
                value={config.greeting || ''}
                onChange={(e) => updateConfig('greeting', e.target.value)}
                placeholder="Hi {caller_name}, how can I help you?"
                tooltip="Use {caller_name} as a placeholder for the caller's name"
            />

            <div className="space-y-2">
                <FormLabel tooltip="The main instruction prompt for the AI agent">System Prompt</FormLabel>
                <textarea
                    className="w-full p-3 rounded-md border border-input bg-transparent text-sm min-h-[200px] focus:outline-none focus:ring-1 focus:ring-ring"
                    value={config.prompt || ''}
                    onChange={(e) => updateConfig('prompt', e.target.value)}
                    placeholder="You are a helpful voice assistant..."
                />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <FormSelect
                    label="Audio Profile"
                    tooltip="Optional. If not set, the default profile from profiles.default is used."
                    options={[
                        { value: '', label: defaultProfileLabel },
                        ...profileOptions.map(p => ({ value: p, label: p }))
                    ]}
                    value={config.profile || ''}
                    onChange={(e) => updateConfig('profile', e.target.value)}
                />

                <FormSelect
                    label="Provider/Pipeline Override (Optional)"
                    tooltip="Choose either a monolithic provider or a modular pipeline. Pipeline overrides provider when set."
                    options={[
                        { value: '', label: 'Default (None)' },
                        ...pipelineOptions,
                        ...providerOptions,
                    ]}
                    value={overrideValue}
                    onChange={(e) => handleOverrideChange(e.target.value)}
                />
            </div>

            {/* Phase-Based Tool Configuration */}
            <div className="space-y-3">
                <FormLabel>Tools by Phase</FormLabel>
                
                {/* Pre-Call Tools */}
                <div className="border border-border rounded-lg overflow-hidden">
                    <button
                        type="button"
                        onClick={() => togglePhase('pre_call')}
                        className="w-full flex items-center justify-between p-3 bg-card/50 hover:bg-accent/50 transition-colors"
                    >
                        <div className="flex items-center gap-2">
                            <Search className="w-4 h-4 text-blue-500" />
                            <span className="font-medium text-sm">Pre-Call Tools</span>
                            <span className="text-xs text-muted-foreground">(CRM lookups, enrichment)</span>
                        </div>
                        {expandedPhases.pre_call ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                    </button>
                    {expandedPhases.pre_call && (
                        <div className="p-3 border-t border-border bg-background/50">
                            {getHttpToolsByPhase('pre_call').length === 0 ? (
                                <p className="text-xs text-muted-foreground">No pre-call tools configured. Add them in the Tools page.</p>
                            ) : (
                                <div className="grid grid-cols-2 gap-2">
                                    {getHttpToolsByPhase('pre_call').map(tool => (
                                        <label 
                                            key={tool.name} 
                                            className={`flex items-center justify-between p-2 rounded border border-border bg-card/30 ${
                                                tool.is_global && isGlobalToolDisabled('pre_call', tool.name) ? 'opacity-50' : 'hover:bg-accent'
                                            } cursor-pointer`}
                                        >
                                            <div className="flex items-center space-x-2">
                                                {!tool.is_global && (
                                                    <input
                                                        type="checkbox"
                                                        className="rounded border-input text-primary focus:ring-primary"
                                                        checked={(config.pre_call_tools || []).includes(tool.name)}
                                                        onChange={() => handlePhaseToolToggle('pre_call_tools', tool.name)}
                                                    />
                                                )}
                                                <span className="text-xs font-medium">{tool.name}</span>
                                                {(toolCatalogByName?.[tool.name]?.description || tool.description) ? (
                                                    <HelpTooltip content={(toolCatalogByName?.[tool.name]?.description || tool.description || '').toString()} />
                                                ) : null}
                                                {tool.is_global && <span title="Global tool (runs for all contexts)"><Lock className="w-3 h-3 text-blue-500" /></span>}
                                            </div>
                                            {tool.is_global && (
                                                <button
                                                    type="button"
                                                    onClick={(e) => {
                                                        e.preventDefault();
                                                        handleGlobalToolDisable('disable_global_pre_call_tools', tool.name);
                                                    }}
                                                    className={`text-xs px-2 py-0.5 rounded ${
                                                        isGlobalToolDisabled('pre_call', tool.name)
                                                            ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30'
                                                            : 'bg-green-500/20 text-green-400 hover:bg-green-500/30'
                                                    }`}
                                                    title={isGlobalToolDisabled('pre_call', tool.name) ? 'Click to enable for this context' : 'Click to disable for this context'}
                                                >
                                                    {isGlobalToolDisabled('pre_call', tool.name) ? 'Disabled' : 'Enabled'}
                                                </button>
                                            )}
                                        </label>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}
                </div>

                {/* In-Call Tools */}
                <div className="border border-border rounded-lg overflow-hidden">
                    <button
                        type="button"
                        onClick={() => togglePhase('in_call')}
                        className="w-full flex items-center justify-between p-3 bg-card/50 hover:bg-accent/50 transition-colors"
                    >
                        <div className="flex items-center gap-2">
                            <Phone className="w-4 h-4 text-green-500" />
                            <span className="font-medium text-sm">In-Call Tools</span>
                            <span className="text-xs text-muted-foreground">(transfer, hangup, email)</span>
                        </div>
                        {expandedPhases.in_call ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                    </button>
                    {expandedPhases.in_call && (
                        <div className="p-3 border-t border-border bg-background/50">
                            <div className="grid grid-cols-2 gap-2">
                                {/* Built-in tools */}
                                {toolOptions.map(tool => (
                                    <label
                                        key={tool}
                                        title={isToolDisabled(tool) ? 'Disabled globally in Tools settings' : undefined}
                                        className={[
                                            "flex items-center space-x-2 p-2 rounded border border-border bg-card/30 transition-colors",
                                            isToolDisabled(tool) ? "opacity-50 cursor-not-allowed" : "hover:bg-accent cursor-pointer"
                                        ].join(' ')}
                                    >
                                        <input
                                            type="checkbox"
                                            className="rounded border-input text-primary focus:ring-primary"
                                            disabled={isToolDisabled(tool)}
                                            checked={(config.tools || []).includes(tool)}
                                            onChange={() => handleToolToggle(tool)}
                                        />
                                        <span className="text-xs font-medium">{displayToolName(tool)}</span>
                                        {toolDescription(tool) ? (
                                            <HelpTooltip content={toolDescription(tool)} />
                                        ) : null}
                                    </label>
                                ))}
                                {/* HTTP tools with phase=in_call */}
                                {getHttpToolsByPhase('in_call').map(tool => (
                                    <label 
                                        key={`http-${tool.name}`} 
                                        className={`flex items-center justify-between p-2 rounded border border-border bg-card/30 ${
                                            tool.is_global && isGlobalToolDisabled('in_call', tool.name) ? 'opacity-50' : 'hover:bg-accent'
                                        } cursor-pointer`}
                                    >
                                        <div className="flex items-center space-x-2">
                                            {!tool.is_global && (
                                                <input
                                                    type="checkbox"
                                                    className="rounded border-input text-primary focus:ring-primary"
                                                    checked={(config.in_call_http_tools || []).includes(tool.name)}
                                                    onChange={() => handlePhaseToolToggle('in_call_http_tools', tool.name)}
                                                />
                                            )}
                                            <span className="text-xs font-medium">{tool.name}</span>
                                            {(toolCatalogByName?.[tool.name]?.description || tool.description) ? (
                                                <HelpTooltip content={(toolCatalogByName?.[tool.name]?.description || tool.description || '').toString()} />
                                            ) : null}
                                            {tool.is_global && <span title="Global tool (runs for all contexts)"><Lock className="w-3 h-3 text-blue-500" /></span>}
                                        </div>
                                        {tool.is_global && (
                                            <button
                                                type="button"
                                                onClick={(e) => {
                                                    e.preventDefault();
                                                    handleGlobalToolDisable('disable_global_in_call_tools', tool.name);
                                                }}
                                                className={`text-xs px-2 py-0.5 rounded ${
                                                    isGlobalToolDisabled('in_call', tool.name)
                                                        ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30'
                                                        : 'bg-green-500/20 text-green-400 hover:bg-green-500/30'
                                                }`}
                                                title={isGlobalToolDisabled('in_call', tool.name) ? 'Click to enable for this context' : 'Click to disable for this context'}
                                            >
                                                {isGlobalToolDisabled('in_call', tool.name) ? 'Disabled' : 'Enabled'}
                                            </button>
                                        )}
                                    </label>
                                ))}
                            </div>
                        </div>
                    )}
                </div>

                {/* Post-Call Tools */}
                <div className="border border-border rounded-lg overflow-hidden">
                    <button
                        type="button"
                        onClick={() => togglePhase('post_call')}
                        className="w-full flex items-center justify-between p-3 bg-card/50 hover:bg-accent/50 transition-colors"
                    >
                        <div className="flex items-center gap-2">
                            <Webhook className="w-4 h-4 text-orange-500" />
                            <span className="font-medium text-sm">Post-Call Tools</span>
                            <span className="text-xs text-muted-foreground">(webhooks, CRM updates)</span>
                        </div>
                        {expandedPhases.post_call ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                    </button>
                    {expandedPhases.post_call && (
                        <div className="p-3 border-t border-border bg-background/50">
                            {getHttpToolsByPhase('post_call').length === 0 ? (
                                <p className="text-xs text-muted-foreground">No post-call tools configured. Add them in the Tools page.</p>
                            ) : (
                                <div className="grid grid-cols-2 gap-2">
                                    {getHttpToolsByPhase('post_call').map(tool => (
                                        <label 
                                            key={tool.name} 
                                            className={`flex items-center justify-between p-2 rounded border border-border bg-card/30 ${
                                                tool.is_global && isGlobalToolDisabled('post_call', tool.name) ? 'opacity-50' : 'hover:bg-accent'
                                            } cursor-pointer`}
                                        >
                                            <div className="flex items-center space-x-2">
                                                {!tool.is_global && (
                                                    <input
                                                        type="checkbox"
                                                        className="rounded border-input text-primary focus:ring-primary"
                                                        checked={(config.post_call_tools || []).includes(tool.name)}
                                                        onChange={() => handlePhaseToolToggle('post_call_tools', tool.name)}
                                                    />
                                                )}
                                                <span className="text-xs font-medium">{tool.name}</span>
                                                {(toolCatalogByName?.[tool.name]?.description || tool.description) ? (
                                                    <HelpTooltip content={(toolCatalogByName?.[tool.name]?.description || tool.description || '').toString()} />
                                                ) : null}
                                                {tool.is_global && <span title="Global tool (runs for all contexts)"><Lock className="w-3 h-3 text-blue-500" /></span>}
                                            </div>
                                            {tool.is_global && (
                                                <button
                                                    type="button"
                                                    onClick={(e) => {
                                                        e.preventDefault();
                                                        handleGlobalToolDisable('disable_global_post_call_tools', tool.name);
                                                    }}
                                                    className={`text-xs px-2 py-0.5 rounded ${
                                                        isGlobalToolDisabled('post_call', tool.name)
                                                            ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30'
                                                            : 'bg-green-500/20 text-green-400 hover:bg-green-500/30'
                                                    }`}
                                                    title={isGlobalToolDisabled('post_call', tool.name) ? 'Click to enable for this context' : 'Click to disable for this context'}
                                                >
                                                    {isGlobalToolDisabled('post_call', tool.name) ? 'Disabled' : 'Enabled'}
                                                </button>
                                            )}
                                        </label>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div>

            {/* Background Music Configuration */}
            <div className="space-y-4 p-4 rounded-lg border border-border bg-card/30">
                <div className="flex items-center justify-between">
                    <FormLabel tooltip="Play background music during calls. Music will be heard by the caller while talking to the AI agent.">
                        Background Music
                    </FormLabel>
                    <label className="relative inline-flex items-center cursor-pointer">
                        <input
                            type="checkbox"
                            className="sr-only peer"
                            checked={!!config.background_music}
                            onChange={(e) => {
                                if (e.target.checked) {
                                    updateConfig('background_music', 'default');
                                } else {
                                    updateConfig('background_music', undefined);
                                }
                            }}
                        />
                        <div className="w-11 h-6 bg-gray-600 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-primary rounded-full peer peer-checked:after:translate-x-full rtl:peer-checked:after:-translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:start-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                    </label>
                </div>

                {config.background_music && (
                    <div className="space-y-3">
                        <FormInput
                            label="MOH Class Name"
                            value={config.background_music || 'default'}
                            onChange={(e) => updateConfig('background_music', e.target.value || 'default')}
                            placeholder="default"
                            tooltip="Music On Hold class name from Asterisk's musiconhold.conf"
                        />
                        <div className="text-xs text-muted-foreground bg-muted/50 p-3 rounded-md space-y-2">
                            <p className="font-medium text-foreground">📁 How to configure Music On Hold:</p>
                            <ol className="list-decimal list-inside space-y-1 ml-1">
                                <li>Place audio files in <code className="bg-muted px-1 rounded">/var/lib/asterisk/moh/{'<class-name>'}/</code></li>
                                <li>For FreePBX: Go to <strong>Settings → Music On Hold</strong> to create categories</li>
                                <li>Supported formats: WAV, ulaw, alaw, sln, mp3</li>
                                <li>💡 <strong>Tip:</strong> Reduce music volume to ~15-20% to avoid interfering with conversation</li>
                            </ol>
                            <p className="mt-2 text-yellow-500/80">
                                ⚠️ Music will be heard by the AI (for VAD). Use low-volume ambient music for best results.
                            </p>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};

export default ContextForm;
