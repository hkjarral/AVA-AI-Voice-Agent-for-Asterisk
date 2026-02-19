import React, { useEffect, useState, useRef, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useConfirmDialog } from '../../../hooks/useConfirmDialog';
import { AlertTriangle, Upload, Trash2, CheckCircle, XCircle, Loader2, FileJson } from 'lucide-react';
import HelpTooltip from '../../ui/HelpTooltip';
import {
    GOOGLE_LIVE_MODEL_GROUPS,
    GOOGLE_LIVE_SUPPORTED_MODELS,
    normalizeGoogleLiveModelForUi,
} from '../../../utils/googleLiveModels';

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
}

const GoogleLiveProviderForm: React.FC<GoogleLiveProviderFormProps> = ({ config, onChange }) => {
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

    // Fetch regions and credentials status
    const fetchVertexData = useCallback(async () => {
        try {
            const [regionsRes, credsRes] = await Promise.all([
                axios.get('/api/config/vertex-ai/regions'),
                axios.get('/api/config/vertex-ai/credentials'),
            ]);
            if (regionsRes.data) {
                setRegions(regionsRes.data.regions || []);
            }
            if (credsRes.data) {
                setCredentials(credsRes.data);
            }
        } catch (e) {
            console.error('Failed to fetch Vertex AI data:', e);
        }
    }, []);

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
            const res = await axios.post('/api/config/vertex-ai/credentials', formData, {
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
            await axios.delete('/api/config/vertex-ai/credentials');
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
            const res = await axios.post('/api/config/vertex-ai/verify');
            setVerifyResult({ status: 'success', message: res.data.message || 'Credentials verified!' });
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
                                const useVertex = e.target.checked;
                                // Auto-switch to a compatible model when toggling API mode
                                const currentModel = config.llm_model || '';
                                const isCurrentModelVertex = currentModel.startsWith('gemini-live-');
                                const needsModelSwitch = useVertex ? !isCurrentModelVertex : isCurrentModelVertex;
                                
                                if (needsModelSwitch) {
                                    const newModel = useVertex 
                                        ? 'gemini-live-2.5-flash-native-audio'  // Default Vertex AI model
                                        : 'gemini-2.5-flash-native-audio-latest';  // Default Developer API model
                                    onChange({ ...config, use_vertex_ai: useVertex, llm_model: newModel });
                                } else {
                                    handleChange('use_vertex_ai', useVertex);
                                }
                            }}
                        />
                        <div>
                            <label htmlFor="use_vertex_ai" className="text-sm font-medium cursor-pointer">
                                Use Vertex AI (Enterprise / GCP)
                            </label>
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
                                    <label className="text-sm font-medium">Service Account JSON</label>
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
                                    <label className="text-sm font-medium">GCP Project ID</label>
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
                                    <label className="text-sm font-medium">GCP Region</label>
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
                        <div className="space-y-2">
                            <label className="text-sm font-medium">API Key (Environment Variable)</label>
                            <input
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.api_key || '${GOOGLE_API_KEY}'}
                                onChange={(e) => handleChange('api_key', e.target.value)}
                                placeholder="${GOOGLE_API_KEY}"
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
                    <label className="text-sm font-medium">
                        WebSocket Endpoint
                        <span className="text-xs text-muted-foreground ml-2">(websocket_endpoint)</span>
                    </label>
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
                        <label className="text-sm font-medium">LLM Model</label>
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
                        <label className="text-sm font-medium">TTS Voice Name</label>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.tts_voice_name || 'Aoede'}
                            onChange={(e) => handleChange('tts_voice_name', e.target.value)}
                        >
                            <optgroup label="Female">
                                <option value="Aoede">Aoede</option>
                                <option value="Kore">Kore</option>
                                <option value="Leda">Leda</option>
                            </optgroup>
                            <optgroup label="Male">
                                <option value="Puck">Puck</option>
                                <option value="Charon">Charon</option>
                                <option value="Fenrir">Fenrir</option>
                                <option value="Orus">Orus</option>
                                <option value="Zephyr">Zephyr</option>
                            </optgroup>
                        </select>
                        <p className="text-xs text-muted-foreground">
                            Multilingual voices - auto-switches between 24 languages without configuration.
                            <a href="https://firebase.google.com/docs/ai-logic/live-api/configuration" target="_blank" rel="noopener noreferrer" className="ml-1 text-blue-500 hover:underline">Voice Docs ↗</a>
                        </p>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Temperature</label>
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
                        <label className="text-sm font-medium">Max Output Tokens</label>
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
                            <label className="text-sm font-medium">Top P</label>
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
                            <label className="text-sm font-medium">Top K</label>
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
                            <label className="text-sm font-medium">Input Encoding</label>
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
                            <label className="text-sm font-medium">Input Sample Rate (Hz)</label>
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
                            <label className="text-sm font-medium">Output Encoding</label>
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
                            <label className="text-sm font-medium">Output Sample Rate (Hz)</label>
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
                        <label className="text-sm font-medium">Target Encoding</label>
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
                            <label className="text-sm font-medium">Target Sample Rate (Hz)</label>
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
                            <label className="text-sm font-medium">Provider Input Encoding</label>
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
                            <label className="text-sm font-medium">Provider Input Sample Rate (Hz)</label>
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
                            <label className="text-sm font-medium">Greeting</label>
                            <input
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.greeting || ''}
                                onChange={(e) => handleChange('greeting', e.target.value)}
                                placeholder="Hi! I'm powered by Google Gemini Live API."
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Response Modalities</label>
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
                            <label htmlFor="enable_input_transcription" className="text-sm font-medium">Enable Input Transcription</label>
                        </div>
                        <div className="flex items-center space-x-2">
                            <input
                                type="checkbox"
                                id="enable_output_transcription"
                                className="rounded border-input"
                                checked={config.enable_output_transcription ?? true}
                                onChange={(e) => handleChange('enable_output_transcription', e.target.checked)}
                            />
                            <label htmlFor="enable_output_transcription" className="text-sm font-medium">Enable Output Transcription</label>
                        </div>
                        <div className="flex items-center space-x-2">
                            <input
                                type="checkbox"
                                id="enabled"
                                className="rounded border-input"
                                checked={config.enabled ?? true}
                                onChange={(e) => handleChange('enabled', e.target.checked)}
                            />
                            <label htmlFor="enabled" className="text-sm font-medium">Enabled</label>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Input Gain Target RMS</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.input_gain_target_rms || 0}
                                onChange={(e) => handleChange('input_gain_target_rms', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">Optional normalization target for inbound audio.</p>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Input Gain Max dB</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.input_gain_max_db || 0}
                                onChange={(e) => handleChange('input_gain_max_db', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">Optional max gain applied during normalization.</p>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Farewell Hangup Delay (seconds)</label>
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
                            <label htmlFor="hangup_markers_enabled" className="text-sm font-medium">Enable Marker-Based Hangup Heuristics</label>
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
                                value={config.vad_start_of_speech_sensitivity || 'START_SENSITIVITY_HIGH'}
                                onChange={(e) => handleChange('vad_start_of_speech_sensitivity', e.target.value)}
                            >
                                <option value="START_SENSITIVITY_LOW">Low</option>
                                <option value="START_SENSITIVITY_MEDIUM">Medium</option>
                                <option value="START_SENSITIVITY_HIGH">High (Recommended)</option>
                            </select>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium flex items-center gap-1">
                                End of Speech Sensitivity
                                <HelpTooltip content="How aggressively Google detects the END of speech. HIGH means faster turn-taking (shorter silence = end of utterance). LOW waits longer before deciding the user stopped talking." />
                            </label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.vad_end_of_speech_sensitivity || 'END_SENSITIVITY_HIGH'}
                                onChange={(e) => handleChange('vad_end_of_speech_sensitivity', e.target.value)}
                            >
                                <option value="END_SENSITIVITY_LOW">Low</option>
                                <option value="END_SENSITIVITY_MEDIUM">Medium</option>
                                <option value="END_SENSITIVITY_HIGH">High (Recommended)</option>
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
