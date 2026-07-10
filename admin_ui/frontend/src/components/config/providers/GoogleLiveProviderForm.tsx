import React, { useEffect, useState, useRef, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useConfirmDialog } from '../../../hooks/useConfirmDialog';
import { AlertTriangle, Upload, Trash2, CheckCircle, XCircle, Loader2, FileJson } from 'lucide-react';
import HelpTooltip from '../../ui/HelpTooltip';
import ProviderCredentialsCard, { applyCredentialPatch } from './ProviderCredentialsCard';
import {
    GOOGLE_LIVE_MODEL_GROUPS,
    GOOGLE_LIVE_SUPPORTED_MODELS,
    normalizeGoogleLiveModelForUi,
} from '../../../utils/googleLiveModels';

const GOOGLE_LIVE_VOICE_OPTIONS = [
    { value: 'Achernar', tone: 'Soft' },
    { value: 'Achird', tone: 'Friendly' },
    { value: 'Algenib', tone: 'Gravelly' },
    { value: 'Algieba', tone: 'Smooth' },
    { value: 'Alnilam', tone: 'Firm' },
    { value: 'Aoede', tone: 'Breezy' },
    { value: 'Autonoe', tone: 'Bright' },
    { value: 'Callirrhoe', tone: 'Easy-going' },
    { value: 'Charon', tone: 'Informative' },
    { value: 'Despina', tone: 'Smooth' },
    { value: 'Enceladus', tone: 'Breathy' },
    { value: 'Erinome', tone: 'Clear' },
    { value: 'Fenrir', tone: 'Excitable' },
    { value: 'Gacrux', tone: 'Mature' },
    { value: 'Iapetus', tone: 'Clear' },
    { value: 'Kore', tone: 'Firm' },
    { value: 'Laomedeia', tone: 'Upbeat' },
    { value: 'Leda', tone: 'Youthful' },
    { value: 'Orus', tone: 'Firm' },
    { value: 'Puck', tone: 'Upbeat' },
    { value: 'Pulcherrima', tone: 'Forward' },
    { value: 'Rasalgethi', tone: 'Informative' },
    { value: 'Sadachbia', tone: 'Lively' },
    { value: 'Sadaltager', tone: 'Knowledgeable' },
    { value: 'Schedar', tone: 'Even' },
    { value: 'Sulafat', tone: 'Warm' },
    { value: 'Umbriel', tone: 'Easy-going' },
    { value: 'Vindemiatrix', tone: 'Gentle' },
    { value: 'Zephyr', tone: 'Bright' },
    { value: 'Zubenelgenubi', tone: 'Casual' },
] as const;

const GOOGLE_LIVE_SUPPORTED_VOICE_NAMES = GOOGLE_LIVE_VOICE_OPTIONS.map((v) => v.value);

interface VertexRegion {
    value: string;
    label: string;
}

interface CredentialsStatus {
    uploaded: boolean;
    filename: string | null;
    project_id: string | null;
    client_email: string | null;
    uploaded_at: number | null;
    error?: string;
}

interface GoogleLiveProviderFormProps {
    config: any;
    onChange: (newConfig: any) => void;
    providerKey?: string;
}

const GoogleLiveProviderForm: React.FC<GoogleLiveProviderFormProps> = ({ config, onChange, providerKey }) => {
    const { confirm } = useConfirmDialog();
    const handleChange = (field: string, value: any) => {
        onChange({ ...config, [field]: value });
    };

    const expertStorageKey = `providers.google_live.expert.keepalive.v1`;
    const [expertEnabled, setExpertEnabled] = useState<boolean>(() => {
        try {
            return window.localStorage.getItem(expertStorageKey) === 'true';
        } catch {
            return false;
        }
    });

    // Vertex AI state
    const [regions, setRegions] = useState<VertexRegion[]>([]);
    const [credentials, setCredentials] = useState<CredentialsStatus | null>(null);
    const [uploading, setUploading] = useState(false);
    const [verifying, setVerifying] = useState(false);
    const [verifyResult, setVerifyResult] = useState<{ status: 'success' | 'error'; message: string } | null>(null);
    const [uploadError, setUploadError] = useState<string | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const providerCredentialsBase = providerKey
        ? `/api/config/providers/${encodeURIComponent(providerKey)}/credentials`
        : '/api/config/vertex-ai';

    // Fetch regions and credentials status
    const fetchVertexData = useCallback(async () => {
        try {
            const [regionsRes, credsRes] = await Promise.all([
                axios.get('/api/config/vertex-ai/regions'),
                axios.get(providerKey ? providerCredentialsBase : `${providerCredentialsBase}/credentials`),
            ]);
            if (regionsRes.data) {
                setRegions(regionsRes.data.regions || []);
            }
            if (credsRes.data) {
                setCredentials(providerKey ? (credsRes.data.credentials?.['vertex-json'] || { uploaded: false }) : credsRes.data);
            }
        } catch (e) {
            console.error('Failed to fetch Vertex AI data:', e);
        }
    }, [providerKey, providerCredentialsBase]);

    useEffect(() => {
        fetchVertexData();
    }, [fetchVertexData]);

    useEffect(() => {
        try {
            window.localStorage.setItem(expertStorageKey, expertEnabled ? 'true' : 'false');
        } catch {
            // ignore
        }
    }, [expertEnabled]);

    // Auto-switch model when API mode changes so Vertex ↔ Developer models stay in sync.
    // This useEffect is the authoritative guard — it fires whenever use_vertex_ai flips
    // and corrects the model if it belongs to the wrong API group.
    const prevVertexRef = useRef<boolean | undefined>(undefined);
    useEffect(() => {
        const useVertex = config.use_vertex_ai ?? false;
        // Only fire on actual toggles, not on initial mount
        if (prevVertexRef.current !== undefined && prevVertexRef.current !== useVertex) {
            const currentModel = config.llm_model || '';
            const isModelVertex = currentModel.startsWith('gemini-live-');
            const mismatch = useVertex ? !isModelVertex : isModelVertex;
            if (mismatch) {
                const newModel = useVertex
                    ? 'gemini-live-2.5-flash-native-audio'
                    : 'gemini-2.5-flash-native-audio-latest';
                onChange({ ...config, llm_model: newModel });
            }
        }
        prevVertexRef.current = useVertex;
    }, [config.use_vertex_ai]); // eslint-disable-line react-hooks/exhaustive-deps

    const selectedModel = normalizeGoogleLiveModelForUi(config.llm_model);

    // File upload handler
    const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;

        setUploading(true);
        setUploadError(null);
        setVerifyResult(null);

        const formData = new FormData();
        formData.append('file', file);

        try {
            const res = await axios.post(providerKey ? `${providerCredentialsBase}/vertex-json` : `${providerCredentialsBase}/credentials`, formData, {
                headers: { 'Content-Type': 'multipart/form-data' },
            });
            await fetchVertexData();
            // Auto-fill project ID if empty
            if (res.data.project_id && !config.vertex_project) {
                handleChange('vertex_project', res.data.project_id);
            }
        } catch (e: any) {
            setUploadError(e.response?.data?.detail || 'Upload failed');
        } finally {
            setUploading(false);
            if (fileInputRef.current) fileInputRef.current.value = '';
        }
    };

    // Delete credentials
    const handleDeleteCredentials = async () => {
        const confirmed = await confirm({
            title: 'Delete Service Account JSON',
            description: 'Delete the uploaded service account JSON? This cannot be undone.',
            confirmText: 'Delete',
            variant: 'destructive',
        });
        if (!confirmed) return;

        try {
            await axios.delete(providerKey ? `${providerCredentialsBase}/vertex-json` : `${providerCredentialsBase}/credentials`);
            setCredentials({ uploaded: false, filename: null, project_id: null, client_email: null, uploaded_at: null });
            setVerifyResult(null);
            toast.success('Service account credentials deleted');
        } catch (e: any) {
            toast.error(e.response?.data?.detail || 'Failed to delete credentials');
        }
    };

    // Verify credentials
    const handleVerifyCredentials = async () => {
        setVerifying(true);
        setVerifyResult(null);

        try {
            const res = await axios.post(providerKey ? `${providerCredentialsBase}/verify` : `${providerCredentialsBase}/verify`);
            setVerifyResult({ status: 'success', message: res.data.message || 'Credentials verified!' });
            // Auto-switch to a Vertex-compatible model on successful verification
            const currentModel = config.llm_model || '';
            if (!currentModel.startsWith('gemini-live-')) {
                onChange({ ...config, llm_model: 'gemini-live-2.5-flash-native-audio' });
            }
        } catch (e: any) {
            setVerifyResult({ status: 'error', message: e.response?.data?.detail || 'Verification failed' });
        } finally {
            setVerifying(false);
        }
    };

    return (
        <div className="space-y-6">
            {/* API Mode Section - Top of form like OpenAI Realtime */}
            <div>
                <h4 className="font-semibold mb-3">API Mode</h4>
                <div className="space-y-4">
                    <div className="flex items-start gap-3 p-3 rounded-md border border-input bg-muted/30">
                        <input
                            type="checkbox"
                            id="use_vertex_ai"
                            className="mt-1 rounded border-input"
                            checked={config.use_vertex_ai ?? false}
                            onChange={(e) => {
                                handleChange('use_vertex_ai', e.target.checked);
                                // Model auto-switch is handled by the useEffect above
                            }}
                        />
                        <div>
                            <div className="flex items-center gap-1.5">
                                <label htmlFor="use_vertex_ai" className="text-sm font-medium cursor-pointer">
                                    Use Vertex AI (Enterprise / GCP)
                                </label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>API Mode toggle</strong> — choose between Google's two Live API surfaces.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><strong>Off (Developer API):</strong> <code>generativelanguage.googleapis.com</code> with a simple <code>GOOGLE_API_KEY</code>. Fastest setup; preview models.</li>
                                                <li><strong>On (Vertex AI):</strong> <code>aiplatform.googleapis.com</code> with OAuth2/ADC via service-account JSON. GA models, enterprise quotas, fixed function-calling reliability.</li>
                                            </ul>
                                            Toggling auto-switches the model to the matching API group.
                                        </>
                                    }
                                    link="https://ai.google.dev/gemini-api/docs/live"
                                    linkText="Gemini Live docs"
                                />
                            </div>
                            <p className="text-xs text-muted-foreground mt-0.5">
                                Connects to <code>aiplatform.googleapis.com</code> using OAuth2/ADC instead of an API key.
                                Enables GA models with fixed function calling reliability.
                            </p>
                        </div>
                    </div>

                    {/* Vertex AI project + location — shown when Vertex AI is ON */}
                    {config.use_vertex_ai && (
                        <div className="space-y-4 p-3 rounded-md border border-blue-200 dark:border-blue-800 bg-blue-50/40 dark:bg-blue-900/10">
                            {/* Service Account JSON Upload */}
                            <div className="space-y-3">
                                <div className="flex items-center justify-between">
                                    <div className="flex items-center gap-1.5">
                                        <label className="text-sm font-medium">Service Account JSON</label>
                                        <HelpTooltip
                                            content={
                                                <>
                                                    <strong>GCP Service Account key</strong> — required when Use Vertex AI is enabled. Stored per-instance as <code>GOOGLE_APPLICATION_CREDENTIALS</code>.
                                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                        <li>Create via GCP Console → IAM &amp; Admin → Service Accounts → Keys → Add key (JSON)</li>
                                                        <li>Required IAM role: <code>roles/aiplatform.user</code></li>
                                                        <li>Use the Verify button after upload to confirm Vertex AI access</li>
                                                    </ul>
                                                </>
                                            }
                                            link="https://cloud.google.com/iam/docs/service-account-overview"
                                            linkText="Service accounts"
                                        />
                                    </div>
                                    {credentials?.uploaded && (
                                        <button
                                            type="button"
                                            onClick={handleVerifyCredentials}
                                            disabled={verifying}
                                            className="text-xs px-2 py-1 rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 flex items-center gap-1"
                                        >
                                            {verifying ? <Loader2 className="w-3 h-3 animate-spin" /> : <CheckCircle className="w-3 h-3" />}
                                            {verifying ? 'Verifying...' : 'Verify Credentials'}
                                        </button>
                                    )}
                                </div>

                                {credentials?.uploaded ? (
                                    <div className="flex items-center gap-3 p-2 rounded border border-green-200 dark:border-green-800 bg-green-50/40 dark:bg-green-900/10">
                                        <FileJson className="w-8 h-8 text-green-600" />
                                        <div className="flex-1 min-w-0">
                                            <p className="text-sm font-medium truncate">{credentials.filename}</p>
                                            <p className="text-xs text-muted-foreground truncate">
                                                {credentials.client_email || 'Service Account'}
                                                {credentials.project_id && ` • ${credentials.project_id}`}
                                            </p>
                                        </div>
                                        <button
                                            type="button"
                                            onClick={handleDeleteCredentials}
                                            className="p-1.5 rounded hover:bg-red-100 dark:hover:bg-red-900/30 text-red-600"
                                            title="Delete credentials"
                                        >
                                            <Trash2 className="w-4 h-4" />
                                        </button>
                                    </div>
                                ) : (
                                    <div className="flex items-center gap-2">
                                        <input
                                            ref={fileInputRef}
                                            type="file"
                                            accept=".json"
                                            onChange={handleFileUpload}
                                            className="hidden"
                                            id="vertex-json-upload"
                                        />
                                        <label
                                            htmlFor="vertex-json-upload"
                                            className={`flex items-center gap-2 px-3 py-2 rounded border border-dashed border-input cursor-pointer hover:bg-muted/50 ${uploading ? 'opacity-50 pointer-events-none' : ''}`}
                                        >
                                            {uploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
                                            <span className="text-sm">{uploading ? 'Uploading...' : 'Upload Service Account JSON'}</span>
                                        </label>
                                    </div>
                                )}

                                {uploadError && (
                                    <p className="text-xs text-red-600 flex items-center gap-1">
                                        <XCircle className="w-3 h-3" /> {uploadError}
                                    </p>
                                )}

                                {verifyResult && (
                                    <p className={`text-xs flex items-center gap-1 ${verifyResult.status === 'success' ? 'text-green-600' : 'text-red-600'}`}>
                                        {verifyResult.status === 'success' ? <CheckCircle className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
                                        {verifyResult.message}
                                    </p>
                                )}

                                <p className="text-xs text-muted-foreground">
                                    Upload your GCP service account JSON key. Required IAM role: <code>roles/aiplatform.user</code>
                                </p>
                            </div>

                            {/* Project ID and Region */}
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <div className="flex items-center gap-1.5">
                                        <label className="text-sm font-medium">GCP Project ID</label>
                                        <HelpTooltip
                                            content={
                                                <>
                                                    <strong>GCP Project ID</strong> — the project where Vertex AI is enabled and billing is attached.
                                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                        <li>Auto-filled from the uploaded service-account JSON when this field is empty</li>
                                                        <li>Run <code>gcloud projects list</code> or check GCP Console → Dashboard</li>
                                                        <li>Vertex AI API must be enabled on this project</li>
                                                    </ul>
                                                </>
                                            }
                                            link="https://cloud.google.com/resource-manager/docs/creating-managing-projects"
                                            linkText="GCP projects"
                                        />
                                    </div>
                                    <input
                                        type="text"
                                        className="w-full p-2 rounded border border-input bg-background"
                                        value={config.vertex_project || ''}
                                        onChange={(e) => handleChange('vertex_project', e.target.value)}
                                        placeholder="my-project-123"
                                    />
                                    <p className="text-xs text-muted-foreground">Auto-filled from JSON if empty</p>
                                </div>
                                <div className="space-y-2">
                                    <div className="flex items-center gap-1.5">
                                        <label className="text-sm font-medium">GCP Region</label>
                                        <HelpTooltip
                                            content={
                                                <>
                                                    <strong>Vertex AI region</strong> — which GCP region serves the Live API endpoint.
                                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                        <li><code>us-central1</code> (Iowa) is the default and has the widest model availability</li>
                                                        <li>Pick the region closest to your Asterisk PBX for lower round-trip latency</li>
                                                        <li>Some preview models are only available in <code>us-central1</code></li>
                                                    </ul>
                                                </>
                                            }
                                            link="https://cloud.google.com/vertex-ai/generative-ai/docs/learn/locations"
                                            linkText="Vertex AI locations"
                                        />
                                    </div>
                                    <select
                                        className="w-full p-2 rounded border border-input bg-background"
                                        value={config.vertex_location || 'us-central1'}
                                        onChange={(e) => handleChange('vertex_location', e.target.value)}
                                    >
                                        {regions.length > 0 ? (
                                            regions.map((region) => (
                                                <option key={region.value} value={region.value}>
                                                    {region.label}
                                                </option>
                                            ))
                                        ) : (
                                            <>
                                                <option value="us-central1">US Central (Iowa)</option>
                                                <option value="us-east1">US East (South Carolina)</option>
                                                <option value="europe-west1">Europe West (Belgium)</option>
                                                <option value="asia-northeast1">Asia Northeast (Tokyo)</option>
                                            </>
                                        )}
                                    </select>
                                    <p className="text-xs text-muted-foreground">Region for Vertex AI endpoint</p>
                                </div>
                            </div>
                        </div>
                    )}

                    {/* Developer API key — shown when Vertex AI is OFF */}
                    {!config.use_vertex_ai && (
                        <div className="space-y-3">
                            <ProviderCredentialsCard
                                providerKey={providerKey}
                                credentialType="api-key"
                                label="Google API Key"
                                placeholder="AIza..."
                                envVarFallback="GOOGLE_API_KEY"
                                inlineValue={config.api_key}
                                onConfigPatch={(patch) => applyCredentialPatch(patch, onChange)}
                                helpText={
                                    <>
                                        Get a key from{' '}
                                        <a
                                            href="https://aistudio.google.com/apikey"
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="text-primary hover:underline"
                                        >
                                            Google AI Studio
                                        </a>
                                        . Per-instance keys override the env var fallback.
                                    </>
                                }
                            />
                        </div>
                    )}
                </div>
            </div>

            {/* Base URL Section - only shown for Developer API */}
            {!config.use_vertex_ai && (
            <div>
                <h4 className="font-semibold mb-3">API Endpoint</h4>
                <div className="space-y-2">
                    <div className="flex items-center gap-1.5">
                        <label className="text-sm font-medium">
                            WebSocket Endpoint
                            <span className="text-xs text-muted-foreground ml-2">(websocket_endpoint)</span>
                        </label>
                        <HelpTooltip
                            content={
                                <>
                                    <strong>Gemini Live WebSocket URL</strong> — the bidirectional streaming endpoint for the Developer API.
                                    <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                        <li>Default points at <code>v1beta</code> BidiGenerateContent — required for current Live models</li>
                                        <li>Only edit if Google publishes a stable <code>v1</code> Live WS path or you're routing through a proxy</li>
                                        <li>Ignored when Use Vertex AI is enabled (Vertex builds its endpoint from project + region)</li>
                                    </ul>
                                </>
                            }
                            link="https://ai.google.dev/api/multimodal-live"
                            linkText="Live WS API"
                        />
                    </div>
                    <input
                        type="text"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.websocket_endpoint || 'wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent'}
                        onChange={(e) => handleChange('websocket_endpoint', e.target.value)}
                        placeholder="wss://generativelanguage.googleapis.com/ws/..."
                    />
                    <p className="text-xs text-muted-foreground">
                        Google Live bidirectional endpoint. Keep `v1beta` unless Google publishes a stable `v1` Live WS path.
                    </p>
                </div>
            </div>
            )}

            {/* Models & Voice Section */}
            <div>
                <h4 className="font-semibold mb-3">Models & Voice</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">LLM Model</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Gemini Live model</strong> — pricing ~1.5¢/min, sub-second response latency, 24+ language coverage.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>gemini-live-2.5-flash-native-audio</code> — Vertex AI GA, recommended for production</li>
                                            <li><code>gemini-2.5-flash-preview-native-audio-dialog</code> — Developer API preview with native-audio dialog tuning</li>
                                            <li>Models are scoped to their API group — switching Use Vertex AI auto-swaps to a compatible model</li>
                                        </ul>
                                    </>
                                }
                                link="https://ai.google.dev/gemini-api/docs/live-guide"
                                linkText="Live model guide"
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={selectedModel}
                            onChange={(e) => handleChange('llm_model', e.target.value)}
                        >
                            {GOOGLE_LIVE_MODEL_GROUPS.map((group) => {
                                const isVertexGroup = group.label === 'Vertex AI Live API';
                                const isActiveGroup = config.use_vertex_ai ? isVertexGroup : !isVertexGroup;
                                return (
                                    <optgroup key={group.label} label={group.label}>
                                        {group.options.map((modelOption) => (
                                            <option 
                                                key={modelOption.value} 
                                                value={modelOption.value}
                                                disabled={!isActiveGroup}
                                                className={!isActiveGroup ? 'text-muted-foreground' : ''}
                                            >
                                                {modelOption.label}{!isActiveGroup ? ' (requires ' + (isVertexGroup ? 'Vertex AI' : 'Developer API') + ')' : ''}
                                            </option>
                                        ))}
                                    </optgroup>
                                );
                            })}
                            {!GOOGLE_LIVE_SUPPORTED_MODELS.includes(selectedModel) && (
                                <optgroup label="Custom">
                                    <option value={selectedModel}>{selectedModel}</option>
                                </optgroup>
                            )}
                        </select>
                        <p className="text-xs text-muted-foreground">
                            {config.use_vertex_ai 
                                ? 'Showing Vertex AI models. Developer API models are disabled.'
                                : 'Showing Developer API models. Vertex AI models are disabled.'}
                            <a href="https://ai.google.dev/gemini-api/docs/live-guide" target="_blank" rel="noopener noreferrer" className="ml-1 text-blue-500 hover:underline">API Docs ↗</a>
                        </p>
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Default TTS Voice</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Gemini Live voice</strong> — 30 named voices, each with a tonal descriptor (e.g. Aoede=Breezy, Kore=Firm, Charon=Informative, Sulafat=Warm). Agents can override this per agent (Agents page); this value is the fallback when an agent doesn't set its own voice.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>All voices are multilingual — they auto-switch across 70+ languages with no configuration</li>
                                            <li>Tone is a quick personality hint; preview in AI Studio before committing</li>
                                            <li>Picking <strong>Aoede</strong> or <strong>Kore</strong> is a safe default for support-agent personas</li>
                                        </ul>
                                    </>
                                }
                                link="https://ai.google.dev/gemini-api/docs/speech-generation"
                                linkText="Speech generation"
                            />
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.tts_voice_name || 'Aoede'}
                            onChange={(e) => handleChange('tts_voice_name', e.target.value)}
                        >
                            {GOOGLE_LIVE_VOICE_OPTIONS.map((voice) => (
                                <option key={voice.value} value={voice.value}>
                                    {voice.value} — {voice.tone}
                                </option>
                            ))}
                            {config.tts_voice_name && !GOOGLE_LIVE_SUPPORTED_VOICE_NAMES.includes(config.tts_voice_name) && (
                                <optgroup label="Custom">
                                    <option value={config.tts_voice_name}>{config.tts_voice_name}</option>
                                </optgroup>
                            )}
                        </select>
                        <p className="text-xs text-muted-foreground">
                            Multilingual voices — auto-switch across 70+ languages without configuration.
                            <a href="https://ai.google.dev/gemini-api/docs/speech-generation" target="_blank" rel="noopener noreferrer" className="ml-1 text-blue-500 hover:underline">Voice Docs ↗</a>
                        </p>
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Temperature</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Sampling temperature</strong> (0.0–2.0) — randomness of model output.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><strong>0.0–0.3:</strong> deterministic, good for scripted IVR / structured tool calls</li>
                                            <li><strong>0.7</strong> (default): balanced conversational tone</li>
                                            <li><strong>1.0+:</strong> more creative; avoid for transactional flows where consistency matters</li>
                                        </ul>
                                    </>
                                }
                            />
                        </div>
                        <input
                            type="number"
                            step="0.1"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.llm_temperature || 0.7}
                            onChange={(e) => handleChange('llm_temperature', parseFloat(e.target.value))}
                        />
                        <p className="text-xs text-muted-foreground">
                            Controls randomness (0.0-2.0). Lower = more focused, higher = more creative.
                        </p>
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Max Output Tokens</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Per-turn output cap</strong> — maximum tokens the model may produce in a single response.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li>8192 (default) is generous for phone-call turns; most voice replies stay well under 200 tokens</li>
                                            <li>Lower it (e.g. 512) to discourage rambling responses and reduce time-to-first-audio jitter</li>
                                            <li>Hitting this cap mid-sentence truncates the reply — keep headroom</li>
                                        </ul>
                                    </>
                                }
                            />
                        </div>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.llm_max_output_tokens || 8192}
                            onChange={(e) => handleChange('llm_max_output_tokens', parseInt(e.target.value))}
                        />
                        <p className="text-xs text-muted-foreground">
                            Maximum tokens in response. Higher allows longer answers but increases latency.
                        </p>
                    </div>
                </div>

                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">Advanced Sampling</h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Top P</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Nucleus sampling</strong> (0.0–1.0) — restricts sampling to the smallest set of tokens whose cumulative probability exceeds P.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>0.95 (default) is balanced; lower values constrain the vocabulary further</li>
                                                <li>Usually tune temperature OR top_p, not both</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <input
                                type="number"
                                step="0.01"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.llm_top_p || 0.95}
                                onChange={(e) => handleChange('llm_top_p', parseFloat(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Nucleus sampling (0.0-1.0). Considers tokens comprising top P probability mass.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Top K</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Top-K sampling</strong> — at each step, sample only from the K most likely next tokens.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>40 (default) is a reasonable balance for conversational use</li>
                                                <li>Lower values = more focused / deterministic; higher = more diverse</li>
                                                <li>Combined with top_p — the more restrictive of the two wins</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.llm_top_k || 40}
                                onChange={(e) => handleChange('llm_top_k', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Limits to top K most likely tokens. Lower = more focused responses.
                            </p>
                        </div>
                    </div>
                </div>

                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">Audio Configuration</h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Input Encoding</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Codec coming FROM Asterisk</strong> — the raw audio format AudioSocket delivers to AAVA.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><strong>μ-law</strong> (ulaw) — standard PSTN / SIP telephony; matches most chan_pjsip setups</li>
                                                <li><strong>pcm16 / linear16</strong> — uncompressed 16-bit PCM if Asterisk transcodes upstream</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.input_encoding || 'ulaw'}
                                onChange={(e) => handleChange('input_encoding', e.target.value)}
                            >
                                <option value="ulaw">μ-law</option>
                                <option value="pcm16">PCM16</option>
                                <option value="linear16">Linear16</option>
                            </select>
                            <p className="text-xs text-muted-foreground">
                                Audio format from Asterisk. Use μ-law for standard telephony.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Input Sample Rate (Hz)</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Sample rate FROM Asterisk</strong>.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><strong>8000 Hz</strong> — standard telephony (narrowband)</li>
                                                <li><strong>16000 Hz</strong> — wideband (G.722, Opus); only if your dialplan transcodes</li>
                                                <li>Must match what Asterisk actually emits — mismatch causes chipmunk / slow-talk artifacts</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.input_sample_rate_hz || 8000}
                                onChange={(e) => handleChange('input_sample_rate_hz', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Sample rate from Asterisk. Standard telephony uses 8000 Hz.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Output Encoding</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Codec returned BY Google</strong> — the raw TTS format on the wire from the Live API.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><strong>linear16</strong> — what Gemini natively returns; best quality</li>
                                                <li>μ-law / pcm16 only if you're routing through a transcoding proxy</li>
                                                <li>Will be re-encoded to <strong>Target Encoding</strong> before being sent to Asterisk</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.output_encoding || 'linear16'}
                                onChange={(e) => handleChange('output_encoding', e.target.value)}
                            >
                                <option value="linear16">Linear16</option>
                                <option value="pcm16">PCM16</option>
                                <option value="ulaw">μ-law</option>
                            </select>
                            <p className="text-xs text-muted-foreground">
                                Audio format from Google API. Linear16 provides best quality.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Output Sample Rate (Hz)</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Sample rate Google emits</strong>.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><strong>24000 Hz</strong> — Gemini's native TTS rate; do not change unless you know the model emits otherwise</li>
                                                <li>AAVA resamples 24k → 8k for telephony before playback</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.output_sample_rate_hz || 24000}
                                onChange={(e) => handleChange('output_sample_rate_hz', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Sample rate from Google. 24000 Hz is native for Gemini audio.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Target Encoding</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Codec sent TO Asterisk</strong> — final playback format after AAVA transcodes Gemini's audio.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><strong>μ-law</strong> — match your Asterisk channel's negotiated codec (PSTN default)</li>
                                                <li>Mismatch with Asterisk = no audio or garbled playback</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.target_encoding || 'ulaw'}
                                onChange={(e) => handleChange('target_encoding', e.target.value)}
                            >
                                <option value="ulaw">μ-law</option>
                                <option value="pcm16">PCM16</option>
                                <option value="linear16">Linear16</option>
                            </select>
                            <p className="text-xs text-muted-foreground">
                                Final format for playback to caller. Match your Asterisk codec.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Target Sample Rate (Hz)</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Sample rate sent TO Asterisk</strong> for playback.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><strong>8000 Hz</strong> — standard PSTN telephony; pair with μ-law target encoding</li>
                                                <li><strong>16000 Hz</strong> — wideband (Opus/G.722) channels only</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.target_sample_rate_hz || 8000}
                                onChange={(e) => handleChange('target_sample_rate_hz', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Final sample rate for playback. 8000 Hz for standard telephony.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Provider Input Encoding</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Codec sent TO Google</strong> — what AAVA re-encodes the caller's voice into before streaming to Gemini.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><strong>linear16</strong> — required by Gemini Live; do not change</li>
                                                <li>pcm16 is the same byte format under a different name</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.provider_input_encoding || 'linear16'}
                                onChange={(e) => handleChange('provider_input_encoding', e.target.value)}
                            >
                                <option value="linear16">Linear16</option>
                                <option value="pcm16">PCM16</option>
                            </select>
                            <p className="text-xs text-muted-foreground">
                                Format sent to Google API. Linear16 is required by Gemini.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Provider Input Sample Rate (Hz)</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Sample rate sent TO Google</strong>.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><strong>16000 Hz</strong> — optimal for Gemini Live STT accuracy; AAVA upsamples 8k → 16k automatically</li>
                                                <li>Don't go below 16000 — quality drops noticeably</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.provider_input_sample_rate_hz || 16000}
                                onChange={(e) => handleChange('provider_input_sample_rate_hz', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Sample rate for Google API input. 16000 Hz is optimal for Gemini STT.
                            </p>
                        </div>
                    </div>
                </div>

                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">Transcription & Modalities</h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Greeting</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>First-utterance greeting</strong> — spoken (or sent as text) immediately when the call connects, before the user speaks.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>Leave blank to let the system prompt drive the opener</li>
                                                <li>Short greetings reduce time-to-first-audio and feel snappier</li>
                                                <li>Will be voiced by the selected TTS voice in 1-1.5s</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <input
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.greeting || ''}
                                onChange={(e) => handleChange('greeting', e.target.value)}
                                placeholder="Hi! I'm powered by Google Gemini Live API."
                            />
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Response Modalities</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Which modalities Gemini returns</strong> on each turn.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li><strong>Audio Only</strong> (default for voice) — TTS is generated server-side</li>
                                                <li><strong>Text Only</strong> — no TTS; useful for chat-style integrations or external TTS pipelines</li>
                                                <li><strong>Audio &amp; Text</strong> — both streams; enables real-time transcript display alongside playback</li>
                                            </ul>
                                        </>
                                    }
                                    link="https://ai.google.dev/api/multimodal-live"
                                    linkText="Modalities"
                                />
                            </div>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.response_modalities || 'audio'}
                                onChange={(e) => handleChange('response_modalities', e.target.value)}
                            >
                                <option value="audio">Audio Only</option>
                                <option value="text">Text Only</option>
                                <option value="audio_text">Audio & Text</option>
                            </select>
                        </div>
                        <div className="flex items-center space-x-2">
                            <input
                                type="checkbox"
                                id="enable_input_transcription"
                                className="rounded border-input"
                                checked={config.enable_input_transcription ?? true}
                                onChange={(e) => handleChange('enable_input_transcription', e.target.checked)}
                            />
                            <div className="flex items-center gap-1.5">
                                <label htmlFor="enable_input_transcription" className="text-sm font-medium">Enable Input Transcription</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Caller-side STT transcription</strong> — when on, Gemini returns a text transcript of what the user said.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>Required for call logs, post-call analytics, and tool calls that depend on user text</li>
                                                <li>Adds minor token cost but is generally cheap</li>
                                                <li>Recommended <strong>on</strong> for production</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                        </div>
                        <div className="flex items-center space-x-2">
                            <input
                                type="checkbox"
                                id="enable_output_transcription"
                                className="rounded border-input"
                                checked={config.enable_output_transcription ?? true}
                                onChange={(e) => handleChange('enable_output_transcription', e.target.checked)}
                            />
                            <div className="flex items-center gap-1.5">
                                <label htmlFor="enable_output_transcription" className="text-sm font-medium">Enable Output Transcription</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Assistant-side TTS transcription</strong> — text of what the model just spoke.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>Useful for transcript pairing, supervisor review, and barge-in handling</li>
                                                <li>Recommended <strong>on</strong> — required by some downstream tool-call heuristics</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                        </div>
                        <div className="flex items-center space-x-2">
                            <input
                                type="checkbox"
                                id="enabled"
                                className="rounded border-input"
                                checked={config.enabled ?? true}
                                onChange={(e) => handleChange('enabled', e.target.checked)}
                            />
                            <div className="flex items-center gap-1.5">
                                <label htmlFor="enabled" className="text-sm font-medium">Enabled</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Provider enabled flag</strong> — gates whether this provider instance can be selected as the active AI engine.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>Disabled providers are hidden from the active-provider dropdown</li>
                                                <li>Does not delete config — toggle back on at any time</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Input Gain Target RMS</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Target RMS level</strong> for inbound caller audio (linear PCM amplitude, 0–32767 scale).
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>0 disables AGC — audio is forwarded unchanged</li>
                                                <li>Use only if you see chronically quiet callers (e.g. soft-talkers, distant mic)</li>
                                                <li>Typical values around 3000–5000; pair with Input Gain Max dB to bound boost</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.input_gain_target_rms || 0}
                                onChange={(e) => handleChange('input_gain_target_rms', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">Optional normalization target for inbound audio.</p>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Input Gain Max dB</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Maximum boost (dB)</strong> the AGC may apply when normalizing toward the target RMS.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>0 disables boost (no normalization)</li>
                                                <li>6–12 dB is a safe range for quiet callers; higher amplifies background hiss</li>
                                                <li>Only effective when Input Gain Target RMS &gt; 0</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.input_gain_max_db || 0}
                                onChange={(e) => handleChange('input_gain_max_db', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">Optional max gain applied during normalization.</p>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-1.5">
                                <label className="text-sm font-medium">Farewell Hangup Delay (seconds)</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Grace period after farewell audio</strong> finishes playing before AAVA actually hangs up the SIP channel.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>Prevents Asterisk from cutting off the last syllable of "Goodbye."</li>
                                                <li>Leave blank to inherit the global default (2.5s)</li>
                                                <li>Set to 0 only if you control exactly when audio ends</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                            <input
                                type="number"
                                step="0.5"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.farewell_hangup_delay_sec ?? ''}
                                onChange={(e) => handleChange('farewell_hangup_delay_sec', e.target.value ? parseFloat(e.target.value) : null)}
                                placeholder="Use global default (2.5s)"
                            />
                            <p className="text-xs text-muted-foreground">
                                Seconds to wait after farewell audio before hanging up. Leave empty to use global default.
                            </p>
                        </div>
                    </div>
                </div>

                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">Hangup Fallback Tuning</h4>
                    <p className="text-xs text-muted-foreground">
                        Used when Google Live does not emit a reliable turn-complete event after a hangup farewell.
                    </p>
                    <div className="space-y-3 border border-amber-300/40 rounded-lg p-3 bg-amber-500/5">
                        <div className="flex items-center space-x-2">
                            <input
                                type="checkbox"
                                id="hangup_markers_enabled"
                                className="rounded border-input"
                                checked={config.hangup_markers_enabled ?? false}
                                onChange={(e) => handleChange('hangup_markers_enabled', e.target.checked)}
                            />
                            <div className="flex items-center gap-1.5">
                                <label htmlFor="hangup_markers_enabled" className="text-sm font-medium">Enable Marker-Based Hangup Heuristics</label>
                                <HelpTooltip
                                    content={
                                        <>
                                            <strong>Transcript-marker fallback</strong> — scans the output transcript for farewell phrases (e.g. "end_call", "assistant_farewell") to arm <code>cleanup_after_tts</code> when no <code>hangup_call</code> tool fires.
                                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                                <li>Recommended <strong>off</strong> for production — rely on the explicit <code>hangup_call</code> tool</li>
                                                <li>Only enable while debugging cases where Gemini drops turnComplete after a farewell</li>
                                                <li>Can produce false-positive hangups if the bot uses farewell wording mid-call</li>
                                            </ul>
                                        </>
                                    }
                                />
                            </div>
                        </div>
                        <p className="text-xs text-muted-foreground">
                            Advanced: uses transcript marker matching (end_call / assistant_farewell) to arm <code>cleanup_after_tts</code> when a toolCall is missing.
                            Recommended off for production; rely on <code>hangup_call</code> to end calls gracefully.
                        </p>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium flex items-center gap-1">
                                Audio Idle Timeout (sec)
                                <HelpTooltip content="How long to wait after the last audio output before triggering hangup. If the model stops producing audio for this duration after a farewell, the call is ended. Default: 1.25s." />
                            </label>
                            <input
                                type="number"
                                step="0.05"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.hangup_fallback_audio_idle_sec ?? 1.25}
                                onChange={(e) => handleChange('hangup_fallback_audio_idle_sec', e.target.value ? parseFloat(e.target.value) : null)}
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium flex items-center gap-1">
                                Minimum Armed Time (sec)
                                <HelpTooltip content="Minimum time the hangup fallback must be armed before it can fire. Prevents premature hangup if the model is still processing. Default: 0.8s." />
                            </label>
                            <input
                                type="number"
                                step="0.05"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.hangup_fallback_min_armed_sec ?? 0.8}
                                onChange={(e) => handleChange('hangup_fallback_min_armed_sec', e.target.value ? parseFloat(e.target.value) : null)}
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium flex items-center gap-1">
                                No Audio Timeout (sec)
                                <HelpTooltip content="If the model produces NO audio at all after hangup_call, wait this long before forcing a farewell and disconnect. Covers cases where the model goes silent. Default: 4.0s." />
                            </label>
                            <input
                                type="number"
                                step="0.1"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.hangup_fallback_no_audio_timeout_sec ?? 4.0}
                                onChange={(e) => handleChange('hangup_fallback_no_audio_timeout_sec', e.target.value ? parseFloat(e.target.value) : null)}
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium flex items-center gap-1">
                                Turn Complete Timeout (sec)
                                <HelpTooltip content="After the model's farewell audio finishes, wait this long for a turnComplete event before proceeding with hangup. Default: 2.5s." />
                            </label>
                            <input
                                type="number"
                                step="0.1"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.hangup_fallback_turn_complete_timeout_sec ?? 2.5}
                                onChange={(e) => handleChange('hangup_fallback_turn_complete_timeout_sec', e.target.value ? parseFloat(e.target.value) : null)}
                            />
                        </div>
                    </div>
                </div>

                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">Voice Activity Detection (VAD)</h4>
                    <p className="text-xs text-muted-foreground">
                        Controls Google's server-side speech detection. Higher sensitivity catches shorter utterances but may trigger on background noise.
                    </p>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium flex items-center gap-1">
                                Start of Speech Sensitivity
                                <HelpTooltip content="How aggressively Google detects the START of speech. HIGH catches short utterances (1-2 words) better but may false-trigger on noise. LOW requires more confident speech onset." />
                            </label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={['START_SENSITIVITY_LOW', 'START_SENSITIVITY_HIGH', 'START_SENSITIVITY_UNSPECIFIED'].includes(config.vad_start_of_speech_sensitivity || '') ? config.vad_start_of_speech_sensitivity : 'START_SENSITIVITY_HIGH'}
                                onChange={(e) => handleChange('vad_start_of_speech_sensitivity', e.target.value)}
                            >
                                <option value="START_SENSITIVITY_LOW">Low</option>
                                <option value="START_SENSITIVITY_HIGH">High (Recommended)</option>
                                <option value="START_SENSITIVITY_UNSPECIFIED">Unspecified (API default)</option>
                            </select>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium flex items-center gap-1">
                                End of Speech Sensitivity
                                <HelpTooltip content="How aggressively Google detects the END of speech. HIGH means faster turn-taking (shorter silence = end of utterance). LOW waits longer before deciding the user stopped talking." />
                            </label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={['END_SENSITIVITY_LOW', 'END_SENSITIVITY_HIGH', 'END_SENSITIVITY_UNSPECIFIED'].includes(config.vad_end_of_speech_sensitivity || '') ? config.vad_end_of_speech_sensitivity : 'END_SENSITIVITY_HIGH'}
                                onChange={(e) => handleChange('vad_end_of_speech_sensitivity', e.target.value)}
                            >
                                <option value="END_SENSITIVITY_LOW">Low</option>
                                <option value="END_SENSITIVITY_HIGH">High (Recommended)</option>
                                <option value="END_SENSITIVITY_UNSPECIFIED">Unspecified (API default)</option>
                            </select>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium flex items-center gap-1">
                                Prefix Padding (ms)
                                <HelpTooltip content="Milliseconds of audio to include BEFORE detected speech start. Lower values reduce latency; higher values capture soft speech onsets. Telephony default: 20ms." />
                            </label>
                            <input
                                type="number"
                                step="10"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.vad_prefix_padding_ms ?? 20}
                                onChange={(e) => handleChange('vad_prefix_padding_ms', e.target.value ? parseInt(e.target.value) : null)}
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium flex items-center gap-1">
                                Silence Duration (ms)
                                <HelpTooltip content="Milliseconds of silence required to mark the end of an utterance. Lower = faster responses but may cut off mid-sentence pauses. Higher = more natural pauses but slower turn-taking. Telephony default: 500ms." />
                            </label>
                            <input
                                type="number"
                                step="50"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.vad_silence_duration_ms ?? 500}
                                onChange={(e) => handleChange('vad_silence_duration_ms', e.target.value ? parseInt(e.target.value) : null)}
                            />
                        </div>
                    </div>
                </div>

                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">Expert Settings</h4>
                    <div className="space-y-3 border border-amber-300/40 rounded-lg p-3 bg-amber-500/5">
                        <div className="flex items-center justify-between gap-3">
                            <div className="flex items-start gap-2">
                                <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5" />
                                <div>
                                    <div className="flex items-center gap-2">
                                        <span className="text-sm font-medium">WebSocket Keepalive (Advanced)</span>
                                        <HelpTooltip content="These settings control provider-level WebSocket keepalive behavior. Only change if you are troubleshooting disconnects. Some Google Live accounts/models may close the connection (1008) when keepalives are enabled." />
                                    </div>
                                    <p className="text-xs text-muted-foreground">
                                        Warning: enabling keepalive can materially change connection stability. Validate with real test calls before production.
                                    </p>
                                </div>
                            </div>
                            <input
                                type="checkbox"
                                className="rounded border-input"
                                checked={expertEnabled}
                                onChange={(e) => {
                                    setExpertEnabled(e.target.checked);
                                }}
                            />
                        </div>

                        <div className={`grid grid-cols-1 md:grid-cols-3 gap-4 ${expertEnabled ? '' : 'opacity-60 pointer-events-none'}`}>
                            <div className="space-y-2">
                                <label className="text-sm font-medium flex items-center gap-1">
                                    Keepalive Enabled
                                    <HelpTooltip content="Sends protocol-level WebSocket ping frames when the connection is idle. If disabled, the provider only relies on normal audio traffic to keep the session alive." />
                                </label>
                                <input
                                    type="checkbox"
                                    className="rounded border-input"
                                    checked={config.ws_keepalive_enabled ?? false}
                                    onChange={(e) => handleChange('ws_keepalive_enabled', e.target.checked)}
                                    disabled={!expertEnabled}
                                />
                                <p className="text-xs text-muted-foreground">
                                    Default: off. Turn on only if you see idle disconnects.
                                </p>
                            </div>

                            <div className="space-y-2">
                                <label className="text-sm font-medium flex items-center gap-1">
                                    Keepalive Interval (sec)
                                    <HelpTooltip content="How often to send ping frames (when idle). Lower values increase ping traffic; higher values reduce traffic but may not prevent idle timeouts." />
                                </label>
                                <input
                                    type="number"
                                    step="0.5"
                                    className="w-full p-2 rounded border border-input bg-background"
                                    value={config.ws_keepalive_interval_sec ?? 15.0}
                                    onChange={(e) => handleChange('ws_keepalive_interval_sec', e.target.value ? parseFloat(e.target.value) : null)}
                                    disabled={!expertEnabled}
                                />
                            </div>

                            <div className="space-y-2">
                                <label className="text-sm font-medium flex items-center gap-1">
                                    Idle Threshold (sec)
                                    <HelpTooltip content="Only send keepalive pings if we haven't sent any realtime audio to Google in the last N seconds. Prevents pinging while audio is actively flowing." />
                                </label>
                                <input
                                    type="number"
                                    step="0.5"
                                    className="w-full p-2 rounded border border-input bg-background"
                                    value={config.ws_keepalive_idle_sec ?? 5.0}
                                    onChange={(e) => handleChange('ws_keepalive_idle_sec', e.target.value ? parseFloat(e.target.value) : null)}
                                    disabled={!expertEnabled}
                                />
                            </div>
                        </div>
                    </div>
                </div>
            </div>

        </div>
    );
};

export default GoogleLiveProviderForm;
