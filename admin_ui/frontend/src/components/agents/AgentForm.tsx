import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import yaml from 'js-yaml';
import { Modal } from '../ui/Modal';
import { FormInput, FormSelect, FormLabel } from '../ui/FormComponents';
import HelpTooltip from '../ui/HelpTooltip';
import { isFullAgentProvider } from '../../utils/providerNaming';
import AgentToolPicker from './AgentToolPicker';
import {
    ToolDef, AgentToolState, parseAgentConfig, serializeAgentConfig,
} from './agentToolConfig';

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
    email_recipient?: string;
    email_from?: string;
    email_enabled?: boolean | null;
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
    const [voice, setVoice] = useState('');
    const [audioProfile, setAudioProfile] = useState('');
    const [extension, setExtension] = useState('');
    const [roleLabel, setRoleLabel] = useState('');
    const [greeting, setGreeting] = useState('');
    const [prompt, setPrompt] = useState('');
    const [notes, setNotes] = useState('');
    const [isActive, setIsActive] = useState(1);
    const [emailRecipient, setEmailRecipient] = useState('');
    const [emailFrom, setEmailFrom] = useState('');
    // Tri-state as a select value: '' = inherit (null), 'enabled' = true, 'disabled' = false.
    const [emailEnabled, setEmailEnabled] = useState('');

    // Tool/engine config — single source of truth, round-tripped losslessly via the helper.
    const [toolState, setToolState] = useState<AgentToolState>(() => parseAgentConfig(null));

    // Tool catalog (for the picker) + engine option sources.
    const [catalog, setCatalog] = useState<ToolDef[]>([]);
    const [catalogError, setCatalogError] = useState(false);
    const [providersRaw, setProvidersRaw] = useState<Record<string, unknown>>({});
    const [pipelinesRaw, setPipelinesRaw] = useState<Record<string, unknown>>({});
    const [availableProfiles, setAvailableProfiles] = useState<string[]>([]);

    // Templates (create only)
    const [templates, setTemplates] = useState<AgentTemplate[]>([]);
    const [selectedTemplate, setSelectedTemplate] = useState('');

    const [saving, setSaving] = useState(false);

    useEffect(() => {
        if (!isOpen) return;
        loadConfig();
        loadCatalog();
        if (isNew) loadTemplates();
    }, [isOpen, isNew]);

    useEffect(() => {
        if (!isOpen) return;
        if (agent) {
            setDisplayName(agent.display_name);
            setSlug(agent.slug);
            setSlugManuallyEdited(false);
            setVoice(agent.voice || '');
            setAudioProfile(agent.audio_profile || '');
            setExtension(agent.extension || '');
            setRoleLabel(agent.role_label || '');
            setGreeting(agent.greeting || '');
            setPrompt(agent.prompt || '');
            setNotes(agent.notes || '');
            setIsActive(agent.is_active);
            setEmailRecipient(agent.email_recipient || '');
            setEmailFrom(agent.email_from || '');
            setEmailEnabled(
                agent.email_enabled == null ? '' : (agent.email_enabled ? 'enabled' : 'disabled'),
            );
            setToolState(parseAgentConfig(agent));
        } else {
            setDisplayName('');
            setSlug('');
            setSlugManuallyEdited(false);
            setVoice('');
            setAudioProfile('');
            setExtension('');
            setRoleLabel('');
            setGreeting('Hi, how can I help you today?');
            setPrompt('You are a helpful voice assistant.');
            setNotes('');
            setIsActive(1);
            setEmailRecipient('');
            setEmailFrom('');
            setEmailEnabled('');
            setToolState(parseAgentConfig(null));
            setSelectedTemplate('');
        }
    }, [isOpen, agent]);

    const loadConfig = async () => {
        try {
            const res = await axios.get('/api/config/yaml');
            if (res.data.yaml_error) return;
            const parsed = yaml.load(res.data.content) as Record<string, unknown>;
            if (!parsed) return;

            const providersBlock = (parsed.providers as Record<string, unknown>) || {};
            setProvidersRaw(providersBlock);
            setPipelinesRaw((parsed.pipelines as Record<string, unknown>) || {});

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

    const loadCatalog = async () => {
        try {
            const res = await axios.get('/api/tools/catalog');
            const tools = Array.isArray(res.data?.tools) ? res.data.tools : [];
            setCatalog(tools);
            setCatalogError(false);
        } catch {
            setCatalog([]);
            setCatalogError(true);
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

    const handleSubmit = async () => {
        if (!displayName.trim()) { toast.error('Display name is required'); return; }
        if (isNew && !slug.trim()) { toast.error('Slug is required'); return; }
        if (!toolState.provider && !toolState.pipeline) {
            toast.error('Choose an AI engine (a provider or a pipeline)'); return;
        }

        const cfg = serializeAgentConfig(toolState);
        setSaving(true);
        try {
            const baseBody: Record<string, unknown> = {
                display_name: displayName.trim(),
                provider: cfg.provider,
                voice: voice || null,
                audio_profile: audioProfile || null,
                extension: extension || null,
                role_label: roleLabel || null,
                greeting: greeting || '',
                prompt: prompt || '',
                notes: notes || null,
                tools_json: cfg.tools_json,
                mcp_json: cfg.mcp_json,
                extra_json: cfg.extra_json,
                email_recipient: emailRecipient || null,
                email_from: emailFrom || null,
                // Tri-state: '' means inherit — send explicit null (PATCH clears the column),
                // never false. 'enabled' -> true, 'disabled' -> false.
                email_enabled: emailEnabled === '' ? null : emailEnabled === 'enabled',
            };

            if (isNew) {
                await axios.post('/api/agents', { ...baseBody, slug: slug.trim() });
                toast.success('Agent created');
            } else {
                await axios.patch(`/api/agents/${agent!.slug}`, { ...baseBody, is_active: isActive });
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

    const engineOptions = [
        { value: '', label: '— select provider or pipeline —' },
        ...Object.keys(pipelinesRaw).sort().map((name) => ({
            value: `pipeline:${name}`, label: `[Pipeline] ${name}`,
        })),
        ...Object.entries(providersRaw)
            .sort(([a], [b]) => a.localeCompare(b))
            .filter(([name, p]) => isFullAgentProvider(p, name))
            .filter(([name, p]) => (p as Record<string, unknown>).enabled !== false || toolState.provider === name)
            .map(([name]) => ({ value: `provider:${name}`, label: `[Provider] ${name}` })),
    ];

    const engineValue = toolState.pipeline
        ? `pipeline:${toolState.pipeline}`
        : (toolState.provider ? `provider:${toolState.provider}` : '');

    const handleEngineChange = (raw: string) => {
        if (!raw) setToolState((s) => ({ ...s, provider: '', pipeline: '' }));
        else if (raw.startsWith('pipeline:')) setToolState((s) => ({ ...s, pipeline: raw.slice('pipeline:'.length), provider: '' }));
        else if (raw.startsWith('provider:')) setToolState((s) => ({ ...s, provider: raw.slice('provider:'.length), pipeline: '' }));
    };

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
                    id="agent-engine"
                    label="AI Engine"
                    options={engineOptions}
                    value={engineValue}
                    onChange={(e) => handleEngineChange(e.target.value)}
                    tooltip="Choose a monolithic provider or a modular pipeline. They are mutually exclusive — picking one clears the other."
                />

                <div className="mb-4">
                    <div className="flex items-center gap-1.5 mb-1.5">
                        <label htmlFor="agent-voice" className="block text-sm font-medium">
                            Voice (display-only)
                        </label>
                        <HelpTooltip content="Voice is configured on the provider, not per agent — this field is informational and does not change the call voice." />
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

                <div className="mb-4">
                    <FormLabel htmlFor="agent-notes" tooltip="Internal notes about this agent — not used at runtime.">
                        Notes
                    </FormLabel>
                    <textarea
                        id="agent-notes"
                        value={notes}
                        onChange={(e) => setNotes(e.target.value)}
                        rows={3}
                        placeholder="Internal notes about this agent…"
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

                <div className="mb-2">
                    <FormInput
                        id="agent-background-music"
                        label="Background Music"
                        value={toolState.backgroundMusic}
                        onChange={(e) => setToolState((s) => ({ ...s, backgroundMusic: e.target.value }))}
                        placeholder="e.g. jingle"
                        tooltip="Asterisk music-on-hold class to play during the call. Leave blank for none."
                    />
                </div>

                <div className="grid grid-cols-2 gap-4">
                    <FormInput
                        id="agent-email-recipient"
                        label="Email Recipient"
                        value={emailRecipient}
                        onChange={(e) => setEmailRecipient(e.target.value)}
                        placeholder="e.g. ops@example.com"
                        tooltip="Email address that receives this agent's call summaries. Overrides the global/per-context recipient. Leave blank to inherit."
                    />
                    <FormInput
                        id="agent-email-from"
                        label="Email From Address"
                        value={emailFrom}
                        onChange={(e) => setEmailFrom(e.target.value)}
                        placeholder="e.g. ava@example.com"
                        tooltip="From address for this agent's call-summary emails. Leave blank to inherit the global/per-context setting."
                    />
                </div>

                <FormSelect
                    id="agent-email-enabled"
                    label="Email Summaries"
                    options={[
                        { value: '', label: 'Inherit' },
                        { value: 'enabled', label: 'Enabled' },
                        { value: 'disabled', label: 'Disabled' },
                    ]}
                    value={emailEnabled}
                    onChange={(e) => setEmailEnabled(e.target.value)}
                    tooltip="Whether this agent sends call-summary emails. 'Inherit' uses the global/per-context setting; 'Enabled'/'Disabled' override it for this agent."
                />

                <AgentToolPicker
                    catalog={catalog}
                    catalogError={catalogError}
                    state={toolState}
                    onChange={setToolState}
                />
            </div>
        </Modal>
    );
};

export default AgentForm;
