import React, { useState, useEffect, useRef } from 'react';
import Editor from '@monaco-editor/react';
import axios from 'axios';
import { toast } from 'sonner';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import { getCachedConfig, loadConfigYaml } from '../../utils/configCache';
import { Save, AlertCircle, Download, Upload } from 'lucide-react';

const RawYamlPage = () => {
    const { confirm } = useConfirmDialog();
    const [yamlContent, setYamlContent] = useState(() => getCachedConfig()?.content ?? '');
    const [loading, setLoading] = useState(() => getCachedConfig() == null);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [yamlError, setYamlError] = useState<{
        type?: string;
        message?: string;
        line?: number;
        column?: number;
        problem?: string;
        snippet?: string;
    } | null>(() => getCachedConfig()?.yamlError ?? null);
    const [dirty, setDirty] = useState(false);
    const [exportIncludeSecrets, setExportIncludeSecrets] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        fetchConfig();
    }, []);

    const fetchConfig = async (force = false) => {
        try {
            const r = await loadConfigYaml(force);
            setYamlContent(r.content);
            setDirty(false);
            // yamlError is non-null when the document has a parse error (content
            // still loaded for editing).
            setYamlError(r.yamlError);
            setError(null);
        } catch (err) {
            console.error('Failed to load config', err);
            setError('Failed to load configuration');
            setYamlError(null);
        } finally {
            setLoading(false);
        }
    };

    const handleSave = async () => {
        setSaving(true);
        setError(null);
        try {
            await axios.post('/api/config/yaml', { content: yamlContent });
            setDirty(false);
            toast.success('Configuration saved successfully');
            window.location.reload();
        } catch (err: any) {
            console.error(err);
            const msg = err.response?.data?.detail || 'Failed to save configuration';
            setError(msg);
            toast.error(msg);
        } finally {
            setSaving(false);
        }
    };

    const handleImport = async (event: React.ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0];
        if (!file) return;

        const confirmed = await confirm({
            title: 'Import Configuration?',
            description: 'This will overwrite your current configuration. A backup will be created.',
            confirmText: 'Import',
            variant: 'destructive'
        });
        if (!confirmed) {
            if (fileInputRef.current) fileInputRef.current.value = '';
            return;
        }

        const formData = new FormData();
        formData.append('file', file);

        setSaving(true);
        try {
            await axios.post('/api/config/import', formData, {
                headers: { 'Content-Type': 'multipart/form-data' }
            });
            toast.success('Configuration imported successfully. The page will reload.');
            window.location.reload();
        } catch (err: any) {
            console.error(err);
            toast.error('Failed to import configuration', { description: err.response?.data?.detail || err.message });
        } finally {
            setSaving(false);
            if (fileInputRef.current) fileInputRef.current.value = '';
        }
    };

    useEffect(() => {
        const handler = (e: BeforeUnloadEvent) => {
            if (dirty) {
                e.preventDefault();
                e.returnValue = '';
            }
        };
        window.addEventListener('beforeunload', handler);
        return () => window.removeEventListener('beforeunload', handler);
    }, [dirty]);

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading configuration...</div>;

    return (
        <div className="h-[calc(100vh-140px)] flex flex-col space-y-4">
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Raw Configuration</h1>
                    <p className="text-muted-foreground mt-1">
                        Directly edit the AI agent configuration. Changes are saved to `ai-agent.local.yaml` (your operator overrides).
                    </p>
                </div>
                <div className="flex gap-2">
                    <input
                        type="file"
                        ref={fileInputRef}
                        onChange={handleImport}
                        accept=".zip"
                        className="hidden"
                    />
                    <button
                        onClick={() => fileInputRef.current?.click()}
                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2"
                    >
                        <Upload className="w-4 h-4 mr-2" />
                        Import
                    </button>
                    <label className="inline-flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer select-none h-9">
                        <input
                            type="checkbox"
                            checked={exportIncludeSecrets}
                            onChange={e => setExportIncludeSecrets(e.target.checked)}
                            className="accent-primary"
                        />
                        Include secrets (.env)
                        {exportIncludeSecrets && (
                            <span className="text-amber-600 dark:text-amber-400 font-medium ml-1">— export will contain API keys</span>
                        )}
                    </label>
                    <button
                        onClick={async () => {
                            try {
                                const exportUrl = exportIncludeSecrets ? '/api/config/export?include_secrets=true' : '/api/config/export';
                                const response = await axios.get(exportUrl, { responseType: 'blob' });
                                const blobUrl = window.URL.createObjectURL(new Blob([response.data]));
                                const link = document.createElement('a');
                                link.href = blobUrl;
                                const date = new Date().toISOString().slice(0, 19).replace(/:/g, '-');
                                link.setAttribute('download', `config-backup-${date}.zip`);
                                document.body.appendChild(link);
                                link.click();
                                link.remove();
                                toast.success('Configuration exported successfully');
                            } catch (err: any) {
                                console.error(err);
                                toast.error('Failed to export configuration');
                            }
                        }}
                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2"
                    >
                        <Download className="w-4 h-4 mr-2" />
                        Export
                    </button>
                    <button
                        onClick={handleSave}
                        disabled={saving}
                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                    >
                        <Save className="w-4 h-4 mr-2" />
                        {saving ? 'Saving...' : 'Save Changes'}
                    </button>
                </div>
            </div>

            {error && (
                <div className="p-4 bg-destructive/10 text-destructive rounded-md border border-destructive/20 flex items-center gap-2">
                    <AlertCircle className="w-4 h-4" />
                    {error}
                </div>
            )}

            {yamlError && (
                <div className="p-4 bg-orange-500/15 border border-orange-500/30 text-orange-700 dark:text-orange-400 rounded-md space-y-2">
                    <div className="flex items-center font-semibold">
                        <AlertCircle className="w-4 h-4 mr-2" />
                        YAML Syntax Error - Fix below and save
                    </div>
                    {yamlError.line && (
                        <div className="text-sm">
                            <span className="font-medium">Location:</span> Line {yamlError.line}
                            {yamlError.column && <>, Column {yamlError.column}</>}
                        </div>
                    )}
                    {yamlError.problem && (
                        <div className="text-sm">
                            <span className="font-medium">Problem:</span> {yamlError.problem}
                        </div>
                    )}
                </div>
            )}

            <div className="flex-1 border border-border rounded-lg overflow-hidden shadow-sm">
                <Editor
                    height="100%"
                    defaultLanguage="yaml"
                    theme="vs-dark"
                    value={yamlContent}
                    onChange={(value) => {
                        setYamlContent(value || '');
                        setDirty(true);
                    }}
                    options={{
                        minimap: { enabled: false },
                        fontSize: 14,
                        scrollBeyondLastLine: false,
                        wordWrap: 'on',
                    }}
                />
            </div>
        </div>
    );
};

export default RawYamlPage;
