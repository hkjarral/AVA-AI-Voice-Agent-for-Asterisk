import React from 'react';
import { FormInput, FormSelect, FormLabel } from '../ui/FormComponents';
import { isFullAgentProvider } from '../../utils/providerNaming';

interface ContextFormProps {
    config: any;
    providers: any;
    pipelines?: any;
    availableTools?: string[];
    toolEnabledMap?: Record<string, boolean>;
    availableProfiles?: string[];
    defaultProfileName?: string;
    onChange: (newConfig: any) => void;
    isNew?: boolean;
}

const ContextForm = ({ config, providers, pipelines, availableTools, toolEnabledMap, availableProfiles, defaultProfileName, onChange, isNew }: ContextFormProps) => {
    const updateConfig = (field: string, value: any) => {
        onChange({ ...config, [field]: value });
    };

    const fallbackTools = [
        'transfer',
        'attended_transfer',
        'cancel_transfer',
        'hangup_call',
        'leave_voicemail',
        'send_email_summary',
        'request_transcript'
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
            updateConfig('provider', '');
            updateConfig('pipeline', '');
            return;
        }
        if (raw.startsWith('pipeline:')) {
            updateConfig('pipeline', raw.slice('pipeline:'.length));
            updateConfig('provider', '');
            return;
        }
        if (raw.startsWith('provider:')) {
            updateConfig('provider', raw.slice('provider:'.length));
            updateConfig('pipeline', '');
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

            <div className="space-y-3">
                <FormLabel>Available Tools</FormLabel>
                <div className="grid grid-cols-2 gap-3">
                    {toolOptions.map(tool => (
                        <label
                            key={tool}
                            title={isToolDisabled(tool) ? 'Disabled globally in Tools settings' : undefined}
                            className={[
                                "flex items-center space-x-3 p-3 rounded-md border border-border bg-card/50 transition-colors",
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
                            <span className="text-sm font-medium">{displayToolName(tool)}</span>
                        </label>
                    ))}
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
