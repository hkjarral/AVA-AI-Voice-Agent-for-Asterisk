import React, { useEffect, useState, useCallback, useRef } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { Upload, Trash2, CheckCircle, Loader2, KeyRound, ExternalLink } from 'lucide-react';
import { useConfirmDialog } from '../../../hooks/useConfirmDialog';
import { Link } from 'react-router-dom';

/**
 * Shared credential management card for full-agent providers.
 *
 * Wraps the existing admin backend endpoints:
 *   GET    /api/config/providers/{key}/credentials
 *   POST   /api/config/providers/{key}/credentials/api-key   (paste-upload)
 *   POST   /api/config/providers/{key}/credentials/agent-id  (paste-upload)
 *   DELETE /api/config/providers/{key}/credentials/{name}
 *
 * Behavior summary:
 * - If `providerKey` is empty (new provider not yet saved): shows a hint to save first.
 * - If the YAML inline value is a `${ENV_VAR}` reference: shows env-var fallback
 *   pointing at the Environment page (no file upload — legacy single-instance flow).
 * - Otherwise: shows file status + upload / re-upload / delete actions.
 */

interface CredentialStatus {
    uploaded: boolean;
    path: string;
    uploaded_at?: number;
    configured?: boolean;
    error?: string;
}

interface ProviderCredentialsCardProps {
    /** YAML key for the provider (e.g. "grok", "acme_grok"). Empty for unsaved providers. */
    providerKey?: string;
    /** What credential to manage. */
    credentialType: 'api-key' | 'agent-id';
    /** Display label (e.g. "xAI API Key"). */
    label: string;
    /** Input placeholder (e.g. "xai-..."). */
    placeholder?: string;
    /**
     * Legacy env-var fallback name (e.g. "XAI_API_KEY"). When the YAML inline value
     * (`inlineValue`) is a "${ENV}" reference, the card surfaces this name and links
     * to the Environment page instead of offering per-instance upload.
     */
    envVarFallback?: string;
    /** Current YAML inline value for this credential (e.g. `api_key: "${XAI_API_KEY}"`). */
    inlineValue?: string;
    /**
     * Help text shown below the upload form. Use for guidance like
     * "Find your key in the xAI console at console.x.ai".
     */
    helpText?: React.ReactNode;
    /**
     * Called after a successful upload or delete with a patch describing the
     * field change. The parent form should merge this into its `providerForm`
     * state so that any subsequent Save sends the new value back. Without this
     * callback, the form's local state would be stale (still missing
     * `api_key_file`/`agent_id_file`) and the next Save would write a stale
     * provider entry, wiping the credential reference from the YAML.
     *
     * Patch shape: `{ api_key_file: "/path/to/file" }` after upload, or
     *              `{ api_key_file: undefined }` after delete.
     */
    onConfigPatch?: (patch: Record<string, any>) => void;
}

/** Map credential-type → YAML field name the backend writes. Kept in sync with
 *  `CREDENTIAL_NAME_TO_FIELD` in src/config/provider_instances.py. */
const CREDENTIAL_FIELD: Record<string, string> = {
    'api-key': 'api_key_file',
    'agent-id': 'agent_id_file',
};

/**
 * Normalize an axios error's `detail` field for display.
 *
 * The backend's DELETE endpoint returns a structured payload when a credential
 * file is referenced by multiple providers (`{ message, references }`). Naively
 * passing that object to toast.error renders as `[object Object]`. This helper
 * extracts `.message` when the detail is an object.
 */
const formatErrorDetail = (err: any, fallback: string): string => {
    const detail = err?.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object' && typeof detail.message === 'string') {
        return detail.message;
    }
    return fallback;
};

const ProviderCredentialsCard: React.FC<ProviderCredentialsCardProps> = ({
    providerKey,
    credentialType,
    label,
    placeholder,
    envVarFallback,
    inlineValue,
    helpText,
    onConfigPatch,
}) => {
    const { confirm } = useConfirmDialog();
    const [status, setStatus] = useState<CredentialStatus | null>(null);
    const [loading, setLoading] = useState(false);
    const [showUploadField, setShowUploadField] = useState(false);
    const [secretInput, setSecretInput] = useState('');
    const [submitting, setSubmitting] = useState(false);

    // Hold a ref to the latest `onConfigPatch` so async upload/delete handlers
    // dispatch through the parent's most-recent callback (not the one captured
    // when the request started). Pairs with each form's configRef pattern so
    // patches build off the latest in-memory provider config — even if the
    // user edited other fields while the request was in flight.
    const onConfigPatchRef = useRef(onConfigPatch);
    useEffect(() => {
        onConfigPatchRef.current = onConfigPatch;
    }, [onConfigPatch]);

    const fetchStatus = useCallback(async () => {
        if (!providerKey) return;
        setLoading(true);
        try {
            const res = await axios.get(`/api/config/providers/${encodeURIComponent(providerKey)}/credentials`);
            const cred = res.data?.credentials?.[credentialType];
            setStatus(cred || { uploaded: false, path: '' });
        } catch (e: any) {
            // 404 just means the provider hasn't been saved or the kind doesn't accept this credential.
            setStatus({ uploaded: false, path: '' });
        } finally {
            setLoading(false);
        }
    }, [providerKey, credentialType]);

    useEffect(() => {
        fetchStatus();
    }, [fetchStatus]);

    const handleUpload = async () => {
        const value = secretInput.trim();
        if (!value) {
            toast.error(`${label} cannot be empty.`);
            return;
        }
        setSubmitting(true);
        try {
            const fieldName = credentialType === 'api-key' ? 'api_key' : 'agent_id';
            const res = await axios.post(
                `/api/config/providers/${encodeURIComponent(providerKey || '')}/credentials/${credentialType}`,
                { [fieldName]: value },
            );
            toast.success(`${label} saved.`);
            setSecretInput('');
            setShowUploadField(false);
            await fetchStatus();
            // Notify the parent form so its in-memory state reflects the new
            // field. Without this, a subsequent form Save would overwrite the
            // YAML with stale data and strip the just-written reference. Read
            // through the ref so we always invoke the latest callback.
            const yamlField = CREDENTIAL_FIELD[credentialType];
            const writtenPath = (res.data && (res.data as any).path) || undefined;
            const patchCb = onConfigPatchRef.current;
            if (yamlField && patchCb) {
                patchCb({ [yamlField]: writtenPath });
            }
        } catch (e: any) {
            toast.error(formatErrorDetail(e, `Failed to save ${label}.`));
        } finally {
            setSubmitting(false);
        }
    };

    const handleDelete = async () => {
        const confirmed = await confirm({
            title: `Delete ${label}?`,
            description: `This removes the credential file for "${providerKey}" and reverts the YAML to use whatever fallback (env var or inline) you have configured. The provider will fail to load until a credential is provided.`,
            confirmText: 'Delete',
            variant: 'destructive',
        });
        if (!confirmed) return;
        setSubmitting(true);
        try {
            await axios.delete(
                `/api/config/providers/${encodeURIComponent(providerKey || '')}/credentials/${credentialType}`,
            );
            toast.success(`${label} deleted.`);
            await fetchStatus();
            const yamlField = CREDENTIAL_FIELD[credentialType];
            const patchCb = onConfigPatchRef.current;
            if (yamlField && patchCb) {
                patchCb({ [yamlField]: undefined });
            }
        } catch (e: any) {
            // Backend can return a structured detail like
            //   { message: "Credential file is referenced by other providers", references: [...] }
            // when the file is shared. Render the human-readable message.
            toast.error(formatErrorDetail(e, `Failed to delete ${label}.`));
        } finally {
            setSubmitting(false);
        }
    };

    // Unsaved provider — uploading would 404, so guide the user instead.
    if (!providerKey) {
        return (
            <div className="border border-dashed border-input rounded-lg p-4 bg-muted/30">
                <div className="flex items-start gap-3">
                    <KeyRound className="w-5 h-5 text-muted-foreground flex-shrink-0 mt-0.5" />
                    <div>
                        <p className="font-medium text-sm">{label}</p>
                        <p className="text-xs text-muted-foreground mt-1">
                            Save the provider first, then upload credentials here.
                        </p>
                    </div>
                </div>
            </div>
        );
    }

    // Env-var fallback (legacy single-instance form, e.g. api_key: "${XAI_API_KEY}").
    const isEnvVarRef = typeof inlineValue === 'string' && inlineValue.trim().startsWith('${');
    const envVarFromInline = isEnvVarRef
        ? inlineValue!.trim().replace(/^\$\{/, '').replace(/\}$/, '').split(':-')[0]
        : envVarFallback;

    if (isEnvVarRef && !status?.uploaded) {
        return (
            <div className="border border-input rounded-lg p-4 bg-muted/30">
                <div className="flex items-start gap-3">
                    <KeyRound className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
                    <div className="flex-1">
                        <p className="font-medium text-sm">{label}</p>
                        <p className="text-xs text-muted-foreground mt-1">
                            Using environment variable{' '}
                            <code className="px-1 py-0.5 bg-background rounded text-foreground">
                                {envVarFromInline}
                            </code>
                            {' '}— legacy single-instance form.
                        </p>
                        <div className="mt-3 flex flex-wrap gap-2">
                            <Link
                                to="/env"
                                className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                            >
                                Set in Environment page <ExternalLink className="w-3 h-3" />
                            </Link>
                            <span className="text-xs text-muted-foreground">·</span>
                            <button
                                type="button"
                                className="text-xs text-primary hover:underline"
                                onClick={() => setShowUploadField(true)}
                            >
                                Or upload a per-instance key file
                            </button>
                        </div>
                        {showUploadField && (
                            <UploadField
                                label={label}
                                placeholder={placeholder}
                                value={secretInput}
                                onChange={setSecretInput}
                                onSubmit={handleUpload}
                                onCancel={() => {
                                    setShowUploadField(false);
                                    setSecretInput('');
                                }}
                                submitting={submitting}
                                helpText={helpText}
                            />
                        )}
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="border border-input rounded-lg p-4">
            <div className="flex items-start gap-3">
                <KeyRound className={`w-5 h-5 flex-shrink-0 mt-0.5 ${status?.uploaded ? 'text-green-600' : 'text-muted-foreground'}`} />
                <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-2 flex-wrap">
                        <p className="font-medium text-sm">{label}</p>
                        {loading && <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />}
                    </div>

                    {status?.uploaded ? (
                        <div className="mt-2">
                            <div className="flex items-center gap-2 text-xs text-green-700 dark:text-green-400">
                                <CheckCircle className="w-3.5 h-3.5" />
                                <span>Credential file uploaded</span>
                            </div>
                            <p className="text-xs text-muted-foreground mt-1 font-mono truncate" title={status.path}>
                                {status.path}
                            </p>
                            {status.uploaded_at && (
                                <p className="text-xs text-muted-foreground mt-0.5">
                                    Last updated {new Date(status.uploaded_at * 1000).toLocaleString()}
                                </p>
                            )}
                            <div className="mt-3 flex flex-wrap gap-2">
                                <button
                                    type="button"
                                    className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border border-input hover:bg-secondary"
                                    onClick={() => setShowUploadField(true)}
                                    disabled={submitting}
                                >
                                    <Upload className="w-3 h-3" /> Replace
                                </button>
                                <button
                                    type="button"
                                    className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border border-input text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30"
                                    onClick={handleDelete}
                                    disabled={submitting}
                                >
                                    <Trash2 className="w-3 h-3" /> Delete
                                </button>
                            </div>
                        </div>
                    ) : (
                        <div className="mt-2">
                            <p className="text-xs text-muted-foreground">
                                No credential configured for this provider instance.
                            </p>
                            {!showUploadField && (
                                <button
                                    type="button"
                                    className="mt-2 inline-flex items-center gap-1 text-xs px-2 py-1 rounded border border-input hover:bg-secondary"
                                    onClick={() => setShowUploadField(true)}
                                >
                                    <Upload className="w-3 h-3" /> Upload {label}
                                </button>
                            )}
                        </div>
                    )}

                    {showUploadField && (
                        <UploadField
                            label={label}
                            placeholder={placeholder}
                            value={secretInput}
                            onChange={setSecretInput}
                            onSubmit={handleUpload}
                            onCancel={() => {
                                setShowUploadField(false);
                                setSecretInput('');
                            }}
                            submitting={submitting}
                            helpText={helpText}
                        />
                    )}
                </div>
            </div>
        </div>
    );
};

interface UploadFieldProps {
    label: string;
    placeholder?: string;
    value: string;
    onChange: (v: string) => void;
    onSubmit: () => void;
    onCancel: () => void;
    submitting: boolean;
    helpText?: React.ReactNode;
}

const UploadField: React.FC<UploadFieldProps> = ({
    label,
    placeholder,
    value,
    onChange,
    onSubmit,
    onCancel,
    submitting,
    helpText,
}) => (
    <div className="mt-3 border-t border-input pt-3 space-y-2">
        <label className="text-xs font-medium text-muted-foreground">
            Paste {label}
        </label>
        <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            className="w-full p-2 rounded border border-input bg-background font-mono text-sm"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder}
            onKeyDown={(e) => {
                if (e.key === 'Enter' && !submitting) {
                    e.preventDefault();
                    onSubmit();
                }
            }}
        />
        {helpText && <div className="text-xs text-muted-foreground">{helpText}</div>}
        <div className="flex gap-2">
            <button
                type="button"
                className="px-3 py-1.5 rounded bg-primary text-primary-foreground text-xs hover:bg-primary/90 disabled:opacity-50 inline-flex items-center gap-1"
                onClick={onSubmit}
                disabled={submitting || !value.trim()}
            >
                {submitting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Upload className="w-3 h-3" />}
                Save
            </button>
            <button
                type="button"
                className="px-3 py-1.5 rounded border border-input text-xs hover:bg-secondary"
                onClick={onCancel}
                disabled={submitting}
            >
                Cancel
            </button>
        </div>
    </div>
);

export default ProviderCredentialsCard;

/**
 * Helper for provider forms: forward a credential patch to the parent's
 * `onChange`. The patch contains only the changed keys — never a snapshot of
 * the surrounding config — so the parent's functional `updateForm` can merge
 * it into the latest in-memory state without risk of clobbering concurrent
 * field edits.
 *
 * A patch value of `undefined` means "delete this key from the YAML". The
 * parent's `updateForm` must honor that contract (see ProvidersPage /
 * ConfigEditor) — a plain `{ ...prev, api_key_file: undefined }` spread would
 * leave the key in place (just as `undefined`), which `yaml.dump` then writes
 * as `field: null`, OR a subsequent shallow spread re-preserves the previous
 * non-undefined value. The functional updater explicitly deletes such keys.
 *
 * This helper is intentionally tiny — it exists for naming clarity at the
 * call site, and to colocate the credential-patch contract documentation
 * with the card that emits it.
 */
export const applyCredentialPatch = (
    patch: Record<string, any>,
    onChange: (next: Record<string, any>) => void,
) => {
    onChange(patch);
};
