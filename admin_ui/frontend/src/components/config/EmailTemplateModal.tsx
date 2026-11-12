import { useEffect, useMemo, useRef, useState } from 'react';
import Editor from '@monaco-editor/react';
import { toast } from 'sonner';
import axios from 'axios';
import { Modal } from '../ui/Modal';

type ToolName = 'send_email_summary' | 'request_transcript';

interface EmailTemplateModalProps {
    isOpen: boolean;
    onClose: () => void;
    tool: ToolName;
    // Current config values
    currentTemplate: string | null;
    includeTranscript: boolean;
    // Defaults/vars
    defaultTemplate: string;
    variableNames: string[];
    defaultsStatusText: string;
    onReloadDefaults: () => Promise<void>;
    // Persist changes into parent config
    onSave: (nextTemplate: string | null) => Promise<void> | void;
}

export const EmailTemplateModal = ({
    isOpen,
    onClose,
    tool,
    currentTemplate,
    includeTranscript,
    defaultTemplate,
    variableNames,
    defaultsStatusText,
    onReloadDefaults,
    onSave,
}: EmailTemplateModalProps) => {
    const title = tool === 'send_email_summary' ? 'Send Email Summary – Email Template' : 'Request Transcript – Email Template';

    const initialCustomize = !!(currentTemplate && currentTemplate.trim());
    const [customize, setCustomize] = useState(initialCustomize);
    const [draft, setDraft] = useState<string>(currentTemplate?.toString() || '');

    const [previewing, setPreviewing] = useState(false);
    const [previewHtml, setPreviewHtml] = useState('');
    const [previewError, setPreviewError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);

    const [activeTab, setActiveTab] = useState<'template' | 'preview'>('template');

    const editorRef = useRef<any>(null);
    const monacoRef = useRef<any>(null);

    const effectiveDefault = (defaultTemplate || '').trim();

    // Reset draft when opening (but don't clobber user edits mid-session).
    useEffect(() => {
        if (!isOpen) return;
        const c = (currentTemplate || '').trim();
        const enabled = !!c;
        setCustomize(enabled);
        setDraft(enabled ? currentTemplate || '' : (effectiveDefault ? effectiveDefault : ''));
        setPreviewHtml('');
        setPreviewError(null);
        setPreviewing(false);
        setActiveTab('template');
    }, [isOpen]); // intentionally omit deps to keep "open = snapshot" behavior

    const variableChips = useMemo(() => {
        return (variableNames || []).filter(Boolean).slice().sort();
    }, [variableNames]);

    const insertAtCursor = (text: string) => {
        const editor = editorRef.current;
        if (!editor) return;
        const selection = editor.getSelection();
        const range = selection || editor.getModel()?.getFullModelRange();
        if (!range) return;
        editor.executeEdits('insert-variable', [
            {
                range,
                text,
                forceMoveMarkers: true,
            },
        ]);
        editor.focus();
    };

    const handlePreview = async () => {
        try {
            setPreviewError(null);
            setPreviewHtml('');
            setPreviewing(true);
            const htmlTemplate = customize ? (draft || '') : null;
            const res = await axios.post('/api/tools/email-templates/preview', {
                tool,
                html_template: htmlTemplate,
                include_transcript: includeTranscript,
            });
            if (res.data?.success) {
                setPreviewHtml(res.data.html || '');
                setActiveTab('preview');
                return;
            }
            setPreviewError(res.data?.error || 'Preview failed.');
            setActiveTab('preview');
        } catch (e: any) {
            setPreviewError(e?.response?.data?.detail || e?.message || 'Preview failed.');
            setActiveTab('preview');
        } finally {
            setPreviewing(false);
        }
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            if (!customize) {
                await onSave(null);
                toast.success('Using default template');
                onClose();
                return;
            }
            const next = (draft || '').trim();
            if (!next) {
                await onSave(null);
                toast.success('Using default template');
                onClose();
                return;
            }
            await onSave(draft);
            toast.success('Template saved. Restart AI Engine to apply.');
            onClose();
        } catch (e: any) {
            toast.error('Failed to save template', {
                description: e?.response?.data?.detail || e?.message || 'Unknown error',
            });
        } finally {
            setSaving(false);
        }
    };

    const editorValue = customize ? draft : effectiveDefault;
    const editorReadOnly = !customize;

    return (
        <Modal
            isOpen={isOpen}
            onClose={onClose}
            title={title}
            size="full"
            footer={
                <div className="flex items-center justify-between w-full gap-3">
                    <div className="text-xs text-muted-foreground truncate">{defaultsStatusText}</div>
                    <div className="flex items-center gap-2">
                        <button
                            type="button"
                            onClick={async () => {
                                await onReloadDefaults();
                            }}
                            className="px-3 py-1 text-xs border rounded hover:bg-accent"
                        >
                            Reload Defaults
                        </button>
                        <button
                            type="button"
                            onClick={onClose}
                            className="px-4 py-2 border rounded hover:bg-accent"
                        >
                            Cancel
                        </button>
                        <button
                            type="button"
                            onClick={handleSave}
                            disabled={saving}
                            className="px-4 py-2 bg-primary text-primary-foreground rounded hover:bg-primary/90"
                        >
                            {saving ? 'Saving…' : 'Save'}
                        </button>
                    </div>
                </div>
            }
        >
            <div className="space-y-4">
                <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3">
                    <div className="flex items-center gap-4">
                        <label className="flex items-center gap-2 text-sm">
                            <input
                                type="checkbox"
                                checked={customize}
                                onChange={(e) => {
                                    const enabled = e.target.checked;
                                    setCustomize(enabled);
                                    if (enabled) {
                                        setDraft((currentTemplate || '').trim() ? (currentTemplate as string) : effectiveDefault);
                                    }
                                }}
                            />
                            Customize (override `html_template`)
                        </label>
                        <button
                            type="button"
                            onClick={() => {
                                if (!effectiveDefault) {
                                    toast.error('Default template not loaded yet.');
                                    return;
                                }
                                setCustomize(true);
                                setDraft(effectiveDefault);
                            }}
                            className="px-3 py-1 text-xs border rounded hover:bg-accent"
                            title="Copy the default template into your custom template"
                        >
                            Start From Default
                        </button>
                        <button
                            type="button"
                            onClick={handlePreview}
                            disabled={previewing}
                            className="px-3 py-1 text-xs border rounded hover:bg-accent disabled:opacity-50"
                        >
                            {previewing ? 'Previewing…' : 'Preview'}
                        </button>
                    </div>

                    <div className="flex items-center gap-2">
                        <button
                            type="button"
                            onClick={() => setActiveTab('template')}
                            className={`px-3 py-1 text-xs border rounded ${activeTab === 'template' ? 'bg-accent' : 'hover:bg-accent'}`}
                        >
                            Template
                        </button>
                        <button
                            type="button"
                            onClick={() => setActiveTab('preview')}
                            className={`px-3 py-1 text-xs border rounded ${activeTab === 'preview' ? 'bg-accent' : 'hover:bg-accent'}`}
                            disabled={!previewHtml && !previewError}
                            title={!previewHtml && !previewError ? 'Run Preview first' : undefined}
                        >
                            Preview
                        </button>
                    </div>
                </div>

                {!!variableChips.length && (
                    <div className="space-y-2">
                        <div className="text-xs text-muted-foreground">
                            Click to insert a variable (Jinja2): <span className="font-mono">{'{{ variable }}'}</span>
                        </div>
                        <div className="flex flex-wrap gap-2">
                            {variableChips.map((v) => (
                                <button
                                    key={v}
                                    type="button"
                                    className="px-2 py-1 text-xs border rounded hover:bg-accent font-mono"
                                    onClick={() => insertAtCursor(`{{ ${v} }}`)}
                                    title={`Insert {{ ${v} }}`}
                                >
                                    {v}
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    {/* Editor */}
                    <div className={activeTab === 'preview' ? 'hidden lg:block' : ''}>
                        <div className="border border-border rounded-lg overflow-hidden h-[60vh]">
                            <Editor
                                height="100%"
                                defaultLanguage="html"
                                theme="vs-dark"
                                value={editorValue}
                                onMount={(editor, monaco) => {
                                    editorRef.current = editor;
                                    monacoRef.current = monaco;
                                }}
                                onChange={(value) => {
                                    if (!customize) return;
                                    setDraft(value || '');
                                }}
                                options={{
                                    minimap: { enabled: false },
                                    fontSize: 12,
                                    scrollBeyondLastLine: false,
                                    wordWrap: 'on',
                                    readOnly: editorReadOnly,
                                    lineNumbers: 'on',
                                    folding: true,
                                    renderWhitespace: 'boundary',
                                }}
                            />
                        </div>
                        {!customize && (
                            <div className="text-xs text-muted-foreground mt-2">
                                Read-only default template. Enable Customize to edit.
                            </div>
                        )}
                    </div>

                    {/* Preview */}
                    <div className={activeTab === 'template' ? 'hidden lg:block' : ''}>
                        <div className="border border-border rounded-lg overflow-hidden h-[60vh] bg-background">
                            {previewError ? (
                                <div className="p-4 text-sm text-destructive">{previewError}</div>
                            ) : previewHtml ? (
                                <iframe
                                    title="Email Preview"
                                    sandbox=""
                                    className="w-full h-full"
                                    srcDoc={previewHtml}
                                />
                            ) : (
                                <div className="p-4 text-sm text-muted-foreground">
                                    Click <span className="font-medium">Preview</span> to render the template with test data.
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            </div>
        </Modal>
    );
};
