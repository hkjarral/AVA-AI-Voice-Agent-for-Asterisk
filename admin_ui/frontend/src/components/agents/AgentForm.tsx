import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import yaml from 'js-yaml';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Modal } from '../ui/Modal';
import { FormInput, FormSelect, FormLabel } from '../ui/FormComponents';
import HelpTooltip from '../ui/HelpTooltip';

export interface Agent {
    slug: string;
    display_name: string;
    extension?: string;
    role_label?: string;
    provider: string;
    voice?: string;
    greeting?: string;
    prompt: string;
    audio_profile?: string;
    is_active: number;
    is_default: number;
    is_operator_managed: number;
    source_file?: string;
    tools_json?: string;
    mcp_json?: string;
    extra_json?: string;
    notes?: string;
}

interface AgentTemplate {
    id: string;
    display_name: string;
    prompt: string;
    greeting: string;
    role_label?: string;
}

interface AgentFormProps {
    isOpen: boolean;
    onClose: () => void;
    onSaved: () => void;
    agent?: Agent | null;
}

const slugify = (name: string): string =>
    name.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');

const AgentForm: React.FC<AgentFormProps> = ({ isOpen, onClose, onSaved, agent }) => {
    const isNew = !agent;

    const [displayName, setDisplayName] = useState('');
    const [slug, setSlug] = useState('');
    const [slugManuallyEdited, setSlugManuallyEdited] = useState(false);
    const [provider, setProvider] = useState('');
    const [voice, setVoice] = useState('');
    const [audioProfile, setAudioProfile] = useState('');
    const [extension, setExtension] = useState('');
    const [roleLabel, setRoleLabel] = useState('');
    const [greeting, setGreeting] = useState('');
    const [prompt, setPrompt] = useState('');
    const [isActive, setIsActive] = useState(1);

    // Advanced fields
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [toolsJson, setToolsJson] = useState('');
    const [mcpJson, setMcpJson] = useState('');
    const [extraJson, setExtraJson] = useState('');
    const [toolsJsonError, setToolsJsonError] = useState('');
    const [mcpJsonError, setMcpJsonError] = useState('');
    const [extraJsonError, setExtraJsonError] = useState('');

    // Config-sourced options
    const [availableProviders, setAvailableProviders] = useState<string[]>([]);
    const [availableProfiles, setAvailableProfiles] = useState<string[]>([]);

    // Templates (create only)
    const [templates, setTemplates] = useState<AgentTemplate[]>([]);
    const [selectedTemplate, setSelectedTemplate] = useState('');

    const [saving, setSaving] = useState(false);

    useEffect(() => {
        if (!isOpen) return;
        loadConfig();
        if (isNew) loadTemplates();
    }, [isOpen, isNew]);

    useEffect(() => {
        if (!isOpen) return;
        if (agent) {
            setDisplayName(agent.display_name);
            setSlug(agent.slug);
            setSlugManuallyEdited(false);
            setProvider(agent.provider || '');
            setVoice(agent.voice || '');
            setAudioProfile(agent.audio_profile || '');
            setExtension(agent.extension || '');
            setRoleLabel(agent.role_label || '');
            setGreeting(agent.greeting || '');
            setPrompt(agent.prompt || '');
            setIsActive(agent.is_active);
            setToolsJson(agent.tools_json || '');
            setMcpJson(agent.mcp_json || '');
            setExtraJson(agent.extra_json || '');
            setShowAdvanced(false);
        } else {
            setDisplayName('');
            setSlug('');
            setSlugManuallyEdited(false);
            setProvider('');
            setVoice('');
            setAudioProfile('');
            setExtension('');
            setRoleLabel('');
            setGreeting('Hi, how can I help you today?');
            setPrompt('You are a helpful voice assistant.');
            setIsActive(1);
            setToolsJson('');
            setMcpJson('');
            setExtraJson('');
            setShowAdvanced(false);
            setSelectedTemplate('');
        }
        setToolsJsonError('');
        setMcpJsonError('');
        setExtraJsonError('');
    }, [isOpen, agent]);

    const loadConfig = async () => {
        try {
            const res = await axios.get('/api/config/yaml');
            if (res.data.yaml_error) return;
            const parsed = yaml.load(res.data.content) as Record<string, unknown>;
            if (!parsed) return;

            const providersBlock = (parsed.providers as Record<string, unknown>) || {};
            const providerNames = Object.entries(providersBlock)
                .filter(([, v]) => v && typeof v === 'object' && !Array.isArray(v) && (v as Record<string, unknown>).enabled !== false)
                .map(([k]) => k)
                .sort();
            setAvailableProviders(providerNames);

            const profilesBlock = (parsed.profiles as Record<string, unknown>) || {};
            const profileNames = Object.entries(profilesBlock)
                .filter(([k, v]) => k !== 'default' && !!v && typeof v === 'object' && !Array.isArray(v))
                .map(([k]) => k)
                .sort();
            setAvailableProfiles(profileNames);
        } catch {
            // Non-blocking: dropdowns degrade gracefully to free-text
        }
    };

    const loadTemplates = async () => {
        try {
            const res = await axios.get('/api/agents/templates');
            setTemplates(Array.isArray(res.data) ? res.data : []);
        } catch {
            setTemplates([]);
        }
    };

    const handleDisplayNameChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const val = e.target.value;
        setDisplayName(val);
        if (!slugManuallyEdited) {
            setSlug(slugify(val));
        }
    };

    const handleSlugChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        setSlug(e.target.value);
        setSlugManuallyEdited(true);
    };

    const handleTemplateSelect = (e: React.ChangeEvent<HTMLSelectElement>) => {
        const id = e.target.value;
        setSelectedTemplate(id);
        if (!id) return;
        const tpl = templates.find((t) => t.id === id);
        if (!tpl) return;
        setPrompt(tpl.prompt);
        setGreeting(tpl.greeting);
        if (tpl.role_label) setRoleLabel(tpl.role_label);
    };

    const validateJson = (value: string, setter: (e: string) => void): boolean => {
        if (!value.trim()) { setter(''); return true; }
        try { JSON.parse(value); setter(''); return true; }
        catch { setter('Invalid JSON'); return false; }
    };

    const handleSubmit = async () => {
        if (!displayName.trim()) { toast.error('Display name is required'); return; }
        if (isNew && !slug.trim()) { toast.error('Slug is required'); return; }

        const t1 = validateJson(toolsJson, setToolsJsonError);
        const t2 = validateJson(mcpJson, setMcpJsonError);
        const t3 = validateJson(extraJson, setExtraJsonError);
        if (!t1 || !t2 || !t3) { toast.error('Fix JSON errors before saving'); return; }

        setSaving(true);
        try {
            const baseBody: Record<string, unknown> = {
                display_name: displayName.trim(),
                provider: provider || '',
                voice: voice || null,
                audio_profile: audioProfile || null,
                extension: extension || null,
                role_label: roleLabel || null,
                greeting: greeting || '',
                prompt: prompt || '',
                tools_json: toolsJson.trim() || null,
                mcp_json: mcpJson.trim() || null,
                extra_json: extraJson.trim() || null,
            };

            if (isNew) {
                const body = { ...baseBody, slug: slug.trim() };
                await axios.post('/api/agents', body);
                toast.success('Agent created');
            } else {
                const body = { ...baseBody, is_active: isActive };
                await axios.patch(`/api/agents/${agent!.slug}`, body);
                toast.success('Agent saved');
            }
            onSaved();
            onClose();
        } catch (e: unknown) {
            const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
            toast.error(detail ?? 'Save failed');
        } finally {
            setSaving(false);
        }
    };

    const providerOptions = [
        { value: '', label: '— select provider —' },
        ...availableProviders.map((p) => ({ value: p, label: p })),
    ];

    const profileOptions = [
        { value: '', label: '— default —' },
        ...availableProfiles.map((p) => ({ value: p, label: p })),
    ];

    const templateOptions = [
        { value: '', label: '— choose a template (optional) —' },
        ...templates.map((t) => ({ value: t.id, label: t.display_name })),
    ];

    return (
        <Modal
            isOpen={isOpen}
            onClose={onClose}
            title={isNew ? 'New Agent' : `Edit Agent — ${agent?.display_name}`}
            size="lg"
            footer={
                <>
                    <button
                        onClick={onClose}
                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={handleSubmit}
                        disabled={saving}
                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                    >
                        {saving ? 'Saving…' : isNew ? 'Create Agent' : 'Save Changes'}
                    </button>
                </>
            }
        >
            <div className="space-y-4">
                {/* Template picker — create only */}
                {isNew && templates.length > 0 && (
                    <FormSelect
                        id="agent-template"
                        label="Start from template"
                        options={templateOptions}
                        value={selectedTemplate}
                        onChange={handleTemplateSelect}
                    />
                )}

                <FormInput
                    id="agent-display-name"
                    label="Display Name"
                    value={displayName}
                    onChange={handleDisplayNameChange}
                    placeholder="e.g. Receptionist"
                    required
                />

                {isNew && (
                    <div className="mb-4">
                        <div className="flex items-center gap-1.5 mb-1.5">
                            <label htmlFor="agent-slug" className="block text-sm font-medium">
                                Slug
                            </label>
                            <HelpTooltip content="Unique identifier used in dialplan and API. Auto-generated from display name; cannot be changed after creation." />
                        </div>
                        <input
                            id="agent-slug"
                            value={slug}
                            onChange={handleSlugChange}
                            placeholder="e.g. receptionist"
                            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                        />
                        <p className="text-xs text-muted-foreground mt-1">Lowercase letters, digits, and underscores only.</p>
                    </div>
                )}

                <FormSelect
                    id="agent-provider"
                    label="Provider"
                    options={providerOptions}
                    value={provider}
                    onChange={(e) => setProvider(e.target.value)}
                    tooltip="AI provider used for this agent's calls."
                />

                <div className="mb-4">
                    <div className="flex items-center gap-1.5 mb-1.5">
                        <label htmlFor="agent-voice" className="block text-sm font-medium">
                            Voice
                        </label>
                        <HelpTooltip content="Voice ID or name passed to the TTS provider. Leave blank to use the provider default." />
                    </div>
                    <input
                        id="agent-voice"
                        value={voice}
                        onChange={(e) => setVoice(e.target.value)}
                        placeholder="e.g. alloy, nova, en-US-JennyNeural"
                        className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                    />
                </div>

                <FormSelect
                    id="agent-audio-profile"
                    label="Audio Profile"
                    options={profileOptions}
                    value={audioProfile}
                    onChange={(e) => setAudioProfile(e.target.value)}
                    tooltip="Audio codec/transport profile. Leave blank to use the system default."
                />

                <div className="grid grid-cols-2 gap-4">
                    <FormInput
                        id="agent-extension"
                        label="Extension"
                        value={extension}
                        onChange={(e) => setExtension(e.target.value)}
                        placeholder="e.g. 100"
                        tooltip="Dialplan extension that routes to this agent (informational)."
                    />
                    <FormInput
                        id="agent-role-label"
                        label="Role Label"
                        value={roleLabel}
                        onChange={(e) => setRoleLabel(e.target.value)}
                        placeholder="e.g. Receptionist"
                        tooltip="Human-readable role shown on the card."
                    />
                </div>

                <div className="mb-4">
                    <FormLabel htmlFor="agent-greeting" tooltip="First words the agent speaks when a call connects. Use {caller_name} for the caller's name.">
                        Greeting
                    </FormLabel>
                    <input
                        id="agent-greeting"
                        value={greeting}
                        onChange={(e) => setGreeting(e.target.value)}
                        placeholder="Hi, how can I help you today?"
                        className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                    />
                </div>

                <div className="mb-4">
                    <FormLabel htmlFor="agent-prompt" tooltip="System prompt passed to the LLM. Use {company} as a placeholder for the business name.">
                        Prompt
                    </FormLabel>
                    <textarea
                        id="agent-prompt"
                        value={prompt}
                        onChange={(e) => setPrompt(e.target.value)}
                        rows={6}
                        placeholder="You are a helpful voice assistant…"
                        className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-y"
                    />
                </div>

                {!isNew && (
                    <div className="mb-4 flex items-center justify-between p-3 border border-border rounded-lg bg-card/50">
                        <div>
                            <p className="text-sm font-medium">Active</p>
                            <p className="text-xs text-muted-foreground">Inactive agents are excluded from call routing.</p>
                        </div>
                        <label className="relative inline-flex items-center cursor-pointer">
                            <input
                                type="checkbox"
                                className="sr-only peer"
                                checked={isActive === 1}
                                onChange={(e) => setIsActive(e.target.checked ? 1 : 0)}
                            />
                            <div className="w-9 h-5 bg-muted peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-ring rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-background after:border-border after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-primary"></div>
                        </label>
                    </div>
                )}

                {/* Advanced collapsible */}
                <div className="border border-border rounded-lg overflow-hidden">
                    <button
                        type="button"
                        onClick={() => setShowAdvanced(!showAdvanced)}
                        className="w-full flex items-center justify-between px-4 py-3 bg-muted/30 hover:bg-muted/50 transition-colors text-sm font-medium"
                    >
                        <span>Advanced</span>
                        {showAdvanced ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                    </button>
                    {showAdvanced && (
                        <div className="p-4 space-y-4">
                            <div className="mb-4">
                                <div className="flex items-center gap-1.5 mb-1.5">
                                    <label htmlFor="agent-tools-json" className="block text-sm font-medium">
                                        Tools JSON
                                    </label>
                                    <HelpTooltip content="JSON array or object of in-call tool overrides. Leave blank to use the provider/global defaults." />
                                </div>
                                <textarea
                                    id="agent-tools-json"
                                    value={toolsJson}
                                    onChange={(e) => setToolsJson(e.target.value)}
                                    onBlur={() => validateJson(toolsJson, setToolsJsonError)}
                                    rows={3}
                                    placeholder='["transfer", "hangup_call"]'
                                    className={`flex w-full rounded-md border px-3 py-2 text-sm font-mono shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-y bg-transparent ${toolsJsonError ? 'border-destructive' : 'border-input'}`}
                                />
                                {toolsJsonError && <p className="text-xs text-destructive mt-1">{toolsJsonError}</p>}
                            </div>

                            <div className="mb-4">
                                <div className="flex items-center gap-1.5 mb-1.5">
                                    <label htmlFor="agent-mcp-json" className="block text-sm font-medium">
                                        MCP JSON
                                    </label>
                                    <HelpTooltip content="JSON object of MCP server overrides for this agent." />
                                </div>
                                <textarea
                                    id="agent-mcp-json"
                                    value={mcpJson}
                                    onChange={(e) => setMcpJson(e.target.value)}
                                    onBlur={() => validateJson(mcpJson, setMcpJsonError)}
                                    rows={3}
                                    placeholder="{}"
                                    className={`flex w-full rounded-md border px-3 py-2 text-sm font-mono shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-y bg-transparent ${mcpJsonError ? 'border-destructive' : 'border-input'}`}
                                />
                                {mcpJsonError && <p className="text-xs text-destructive mt-1">{mcpJsonError}</p>}
                            </div>

                            <div className="mb-4">
                                <div className="flex items-center gap-1.5 mb-1.5">
                                    <label htmlFor="agent-extra-json" className="block text-sm font-medium">
                                        Extra JSON
                                    </label>
                                    <HelpTooltip content="JSON object of extra context fields (e.g. pipeline, background_music, pre_call_tools)." />
                                </div>
                                <textarea
                                    id="agent-extra-json"
                                    value={extraJson}
                                    onChange={(e) => setExtraJson(e.target.value)}
                                    onBlur={() => validateJson(extraJson, setExtraJsonError)}
                                    rows={3}
                                    placeholder='{"pipeline": "my_pipeline"}'
                                    className={`flex w-full rounded-md border px-3 py-2 text-sm font-mono shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-y bg-transparent ${extraJsonError ? 'border-destructive' : 'border-input'}`}
                                />
                                {extraJsonError && <p className="text-xs text-destructive mt-1">{extraJsonError}</p>}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </Modal>
    );
};

export default AgentForm;
