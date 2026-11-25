import React, { useState, useEffect, useRef } from 'react';
import Editor from '@monaco-editor/react';
import axios from 'axios';
import { toast } from 'sonner';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import { loadConfigYaml } from '../../utils/configCache';
import { Modal } from '../../components/ui/Modal';
import { Save, AlertCircle, Download, Upload } from 'lucide-react';

function yamlLocation(detail: unknown): string {
    const match = typeof detail === 'string' && detail.match(/\bat line (\d+), column (\d+)\b/i);
    return match ? ` at line ${match[1]}, column ${match[2]}` : '';
}

type OverrideRepair = {revision: string, content: string, partial: boolean};

const RawYamlPage = () => {
    const { confirm } = useConfirmDialog();
    const [yamlContent, setYamlContent] = useState('');
    const [loadedContent, setLoadedContent] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [repair, setRepair] = useState<OverrideRepair | null>(null);
    const [repairing, setRepairing] = useState(false);
    const [conflict, setConflict] = useState(false);
    const dirty = loadedContent !== null && yamlContent !== loadedContent;
    const canSave = (dirty || repairing) && !conflict && !loading && !saving && !!yamlContent.trim();
    const [exportIncludeSecrets, setExportIncludeSecrets] = useState(false);
    const [exportOpen, setExportOpen] = useState(false);
    const [exportPassword, setExportPassword] = useState('');
    const [exportError, setExportError] = useState('');
    const [exporting, setExporting] = useState(false);
    const closeExport = () => {
        setExportOpen(false);
        setExportPassword('');
        setExportError('');
        setExportIncludeSecrets(false);
    };

    const downloadBackup = async (event: React.FormEvent) => {
        event.preventDefault();
        if (exporting || !exportPassword) return;
        setExporting(true);
        setExportError('');
        try {
            const response = await axios.post('/api/config/export', {password: exportPassword}, {
                params: {include_secrets: exportIncludeSecrets}, responseType: 'blob',
            });
            const url = URL.createObjectURL(response.data);
            const link = document.createElement('a');
            link.href = url;
            link.download = `config-backup-${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.zip`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 0);
            closeExport();
            toast.success('Configuration exported successfully');
        } catch (error) {
            const status = (error as {response?: {status: number}}).response?.status;
            setExportError(status === 401 ? 'Incorrect current password. Try again.'
                : status === 429 ? 'Too many failed attempts. Try again after the lockout expires.'
                : 'The backup could not be downloaded. Try again.');
            setExportPassword('');
        } finally {
            setExporting(false);
        }
    };

    const fileInputRef = useRef<HTMLInputElement>(null);

    const fetchConfig = async () => {
        setLoading(true);
        setLoadedContent(null);
        setRepair(null);
        setRepairing(false);
        setConflict(false);
        setError(null);
        const guidance = 'Repair the configuration file on the server, then reload.';
        try {
            const result = await loadConfigYaml(true);
            if (result.yamlError) {
                const { line, column } = result.yamlError;
                const location = Number.isInteger(line) && Number.isInteger(column) ? ` at line ${line}, column ${column}` : '';
                setError(`Invalid YAML${location}. ${guidance}`);
                return;
            }
            setYamlContent(result.content);
            setLoadedContent(result.content);
        } catch (err) {
            const response = (err as {response?: {status?: number; data?: {detail?: unknown}}})?.response;
            const detail = response?.data?.detail;
            if (response?.status === 400 && detail && typeof detail === 'object' && 'repair' in detail) {
                const offer = detail.repair as Partial<OverrideRepair> | null;
                if (offer && typeof offer.revision === 'string' && typeof offer.content === 'string' && typeof offer.partial === 'boolean') {
                    setRepair({revision: offer.revision, content: offer.content, partial: offer.partial});
                    setError(`Invalid YAML${yamlLocation('message' in detail ? detail.message : null)} in ai-agent.local.yaml.`);
                    return;
                }
            }
            setError(response?.status === 400
                ? `Invalid YAML${yamlLocation(detail)}. ${typeof detail === 'string' && detail.includes('shipped config/ai-agent.yaml')
                    ? 'Repair config/ai-agent.yaml on the server, then reload.' : guidance}`
                : 'Failed to load configuration. Reload to try again.');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { void fetchConfig(); }, []);

    const handleSave = async () => {
        if (!canSave) return;
        if (repairing && !await confirm({
            title: 'Replace the damaged override?',
            description: 'This saves the displayed settings as ai-agent.local.yaml. Omitted overrides revert to the base configuration. A copy of the damaged file is kept on the server.',
            confirmText: 'Replace override',
            variant: 'destructive',
        })) return;
        setSaving(true);
        setError(null);
        try {
            await axios.post('/api/config/yaml', repairing && repair
                ? {content: yamlContent, repair_revision: repair.revision} : {content: yamlContent});
            setLoadedContent(yamlContent);
            toast.success('Configuration saved successfully');
            window.location.reload();
        } catch (err) {
            const response = (err as {response?: {status?: number, data?: {detail?: unknown}}})?.response;
            if (repairing && response?.status === 409) setConflict(true);
            const detail = response?.data?.detail;
            const msg = typeof detail === 'string' && detail ? detail : 'Failed to save configuration';
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
        } catch {
            toast.error('Failed to import configuration. Check the backup ZIP and try again.');
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

    return (
        <div className="h-[calc(100vh-140px)] flex flex-col space-y-4">
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Raw Configuration</h1>
                    <p className="text-muted-foreground mt-1">
                        Directly edit the AI agent configuration. Changes are saved to <code>ai-agent.local.yaml</code> (your operator overrides).
                        Keep a stored URL intact or replace the entire URL. Prompts, greetings and descriptions are visible in full.
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
                        disabled={saving}
                        onClick={() => fileInputRef.current?.click()}
                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2"
                    >
                        <Upload className="w-4 h-4 mr-2" />
                        Import
                    </button>
                    <button
                        onClick={() => setExportOpen(true)}
                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2"
                    >
                        <Download className="w-4 h-4 mr-2" />
                        Export
                    </button>
                    <button
                        onClick={handleSave}
                        disabled={!canSave}
                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                    >
                        <Save className="w-4 h-4 mr-2" />
                        {saving ? 'Saving...' : 'Save Changes'}
                    </button>
                </div>
            </div>

            <Modal isOpen={exportOpen} onClose={closeExport} title="Export configuration" size="sm" busy={exporting}>
                <form onSubmit={downloadBackup} className="space-y-4">
                    <label className="block text-sm font-medium">Current password
                        <input type="password" autoComplete="current-password" required
                            value={exportPassword} onChange={e => setExportPassword(e.target.value)}
                            className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2" />
                    </label>
                    <label className="flex items-center gap-2 text-sm">
                        <input type="checkbox" checked={exportIncludeSecrets}
                            onChange={e => setExportIncludeSecrets(e.target.checked)} />
                        Include credentials from .env
                    </label>
                    <p className="text-xs text-muted-foreground">The ZIP can contain credentials stored in configuration files. Keep it private.</p>
                    {exportError && <p role="alert" className="text-sm text-destructive">{exportError}</p>}
                    <button disabled={exporting || !exportPassword} className="rounded-md bg-primary px-4 py-2 text-primary-foreground disabled:opacity-50">
                        {exporting ? 'Preparing backup…' : 'Download backup'}
                    </button>
                </form>
            </Modal>

            {error && (
                <div role="alert" className="p-4 bg-destructive/10 text-destructive rounded-md border border-destructive/20 flex items-center gap-2">
                    <AlertCircle className="w-4 h-4" />
                    {error}
                </div>
            )}

            {loading && <p className="p-8 text-center text-muted-foreground">Loading configuration...</p>}
            {repair && !repairing && <button className="self-start rounded-md border border-input px-4 py-2 text-sm"
                onClick={() => {
                    setLoadedContent(repair.content);
                    setYamlContent(repair.content);
                    setRepairing(true);
                    setError(null);
                }}>Repair override</button>}
            {repairing && <p className="text-sm text-muted-foreground">
                {repair?.partial
                    ? 'The editor recovered independent settings and omitted the malformed entry. Complete the override before saving.'
                    : 'The damaged file could not be safely displayed. Enter a complete local override, or enter {} to use the base configuration.'}
            </p>}
            {!loading && (loadedContent === null || conflict) && (
                <button className="self-start rounded-md border border-input px-4 py-2 text-sm" onClick={async () => {
                    if (dirty && !await confirm({title: 'Discard this repair draft?', description: 'Reload reads the current configuration from disk.', confirmText: 'Reload', variant: 'destructive'})) return;
                    await fetchConfig();
                }}>
                    Reload configuration
                </button>
            )}

            {loadedContent !== null && <div className="flex-1 border border-border rounded-lg overflow-hidden shadow-sm">
                <Editor
                    height="100%"
                    defaultLanguage="yaml"
                    theme="vs-dark"
                    value={yamlContent}
                    onChange={(value) => setYamlContent(value ?? '')}
                    options={{
                        readOnly: saving,
                        minimap: { enabled: false },
                        fontSize: 14,
                        scrollBeyondLastLine: false,
                        wordWrap: 'on',
                    }}
                />
            </div>}
        </div>
    );
};

export default RawYamlPage;
