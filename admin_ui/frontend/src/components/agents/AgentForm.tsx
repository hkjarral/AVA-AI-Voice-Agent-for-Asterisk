import React, { useState, useEffect, useMemo } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import yaml from 'js-yaml';
import { Modal } from '../ui/Modal';
import { FormInput, FormSelect, FormLabel } from '../ui/FormComponents';
import HelpTooltip from '../ui/HelpTooltip';
import { isFullAgentProvider } from '../../utils/providerNaming';
import AgentToolPicker from './AgentToolPicker';
import {
    ToolDef, AgentToolState, parseAgentConfig, serializeAgentConfig, phaseOf, isToolChecked,
} from './agentToolConfig';
import { PromptToolHighlight } from '../ui/PromptToolHighlight';
import { canonicalToolName, type ToolStatus } from '../../utils/promptTools';
import { voiceControlState, type ProviderVoiceMeta } from '../../utils/agentVoice';

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
    const [disabledTools, setDisabledTools] = useState<Set<string>>(new Set());
    // Tool-reference highlighting for the prompt: detect every catalog tool name,
    // colour-code by in-call status for this agent.
    const knownToolNames = useMemo(() => catalog.map((t) => t.name), [catalog]);
    const toolStatusMap = useMemo(() => {
        // Accept legacy aliases (e.g. a stored 'transfer' → catalog 'blind_transfer').
        const inCallCanon = new Set([...toolState.inCallTools, ...toolState.inCallHttpTools].map(canonicalToolName));
        const map: Record<string, ToolStatus> = {};
        for (const t of catalog) {
            const checked = isToolChecked(toolState, t) || inCallCanon.has(t.name);
            map[t.name] = phaseOf(t) !== 'in_call'
                ? 'unavailable'
                : disabledTools.has(t.name)
                    ? 'unavailable'
                    : !checked
                        ? 'unavailable'
                        : t.is_global ? 'global' : 'context';
        }
        return map;
    }, [catalog, toolState, disabledTools]);
    const [providersRaw, setProvidersRaw] = useState<Record<string, unknown>>({});
    const [pipelinesRaw, setPipelinesRaw] = useState<Record<string, unknown>>({});
    const [availableProfiles, setAvailableProfiles] = useState<string[]>([]);
    const [voiceMeta, setVoiceMeta] = useState<ProviderVoiceMeta[] | null>(null);

    // Templates (create only)
    const [templates, setTemplates] = useState<AgentTemplate[]>([]);
    const [selectedTemplate, setSelectedTemplate] = useState('');

    const [saving, setSaving] = useState(false);

    useEffect(() => {
        if (!isOpen) return;
        loadConfig();
        loadCatalog();
        loadVoiceMeta();
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

            // Tools disabled in YAML (e.g. tools.google_calendar.enabled: false) are
            // rejected at runtime even if an agent lists them — mark them unavailable.
            const disabled = new Set<string>();
            const collectDisabled = (block: unknown) => {
                if (block && typeof block === 'object') {
                    for (const [name, cfg] of Object.entries(block as Record<string, unknown>)) {
                        if (cfg && typeof cfg === 'object' && (cfg as { enabled?: unknown }).enabled === false) {
                            disabled.add(canonicalToolName(name));
                        }
                    }
                }
            };
            collectDisabled(parsed.tools);
            collectDisabled((parsed as Record<string, unknown>).in_call_tools);
            setDisabledTools(disabled);
        } catch {
            // Non-blocking: dropdowns degrade gracefully to free-text
        }
    };

    const loadVoiceMeta = async () => {
        try {
            const res = await axios.get('/api/config/providers/meta');
            const providers = Array.isArray(res.data?.providers) ? res.data.providers : null;
            setVoiceMeta(providers);
        } catch {
            setVoiceMeta(null); // Voice control degrades to free text
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
        // Don't persist a voice the selected engine can't use: if the voice
        // control is disabled (pipeline / platform-managed / unsupported), a
        // previously saved value would otherwise ride along invisibly and
        // become active if the agent is later switched back (Codex on #503).
        const voiceControl = voiceControlState(voiceMeta, engineValue, voice);
        const effectiveVoice = voiceControl.control === 'disabled' ? null : (voice || null);
        setSaving(true);
        try {
            const baseBody: Record<string, unknown> = {
                display_name: displayName.trim(),
                provider: cfg.provider,
                voice: effectiveVoice,
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

    const updateNoInputOverride = (key: string, value: unknown) => {
        setToolState((state) => {
            const next = { ...state.noInput };
            if (value === '' || value === undefined) delete next[key];
            else next[key] = value;
            return { ...state, noInput: next };
        });
    };

    const updateNoInputNumberOverride = (
        key: 'initial_timeout_sec' | 'grace_timeout_sec' | 'max_check_ins',
        raw: string,
        minimum: number,
        maximum: number,
        integerOnly = false,
    ) => {
        if (raw === '') {
            updateNoInputOverride(key, '');
            return;
        }
        const value = Number(raw);
        if (!Number.isFinite(value) || value < minimum || value > maximum || (integerOnly && !Number.isInteger(value))) {
            return;
        }
        updateNoInputOverride(key, value);
    };

    const noInputNumber = (key: string): number | '' =>
        typeof toolState.noInput[key] === 'number' ? (toolState.noInput[key] as number) : '';

    const noInputString = (key: string): string =>
        typeof toolState.noInput[key] === 'string' ? (toolState.noInput[key] as string) : '';

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

                {(() => {
                    const vc = voiceControlState(voiceMeta, engineValue, voice);
                    const inputClass = "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring";
                    return (
                        <div className="mb-4">
                            <div className="flex items-center gap-1.5 mb-1.5">
                                <label htmlFor="agent-voice" className="block text-sm font-medium">
                                    Voice
                                </label>
                                <HelpTooltip content="Overrides the provider's default voice for this agent. Leave empty to use the provider's configured voice. Multiple agents can share one provider, each with its own voice." />
                            </div>
                            {vc.control === 'select' && (
                                <select
                                    id="agent-voice"
                                    value={voice}
                                    onChange={(e) => setVoice(e.target.value)}
                                    className={inputClass}
                                >
                                    {vc.options.map((o) => (
                                        <option key={o.id || '__default'} value={o.id}>{o.label}</option>
                                    ))}
                                </select>
                            )}
                            {vc.control === 'combo' && (
                                <>
                                    <input
                                        id="agent-voice"
                                        value={voice}
                                        onChange={(e) => setVoice(e.target.value)}
                                        placeholder="— provider default —"
                                        list="agent-voice-options"
                                        className={inputClass}
                                    />
                                    <datalist id="agent-voice-options">
                                        {vc.options.map((o) => (
                                            <option key={o.id} value={o.id}>{o.label}</option>
                                        ))}
                                    </datalist>
                                </>
                            )}
                            {vc.control === 'disabled' && (
                                <input
                                    id="agent-voice"
                                    value=""
                                    disabled
                                    placeholder={vc.note}
                                    className={`${inputClass} opacity-60 cursor-not-allowed`}
                                />
                            )}
                            {vc.control !== 'disabled' && vc.note && (
                                <p className="text-xs text-muted-foreground mt-1">{vc.note}</p>
                            )}
                            {vc.unrecognized && (
                                <p className="text-xs text-amber-600 dark:text-amber-500 mt-1">
                                    This voice is not in the provider's catalog — calls will fall back to the provider's default voice until you pick a valid one.
                                </p>
                            )}
                        </div>
                    );
                })()}

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
                    <PromptToolHighlight
                        id="agent-prompt"
                        value={prompt}
                        onChange={setPrompt}
                        knownNames={knownToolNames}
                        statusMap={toolStatusMap}
                        rows={6}
                        placeholder="You are a helpful voice assistant…"
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

                <details
                    className="mb-4 border border-border rounded-lg bg-card/50"
                >
                    <summary className="cursor-pointer px-3 py-3 text-sm font-medium">
                        Caller Inactivity Overrides
                    </summary>
                    <div className="px-3 pb-3 pt-1 space-y-4 border-t border-border">
                        <p className="text-xs text-muted-foreground">
                            Inbound calls inherit the global 30-second watchdog. Outbound calls stay disabled until this agent explicitly opts in.
                        </p>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <FormSelect
                                id="agent-no-input-enabled"
                                label="Watchdog"
                                options={[
                                    { value: '', label: 'Inherit global setting' },
                                    { value: 'enabled', label: 'Enabled for this agent' },
                                    { value: 'disabled', label: 'Disabled for this agent' },
                                ]}
                                value={toolState.noInput.enabled === true ? 'enabled' : toolState.noInput.enabled === false ? 'disabled' : ''}
                                onChange={(e) => updateNoInputOverride('enabled', e.target.value === '' ? '' : e.target.value === 'enabled')}
                                tooltip="Overrides the global caller inactivity policy for this agent."
                            />
                            <FormSelect
                                id="agent-no-input-outbound"
                                label="Outbound Calls"
                                options={[
                                    { value: 'disabled', label: 'Disabled (default)' },
                                    { value: 'enabled', label: 'Enable for this agent' },
                                ]}
                                value={toolState.noInput.outbound_enabled === true ? 'enabled' : 'disabled'}
                                onChange={(e) => updateNoInputOverride('outbound_enabled', e.target.value === 'enabled' ? true : '')}
                                tooltip="Outbound campaigns never inherit this globally. This agent must explicitly enable the watchdog."
                            />
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <FormInput
                                id="agent-no-input-initial"
                                label="Initial Silence (sec)"
                                type="number"
                                min="1"
                                max="3600"
                                value={noInputNumber('initial_timeout_sec')}
                                placeholder="Inherit: 30"
                                onChange={(e) => updateNoInputNumberOverride('initial_timeout_sec', e.target.value, 1, 3600)}
                            />
                            <FormInput
                                id="agent-no-input-grace"
                                label="Reply Grace (sec)"
                                type="number"
                                min="1"
                                max="3600"
                                value={noInputNumber('grace_timeout_sec')}
                                placeholder="Inherit: 15"
                                onChange={(e) => updateNoInputNumberOverride('grace_timeout_sec', e.target.value, 1, 3600)}
                            />
                            <FormInput
                                id="agent-no-input-attempts"
                                label="Check-In Attempts"
                                type="number"
                                min="0"
                                max="10"
                                step="1"
                                value={noInputNumber('max_check_ins')}
                                placeholder="Inherit: 1"
                                onChange={(e) => updateNoInputNumberOverride('max_check_ins', e.target.value, 0, 10, true)}
                            />
                        </div>

                        <FormInput
                            id="agent-no-input-check-in"
                            label="Check-In Message"
                            value={noInputString('check_in_message')}
                            placeholder="Inherit: Are you still there?"
                            onChange={(e) => updateNoInputOverride('check_in_message', e.target.value)}
                            tooltip="Spoken through the selected provider or pipeline in this agent's configured voice."
                        />
                        <FormInput
                            id="agent-no-input-final"
                            label="Final Message"
                            value={noInputString('final_message')}
                            placeholder="Inherit global final message"
                            onChange={(e) => updateNoInputOverride('final_message', e.target.value)}
                            tooltip="Spoken immediately before the engine ends an inactive call."
                        />
                    </div>
                </details>

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
