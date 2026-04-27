import { useState, useEffect, useCallback } from 'react';
import { Plus, Trash2, AlertTriangle, Info, Loader2, ChevronDown, ChevronRight, ExternalLink } from 'lucide-react';
import axios from 'axios';
import { Modal } from '../ui/Modal';
import { ConfigCard } from '../ui/ConfigCard';

type ModelType = 'llm' | 'tts' | 'stt';

interface CustomModelEntry {
    id: string;
    name: string;
    download_url: string;
    config_url?: string;
    expected_sha256?: string;
    chat_format?: string;
    notes?: string;
    model_path: string;
    size_mb?: number;
    size_display?: string;
    source: 'user';
    _type: ModelType;
}

interface IntrospectResult {
    ok: boolean;
    error?: string;
    metadata?: {
        architecture?: string | null;
        name?: string | null;
        param_count?: number | null;
        param_count_display?: string | null;
        quantization?: string | null;
        context_length?: number | null;
        block_count?: number | null;
        embedding_length?: number | null;
    };
    compatibility?: {
        arch_supported?: boolean;
        arch_warning?: string | null;
        estimated_ram_gb?: number;
        file_size_gb?: number;
    };
}

interface FormState {
    type: ModelType;
    name: string;
    download_url: string;
    config_url: string;
    expected_sha256: string;
    chat_format: string;
    notes: string;
}

const EMPTY_FORM: FormState = {
    type: 'llm',
    name: '',
    download_url: '',
    config_url: '',
    expected_sha256: '',
    chat_format: '',
    notes: '',
};

interface Props {
    /** Called after a custom model is added or removed so the parent can
     *  refresh its catalog view. */
    onChanged?: () => void;
}

export const CustomModelsPanel = ({ onChanged }: Props) => {
    const [enabled, setEnabled] = useState<boolean | null>(null);
    const [loading, setLoading] = useState(true);
    const [models, setModels] = useState<CustomModelEntry[]>([]);
    const [showForm, setShowForm] = useState(false);
    const [form, setForm] = useState<FormState>(EMPTY_FORM);
    const [submitting, setSubmitting] = useState(false);
    const [submitError, setSubmitError] = useState<string | null>(null);
    const [fetchError, setFetchError] = useState<string | null>(null);
    const [expandedIntrospect, setExpandedIntrospect] = useState<Record<string, IntrospectResult | 'loading' | 'closed'>>({});

    const refresh = useCallback(async () => {
        setLoading(true);
        try {
            const res = await axios.get('/api/custom-models');
            setEnabled(res.data.enabled);
            setModels(res.data.models || []);
            setFetchError(null);
        } catch (e: any) {
            // Don't reset enabled to false on transient network errors —
            // misrepresents backend state and would hide configured entries.
            // Only update enabled when we actually got a response.
            console.error('Failed to load custom models', e);
            setFetchError(e?.response?.data?.detail || e?.message || 'Failed to load');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { refresh(); }, [refresh]);

    const toggle = async (next: boolean) => {
        try {
            await axios.post('/api/custom-models/enabled', { enabled: next });
            setEnabled(next);
            if (next) refresh();
            onChanged?.();
        } catch (e) {
            console.error('Failed to toggle custom models', e);
        }
    };

    const submitForm = async () => {
        setSubmitting(true);
        setSubmitError(null);
        try {
            const payload: Partial<FormState> = {
                type: form.type,
                name: form.name.trim(),
                download_url: form.download_url.trim(),
            };
            if (form.config_url.trim()) payload.config_url = form.config_url.trim();
            if (form.expected_sha256.trim()) payload.expected_sha256 = form.expected_sha256.trim();
            if (form.chat_format.trim() && form.type === 'llm') payload.chat_format = form.chat_format.trim();
            if (form.notes.trim()) payload.notes = form.notes.trim();

            await axios.post('/api/custom-models', payload);
            setShowForm(false);
            setForm(EMPTY_FORM);
            await refresh();
            onChanged?.();
        } catch (e: any) {
            setSubmitError(e?.response?.data?.detail || e?.message || 'Failed to add model');
        } finally {
            setSubmitting(false);
        }
    };

    const removeEntry = async (id: string) => {
        if (!window.confirm('Remove this custom model entry and delete its file from disk?')) return;
        try {
            await axios.delete(`/api/custom-models/${id}`);
            await refresh();
            onChanged?.();
        } catch (e: any) {
            window.alert(e?.response?.data?.detail || 'Failed to delete');
        }
    };

    const introspect = async (entry: CustomModelEntry) => {
        const current = expandedIntrospect[entry.id];
        if (current && current !== 'closed') {
            // Already expanded → collapse
            setExpandedIntrospect(prev => ({ ...prev, [entry.id]: 'closed' }));
            return;
        }
        setExpandedIntrospect(prev => ({ ...prev, [entry.id]: 'loading' }));
        try {
            const res = await axios.post('/api/custom-models/introspect', {
                type: entry._type,
                model_path: entry.model_path,
            });
            setExpandedIntrospect(prev => ({ ...prev, [entry.id]: res.data }));
        } catch (e: any) {
            setExpandedIntrospect(prev => ({
                ...prev,
                [entry.id]: { ok: false, error: e?.response?.data?.detail || 'Inspection failed' }
            }));
        }
    };

    if (loading && enabled === null) {
        return null; // initial load — don't render until we know state
    }

    return (
        <ConfigCard className="mt-6">
            <div className="flex items-start justify-between gap-4 mb-4">
                <div>
                    <h2 className="text-lg font-semibold flex items-center gap-2">
                        Community Models
                        <span className="text-xs px-2 py-0.5 rounded bg-amber-500/15 text-amber-700 dark:text-amber-400 font-medium">
                            Best-effort
                        </span>
                    </h2>
                    <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
                        Add LLM, TTS, or STT models that aren't in the curated catalog by pasting
                        a download URL. These are unsupported — if the model works, great; if not,
                        please open an issue. Useful for trying new GGUF releases (e.g. Qwen3-4B)
                        before they're added to the official catalog.
                    </p>
                </div>
                <label className="flex items-center gap-2 cursor-pointer shrink-0 mt-1">
                    <input
                        type="checkbox"
                        checked={!!enabled}
                        onChange={(e) => toggle(e.target.checked)}
                        className="w-4 h-4 rounded border-border accent-primary"
                    />
                    <span className="text-sm font-medium">{enabled ? 'Enabled' : 'Disabled'}</span>
                </label>
            </div>

            {fetchError && (
                <div className="bg-destructive/10 border border-destructive/30 rounded p-2 text-xs text-destructive mb-3 flex items-center justify-between gap-2">
                    <span>Couldn't reach backend: {fetchError}</span>
                    <button onClick={refresh} className="underline">Retry</button>
                </div>
            )}

            {!enabled && (
                <p className="text-sm text-muted-foreground italic">
                    Toggle on to enable custom model entries. Disabled by default to keep the
                    Models page focused on curated, tested options.
                </p>
            )}

            {enabled && (
                <>
                    <div className="flex items-center justify-between mb-3">
                        <span className="text-sm text-muted-foreground">
                            {models.length === 0
                                ? 'No custom models added yet.'
                                : `${models.length} custom model${models.length === 1 ? '' : 's'}`}
                        </span>
                        <button
                            onClick={() => { setForm(EMPTY_FORM); setSubmitError(null); setShowForm(true); }}
                            className="px-3 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors flex items-center gap-2 text-sm"
                        >
                            <Plus className="w-4 h-4" />
                            Add custom model
                        </button>
                    </div>

                    <div className="space-y-2">
                        {models.map((m) => {
                            const introspectState = expandedIntrospect[m.id];
                            const expanded = introspectState && introspectState !== 'closed';
                            return (
                                <div key={m.id} className="border border-border rounded-md p-3 bg-muted/20">
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2 flex-wrap">
                                                <span className="font-medium">{m.name}</span>
                                                <span className="text-xs px-1.5 py-0.5 rounded bg-muted text-muted-foreground uppercase">
                                                    {m._type}
                                                </span>
                                                <span className="text-xs px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-700 dark:text-amber-400">
                                                    Community
                                                </span>
                                            </div>
                                            <p className="text-xs text-muted-foreground mt-1 truncate flex items-center gap-1">
                                                <ExternalLink className="w-3 h-3 shrink-0" />
                                                <a href={m.download_url} target="_blank" rel="noreferrer" className="hover:underline truncate">
                                                    {m.download_url}
                                                </a>
                                            </p>
                                            {m.notes && (
                                                <p className="text-xs text-muted-foreground italic mt-1">{m.notes}</p>
                                            )}
                                        </div>
                                        <div className="flex items-center gap-1 shrink-0">
                                            {m._type === 'llm' && (
                                                <button
                                                    onClick={() => introspect(m)}
                                                    className="p-1.5 rounded hover:bg-accent text-muted-foreground hover:text-foreground"
                                                    title="Inspect GGUF header (requires the file to be downloaded)"
                                                >
                                                    {introspectState === 'loading'
                                                        ? <Loader2 className="w-4 h-4 animate-spin" />
                                                        : expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                                                </button>
                                            )}
                                            <button
                                                onClick={() => removeEntry(m.id)}
                                                className="p-1.5 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive"
                                                title="Remove entry and delete file"
                                            >
                                                <Trash2 className="w-4 h-4" />
                                            </button>
                                        </div>
                                    </div>

                                    {expanded && introspectState !== 'loading' && (
                                        <IntrospectPanel result={introspectState as IntrospectResult} />
                                    )}
                                </div>
                            );
                        })}
                    </div>
                </>
            )}

            <Modal
                isOpen={showForm}
                onClose={() => setShowForm(false)}
                title="Add custom model"
                size="lg"
                footer={
                    <>
                        <button
                            onClick={() => setShowForm(false)}
                            disabled={submitting}
                            className="px-4 py-2 rounded-md border border-border hover:bg-accent text-sm"
                        >
                            Cancel
                        </button>
                        <button
                            onClick={submitForm}
                            disabled={submitting || !form.name.trim() || !form.download_url.trim()}
                            className="px-4 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 text-sm flex items-center gap-2"
                        >
                            {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
                            Add
                        </button>
                    </>
                }
            >
                <div className="space-y-4">
                    <div className="bg-amber-500/10 border border-amber-500/30 rounded p-3 flex gap-2">
                        <AlertTriangle className="w-4 h-4 text-amber-600 dark:text-amber-500 shrink-0 mt-0.5" />
                        <p className="text-xs text-amber-700 dark:text-amber-400">
                            Custom models are best-effort and unsupported. We verify the file
                            downloads and (for LLMs) that it's a valid GGUF, but cannot guarantee
                            it will load or perform correctly.
                        </p>
                    </div>

                    <FormField label="Type">
                        <select
                            value={form.type}
                            onChange={(e) => setForm(f => ({ ...f, type: e.target.value as ModelType }))}
                            className="w-full px-3 py-2 rounded-md border border-border bg-background text-sm"
                        >
                            <option value="llm">LLM (GGUF, llama.cpp)</option>
                            <option value="tts">TTS</option>
                            <option value="stt">STT</option>
                        </select>
                    </FormField>

                    <FormField label="Display name" required>
                        <input
                            type="text"
                            value={form.name}
                            onChange={(e) => setForm(f => ({ ...f, name: e.target.value }))}
                            placeholder="e.g. Qwen3-4B Instruct Q4_K_M"
                            className="w-full px-3 py-2 rounded-md border border-border bg-background text-sm"
                            maxLength={120}
                        />
                    </FormField>

                    <FormField label="Download URL" required hint="HTTPS direct link to the model file (HuggingFace resolve URL works well)">
                        <input
                            type="url"
                            value={form.download_url}
                            onChange={(e) => setForm(f => ({ ...f, download_url: e.target.value }))}
                            placeholder="https://huggingface.co/bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF/resolve/main/..."
                            className="w-full px-3 py-2 rounded-md border border-border bg-background text-sm"
                        />
                    </FormField>

                    {form.type === 'tts' && (
                        <FormField label="Config URL (optional)" hint="Companion .json config for Piper-style TTS voices">
                            <input
                                type="url"
                                value={form.config_url}
                                onChange={(e) => setForm(f => ({ ...f, config_url: e.target.value }))}
                                className="w-full px-3 py-2 rounded-md border border-border bg-background text-sm"
                            />
                        </FormField>
                    )}

                    {form.type === 'llm' && (
                        <FormField label="Chat format (optional)" hint="Hint for llama.cpp's prompt formatter — chatml, llama-3, mistral-instruct, gemma, etc.">
                            <input
                                type="text"
                                value={form.chat_format}
                                onChange={(e) => setForm(f => ({ ...f, chat_format: e.target.value }))}
                                placeholder="chatml"
                                className="w-full px-3 py-2 rounded-md border border-border bg-background text-sm"
                            />
                        </FormField>
                    )}

                    <FormField label="Expected SHA256 (optional)" hint="64-char hex; verified after download to detect corruption">
                        <input
                            type="text"
                            value={form.expected_sha256}
                            onChange={(e) => setForm(f => ({ ...f, expected_sha256: e.target.value }))}
                            placeholder="a1b2c3..."
                            className="w-full px-3 py-2 rounded-md border border-border bg-background text-sm font-mono"
                            maxLength={64}
                        />
                    </FormField>

                    <FormField label="Notes (optional)">
                        <textarea
                            value={form.notes}
                            onChange={(e) => setForm(f => ({ ...f, notes: e.target.value }))}
                            rows={2}
                            maxLength={500}
                            className="w-full px-3 py-2 rounded-md border border-border bg-background text-sm"
                        />
                    </FormField>

                    {submitError && (
                        <div className="bg-destructive/10 border border-destructive/30 rounded p-3 text-sm text-destructive">
                            {submitError}
                        </div>
                    )}
                </div>
            </Modal>
        </ConfigCard>
    );
};

const FormField = ({ label, required, hint, children }: { label: string; required?: boolean; hint?: string; children: React.ReactNode }) => (
    <div>
        <label className="block text-sm font-medium mb-1">
            {label}
            {required && <span className="text-destructive ml-1">*</span>}
        </label>
        {children}
        {hint && <p className="text-xs text-muted-foreground mt-1">{hint}</p>}
    </div>
);

const IntrospectPanel = ({ result }: { result: IntrospectResult }) => {
    if (!result.ok) {
        return (
            <div className="mt-3 pt-3 border-t border-border text-sm text-muted-foreground flex items-start gap-2">
                <Info className="w-4 h-4 mt-0.5 shrink-0" />
                <div>
                    <p className="font-medium text-foreground">Couldn't read GGUF header</p>
                    <p className="text-xs mt-0.5">{result.error || 'Make sure the model has been downloaded.'}</p>
                </div>
            </div>
        );
    }

    const m = result.metadata!;
    const c = result.compatibility!;
    const fields: [string, string | number | null | undefined][] = [
        ['Architecture', m.architecture],
        ['Name (from header)', m.name],
        ['Parameters', m.param_count_display],
        ['Quantization', m.quantization],
        ['Context length', m.context_length?.toLocaleString()],
        ['Layers (block count)', m.block_count],
        ['Embedding size', m.embedding_length],
        ['File size', c.file_size_gb ? `${c.file_size_gb} GB` : null],
        ['Estimated RAM', c.estimated_ram_gb ? `${c.estimated_ram_gb} GB` : null],
    ];

    return (
        <div className="mt-3 pt-3 border-t border-border space-y-2">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                {fields.filter(([, v]) => v != null && v !== '').map(([k, v]) => (
                    <div key={k} className="flex justify-between gap-2 col-span-1">
                        <dt className="text-muted-foreground">{k}</dt>
                        <dd className="font-mono text-right">{v}</dd>
                    </div>
                ))}
            </dl>
            {c.arch_warning && (
                <div className="flex items-start gap-2 bg-amber-500/10 border border-amber-500/30 rounded p-2 text-xs text-amber-700 dark:text-amber-400">
                    <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                    <span>{c.arch_warning}</span>
                </div>
            )}
        </div>
    );
};
