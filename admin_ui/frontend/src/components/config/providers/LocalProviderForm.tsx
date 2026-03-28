import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { AlertCircle, RefreshCw } from 'lucide-react';

interface LocalProviderFormProps {
    config: any;
    onChange: (newConfig: any) => void;
}

const LocalProviderForm: React.FC<LocalProviderFormProps> = ({ config, onChange }) => {
    // Store raw data for backend discovery
    const [rawModelData, setRawModelData] = useState<any>({ stt: {}, tts: {}, llm: [] });
    // Flattened catalog for easy model lookup
    const [modelCatalog, setModelCatalog] = useState<any>({ stt: [], llm: [], tts: [] });
    const [loading, setLoading] = useState(true);
    const [fetchError, setFetchError] = useState<string | null>(null);

    // Current status from Local AI Server
    const [currentStatus, setCurrentStatus] = useState<any>(null);
    const [statusLoading, setStatusLoading] = useState(true);

    useEffect(() => {
        const fetchModels = async () => {
            try {
                // Fetch installed models from local_ai API
                const res = await axios.get('/api/local-ai/models');
                const data = res.data;
                setRawModelData(data);

                // Flatten STT models (Dict[backend, List[Model]]) -> List[Model]
                const sttModels = Object.values(data.stt || {}).flat();

                // Flatten TTS models (Dict[backend, List[Model]])
                const ttsModels = Object.values(data.tts || {}).flat();

                setModelCatalog({
                    stt: sttModels,
                    llm: data.llm || [],
                    tts: ttsModels
                });
            } catch (err) {
                console.error("Failed to fetch local models", err);
                setFetchError("Could not load installed models. Ensure AI Engine is running.");
            } finally {
                setLoading(false);
            }
        };

        fetchModels();
    }, []);

    const fetchCurrentStatus = useCallback(async () => {
        setStatusLoading(true);
        try {
            const res = await axios.get('/api/system/health');
            if (res.data?.local_ai_server?.status === 'connected') {
                setCurrentStatus(res.data.local_ai_server.details);
            } else {
                setCurrentStatus(null);
            }
        } catch (err) {
            console.error("Failed to fetch current status", err);
        } finally {
            setStatusLoading(false);
        }
    }, []);

    // Fetch status on mount and every 15s to stay fresh
    useEffect(() => {
        fetchCurrentStatus();
        const interval = setInterval(fetchCurrentStatus, 15000);
        return () => clearInterval(interval);
    }, [fetchCurrentStatus]);

    const handleChange = (field: string, value: any) => {
        // If changing model backend, also try to set a sane default model path if available
        if (field === 'stt_backend') {
            // Logic to auto-select recommended model could go here, but for now just change backend
        }
        onChange({ ...config, [field]: value });
    };

    const name = (config?.name || '').toLowerCase();
    const caps = config?.capabilities || [];
    const isFullAgent = (caps.includes('stt') && caps.includes('llm') && caps.includes('tts'));

    // For modular providers, detect role by name or capability
    const isSTT = isFullAgent || name.includes('stt') || caps.includes('stt');
    const isTTS = isFullAgent || name.includes('tts') || caps.includes('tts');
    const isLLM = isFullAgent || name.includes('llm') || caps.includes('llm') || (!name.includes('stt') && !name.includes('tts'));

    // Helpers to find model details
    const getModelPathPlaceholder = (backend: string, type: 'stt' | 'tts') => {
        if (loading) return "Loading...";
        if (backend === 'vosk') return '/app/models/stt/vosk-model-en-us-0.22';
        if (backend === 'sherpa') {
            return config.sherpa_model_type === 'offline'
                ? '/app/models/stt/sherpa-onnx-zipformer-en-2023-06-26'
                : '/app/models/stt/sherpa-onnx-streaming-zipformer-en-2023-06-26';
        }
        if (backend === 'tone') return '/app/models/stt/t-one';
        if (backend === 'piper') return '/app/models/tts/en_US-lessac-medium.onnx';
        if (backend === 'kokoro') return '/app/models/tts/kokoro';
        return '';
    };

    return (
        <div className="space-y-6">
            {/* Full Agent Notice */}
            {isFullAgent && (
                <div className="bg-green-50/50 dark:bg-green-900/10 p-3 rounded-md border border-green-200 dark:border-green-900/30 text-sm text-green-800 dark:text-green-300">
                    <strong>Full Agent Mode:</strong> This provider handles STT, LLM, and TTS together via Local AI Server.
                </div>
            )}

            {/* Currently Loaded Models - Live Status */}
            {currentStatus && (
                <div className="bg-blue-50/50 dark:bg-blue-900/10 p-4 rounded-md border border-blue-200 dark:border-blue-900/30">
                    <div className="flex items-center justify-between mb-3">
                        <h4 className="font-semibold text-sm text-blue-800 dark:text-blue-300">📊 Currently Loaded</h4>
                        <button
                            type="button"
                            onClick={fetchCurrentStatus}
                            className="p-1 rounded hover:bg-blue-100 dark:hover:bg-blue-800/30 transition-colors"
                            title="Refresh status"
                        >
                            <RefreshCw className={`w-3.5 h-3.5 text-blue-600 dark:text-blue-400 ${statusLoading ? 'animate-spin' : ''}`} />
                        </button>
                    </div>
                    {(!!currentStatus?.config?.degraded || !!currentStatus?.config?.mock_models) && (
                        <div className="mb-3 space-y-2">
                            {!!currentStatus?.config?.mock_models && (
                                <div className="p-2 bg-blue-500/10 border border-blue-500/30 rounded text-xs text-blue-700 dark:text-blue-300 flex items-start gap-2">
                                    <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                                    <div>
                                        <div className="font-medium">Mock models enabled</div>
                                        <div className="opacity-80">`LOCAL_AI_MOCK_MODELS=1` is set; status may not reflect real model loading.</div>
                                    </div>
                                </div>
                            )}

                            {!!currentStatus?.config?.degraded && (
                                <div className="p-2 bg-yellow-500/10 border border-yellow-500/30 rounded text-xs text-yellow-700 dark:text-yellow-300">
                                    <div className="flex items-start gap-2">
                                        <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                                        <div>
                                            <div className="font-medium">Degraded mode</div>
                                            <div className="opacity-80">Some components failed to initialize on startup.</div>
                                        </div>
                                    </div>
                                    {currentStatus?.config?.startup_errors && Object.keys(currentStatus.config.startup_errors).length > 0 && (
                                        <details className="mt-2">
                                            <summary className="cursor-pointer opacity-90">
                                                Startup errors ({Object.keys(currentStatus.config.startup_errors).length})
                                            </summary>
                                            <ul className="mt-2 space-y-1 opacity-90">
                                                {Object.entries(currentStatus.config.startup_errors).map(([k, v]: any) => (
                                                    <li key={k} className="flex gap-2">
                                                        <span className="font-mono">{k}:</span>
                                                        <span className="break-words">{String(v)}</span>
                                                    </li>
                                                ))}
                                            </ul>
                                        </details>
                                    )}
                                </div>
                            )}
                        </div>
                    )}
                    <div className="space-y-0 divide-y divide-blue-200/50 dark:divide-blue-800/30">
                        {/* STT Status */}
                        <div className="py-2.5 first:pt-0">
                            <div className="flex items-center gap-2">
                                <span className="text-[10px] uppercase tracking-wider text-muted-foreground w-8 flex-shrink-0">STT</span>
                                {currentStatus.models?.stt?.loaded ? (
                                    <span className="w-1.5 h-1.5 rounded-full bg-green-500 flex-shrink-0"></span>
                                ) : (
                                    <span className="w-1.5 h-1.5 rounded-full bg-yellow-500 flex-shrink-0"></span>
                                )}
                                <span className="text-sm font-medium">
                                    {currentStatus.stt_backend?.charAt(0).toUpperCase() + currentStatus.stt_backend?.slice(1) || 'Unknown'}
                                </span>
                                {currentStatus.kroko_embedded && (
                                    <span className="text-[10px] px-1.5 py-0.5 bg-purple-100 dark:bg-purple-900/30 text-purple-600 rounded-full">
                                        Embedded:{currentStatus.kroko_port || 6006}
                                    </span>
                                )}
                                {currentStatus.stt_backend === 'kroko' && !currentStatus.kroko_embedded && (
                                    <span className="text-[10px] px-1.5 py-0.5 bg-blue-100 dark:bg-blue-900/30 text-blue-600 rounded-full">Cloud</span>
                                )}
                            </div>
                            {currentStatus.models?.stt?.path && (
                                <div className="ml-8 text-xs text-muted-foreground truncate" title={currentStatus.models.stt.path}>
                                    {currentStatus.models.stt.path}
                                </div>
                            )}
                        </div>

                        {/* LLM Status */}
                        <div className="py-2.5">
                            <div className="flex items-center gap-2">
                                <span className="text-[10px] uppercase tracking-wider text-muted-foreground w-8 flex-shrink-0">LLM</span>
                                {currentStatus.models?.llm?.loaded ? (
                                    <span className="w-1.5 h-1.5 rounded-full bg-green-500 flex-shrink-0"></span>
                                ) : (
                                    <span className="w-1.5 h-1.5 rounded-full bg-yellow-500 flex-shrink-0"></span>
                                )}
                                <span className="text-sm font-medium truncate">
                                    {currentStatus.models?.llm?.path?.split('/').pop() || 'Not loaded'}
                                </span>
                            </div>
                            <div className="ml-8 flex items-center gap-3 text-xs text-muted-foreground mt-0.5">
                                <span>ctx: <span className="font-mono">{currentStatus.models?.llm?.config?.context || '—'}</span></span>
                                <span>threads: <span className="font-mono">{currentStatus.models?.llm?.config?.threads || '—'}</span></span>
                                {currentStatus.models?.llm?.config?.max_tokens && (
                                    <span>max_tok: <span className="font-mono">{currentStatus.models.llm.config.max_tokens}</span></span>
                                )}
                            </div>
                        </div>

                        {/* TTS Status */}
                        <div className="py-2.5 last:pb-0">
                            <div className="flex items-center gap-2">
                                <span className="text-[10px] uppercase tracking-wider text-muted-foreground w-8 flex-shrink-0">TTS</span>
                                {currentStatus.models?.tts?.loaded ? (
                                    <span className="w-1.5 h-1.5 rounded-full bg-green-500 flex-shrink-0"></span>
                                ) : (
                                    <span className="w-1.5 h-1.5 rounded-full bg-yellow-500 flex-shrink-0"></span>
                                )}
                                <span className="text-sm font-medium">
                                    {currentStatus.tts_backend?.charAt(0).toUpperCase() + currentStatus.tts_backend?.slice(1) || 'Unknown'}
                                </span>
                                {currentStatus.kokoro_mode === 'local' && (
                                    <span className="text-[10px] px-1.5 py-0.5 bg-green-100 dark:bg-green-900/30 text-green-600 rounded-full">Local</span>
                                )}
                                {currentStatus.kokoro_mode === 'api' && (
                                    <span className="text-[10px] px-1.5 py-0.5 bg-blue-100 dark:bg-blue-900/30 text-blue-600 rounded-full">API</span>
                                )}
                            </div>
                            <div className="ml-8 text-xs text-muted-foreground">
                                {currentStatus.kokoro_voice ? `Voice: ${currentStatus.kokoro_voice}` : currentStatus.models?.tts?.path || 'Not configured'}
                            </div>
                        </div>
                    </div>
                </div>
            )}
            {statusLoading && !currentStatus && (
                <div className="bg-muted/50 p-4 rounded-md animate-pulse">
                    <div className="h-4 bg-muted rounded w-1/3 mb-2"></div>
                    <div className="h-3 bg-muted rounded w-full"></div>
                </div>
            )}

            {/* Error Banner */}
            {fetchError && (
                <div className="bg-red-50 dark:bg-red-900/10 p-3 rounded-md border border-red-200 dark:border-red-900/30 text-sm text-red-600 dark:text-red-400 flex items-center gap-2">
                    <AlertCircle className="w-4 h-4" />
                    {fetchError}
                </div>
            )}

            {/* Greeting (for full agents) */}
            {isFullAgent && (
                <div>
                    <h4 className="font-semibold mb-3">Greeting</h4>
                    <div className="space-y-2">
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.greeting || ''}
                            onChange={(e) => handleChange('greeting', e.target.value)}
                            placeholder="Hello! I'm your local AI assistant."
                        />
                        <p className="text-xs text-muted-foreground">
                            Initial greeting message spoken when a call starts.
                        </p>
                    </div>
                    <div className="space-y-2 mt-4">
                        <label className="text-sm font-medium">Instructions (System Prompt)</label>
                        <textarea
                            className="w-full p-2 rounded border border-input bg-background min-h-[80px]"
                            value={config.instructions || ''}
                            onChange={(e) => handleChange('instructions', e.target.value)}
                            placeholder="You are a helpful voice assistant. Be concise and friendly."
                        />
                        <p className="text-xs text-muted-foreground">
                            System prompt that defines the AI's behavior and personality.
                        </p>
                    </div>
                </div>
            )}

            {/* Connection Settings */}
            <div>
                <h4 className="font-semibold mb-3">Connection Settings</h4>
                <div className="space-y-2">
                    <label className="text-sm font-medium">
                        {isFullAgent ? 'Base URL / WebSocket URL' : 'WebSocket URL'}
                        <span className="text-xs text-muted-foreground ml-2">({isFullAgent ? 'base_url' : 'ws_url'})</span>
                    </label>
                    <input
                        type="text"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={isFullAgent
                            ? (config.base_url || '${LOCAL_WS_URL:-ws://local_ai_server:8765}')
                            : (config.ws_url || '${LOCAL_WS_URL:-ws://local_ai_server:8765}')}
                        onChange={(e) => handleChange(isFullAgent ? 'base_url' : 'ws_url', e.target.value)}
                        placeholder="${LOCAL_WS_URL:-ws://local_ai_server:8765}"
                    />
                    <p className="text-xs text-muted-foreground">
                        WebSocket URL for local AI server. Change port if running on custom configuration.
                    </p>

                    <div className="space-y-2 mt-3">
                        <label className="text-sm font-medium">
                            Auth Token (optional)
                            <span className="text-xs text-muted-foreground ml-2">(auth_token)</span>
                        </label>
                        <input
                            type="password"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.auth_token || '${LOCAL_WS_AUTH_TOKEN:-}'}
                            onChange={(e) => handleChange('auth_token', e.target.value)}
                            placeholder="${LOCAL_WS_AUTH_TOKEN:-}"
                        />
                        <p className="text-xs text-muted-foreground">
                            If set, local-ai-server requires an auth handshake; token must match `LOCAL_WS_AUTH_TOKEN` in both containers.
                        </p>
                    </div>
                </div>
            </div>

            {/* Connection Parameters */}
            <div>
                <h4 className="font-semibold mb-3">Connection Parameters</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Connect Timeout (s)</label>
                        <input
                            type="number"
                            step="0.1"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.connect_timeout_sec || 5.0}
                            onChange={(e) => handleChange('connect_timeout_sec', parseFloat(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Response Timeout (s)</label>
                        <input
                            type="number"
                            step="0.1"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.response_timeout_sec || 5.0}
                            onChange={(e) => handleChange('response_timeout_sec', parseFloat(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Farewell Mode</label>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.farewell_mode || 'asterisk'}
                            onChange={(e) => handleChange('farewell_mode', e.target.value)}
                        >
                            <option value="asterisk">Asterisk Sound (Reliable)</option>
                            <option value="tts">Local TTS (Fast Hardware)</option>
                        </select>
                        <p className="text-xs text-muted-foreground">
                            "asterisk" for slow hardware, "tts" for fast hardware (&lt;5s LLM response).
                        </p>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Farewell TTS Timeout (s)</label>
                        <input
                            type="number"
                            step="1"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.farewell_timeout_sec || 30.0}
                            onChange={(e) => handleChange('farewell_timeout_sec', parseFloat(e.target.value))}
                            disabled={config.farewell_mode !== 'tts'}
                        />
                        <p className="text-xs text-muted-foreground">
                            Only for TTS mode. Set based on LLM warmup time in logs.
                        </p>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Chunk Size (ms)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.chunk_ms || 200}
                            onChange={(e) => handleChange('chunk_ms', parseInt(e.target.value))}
                        />
                    </div>
                </div>
                <div className="flex items-center space-x-2 mt-2">
                    <input
                        type="checkbox"
                        id="local_continuous_input"
                        className="rounded border-input"
                        checked={config.continuous_input ?? true}
                        onChange={(e) => handleChange('continuous_input', e.target.checked)}
                    />
                    <label htmlFor="local_continuous_input" className="text-sm font-medium">Continuous Input</label>
                    <span className="text-xs text-muted-foreground">
                        — Keeps STT listening while AI speaks, enabling natural interruptions.
                    </span>
                </div>
            </div>

            {/* STT Backend Settings */}
            {isSTT && (
                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">STT (Speech-to-Text)</h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">STT Backend</label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.stt_backend || 'vosk'}
                                onChange={(e) => handleChange('stt_backend', e.target.value)}
                            >
                                {/* Default option if loading */}
                                {loading && <option>Loading...</option>}

                                {/* Dynamic options based on available backends */}
                                {!loading && Object.keys(rawModelData.stt).map(backend => (
                                    <option key={backend} value={backend}>
                                        {backend.charAt(0).toUpperCase() + backend.slice(1)}
                                    </option>
                                ))}

                                {/* Fallback options if API fails or data empty */}
                                {!loading && Object.keys(rawModelData.stt).length === 0 && (
                                    <>
                                        <option value="vosk">Vosk (Local)</option>
                                        <option value="kroko">Kroko</option>
                                        <option value="sherpa">Sherpa-ONNX (Local)</option>
                                        <option value="tone">T-one</option>
                                    </>
                                )}
                            </select>
                        </div>

                        {/* Vosk settings */}
                        {config.stt_backend === 'vosk' && (
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Vosk Model Path</label>
                                <div className="relative">
                                    <input
                                        type="text"
                                        className="w-full p-2 rounded border border-input bg-background"
                                        value={config.stt_model || ''}
                                        onChange={(e) => handleChange('stt_model', e.target.value)}
                                        placeholder={getModelPathPlaceholder('vosk', 'stt')}
                                    />
                                    {/* Quick Select for Vosk Models */}
                                    {modelCatalog.stt.some((m: any) => m.backend === 'vosk') && (
                                        <div className="mt-1 text-xs text-muted-foreground">
                                            Available: {modelCatalog.stt.filter((m: any) => m.backend === 'vosk').map((m: any) => (
                                                <button
                                                    key={m.id || m.path}
                                                    type="button"
                                                    className="underline mr-2 text-primary"
                                                    onClick={() => handleChange('stt_model', m.path)}
                                                >
                                                    {m.path}
                                                </button>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            </div>
                        )}

                        {/* Sherpa settings */}
                        {config.stt_backend === 'sherpa' && (
                            <>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Sherpa Model Path</label>
                                    <input
                                        type="text"
                                        className="w-full p-2 rounded border border-input bg-background"
                                        value={config.sherpa_model_path || ''}
                                        onChange={(e) => handleChange('sherpa_model_path', e.target.value)}
                                        placeholder={getModelPathPlaceholder('sherpa', 'stt')}
                                    />
                                    {modelCatalog.stt.some((m: any) => m.backend === 'sherpa') && (
                                        <div className="mt-1 text-xs text-muted-foreground">
                                            Available: {modelCatalog.stt.filter((m: any) => m.backend === 'sherpa').map((m: any) => (
                                                <button
                                                    key={m.id || m.path}
                                                    type="button"
                                                    className="underline mr-2 text-primary"
                                                    onClick={() => handleChange('sherpa_model_path', m.path)}
                                                >
                                                    {m.path}
                                                </button>
                                            ))}
                                        </div>
                                    )}
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Model Type</label>
                                    <select
                                        className="w-full p-2 rounded border border-input bg-background"
                                        value={config.sherpa_model_type || 'online'}
                                        onChange={(e) => handleChange('sherpa_model_type', e.target.value)}
                                    >
                                        <option value="online">Online (Streaming)</option>
                                        <option value="offline">Offline (VAD-gated, e.g. GigaAM)</option>
                                    </select>
                                </div>
                                {config.sherpa_model_type === 'offline' && (
                                    <div className="space-y-2">
                                        <label className="text-sm font-medium">Silero VAD Model Path</label>
                                        <input
                                            type="text"
                                            className="w-full p-2 rounded border border-input bg-background"
                                            value={config.sherpa_vad_model_path || ''}
                                            onChange={(e) => handleChange('sherpa_vad_model_path', e.target.value)}
                                            placeholder="/app/models/vad/silero_vad.onnx"
                                        />
                                        <p className="text-xs text-muted-foreground">
                                            Required for offline mode. Download from sherpa-onnx releases.
                                        </p>
                                    </div>
                                )}
                            </>
                        )}

                        {config.stt_backend === 'tone' && (
                            <>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">T-one Model Path</label>
                                    <input
                                        type="text"
                                        className="w-full p-2 rounded border border-input bg-background"
                                        value={config.tone_model_path || ''}
                                        onChange={(e) => handleChange('tone_model_path', e.target.value)}
                                        placeholder={getModelPathPlaceholder('tone', 'stt')}
                                    />
                                    {modelCatalog.stt.some((m: any) => m.backend === 'tone') && (
                                        <div className="mt-1 text-xs text-muted-foreground">
                                            Available: {modelCatalog.stt.filter((m: any) => m.backend === 'tone' && !String(m.path || '').endsWith('kenlm.bin')).map((m: any) => (
                                                <button
                                                    key={m.id || m.path}
                                                    type="button"
                                                    className="underline mr-2 text-primary"
                                                    onClick={() => handleChange('tone_model_path', m.path)}
                                                >
                                                    {m.path}
                                                </button>
                                            ))}
                                        </div>
                                    )}
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Decoder</label>
                                    <select
                                        className="w-full p-2 rounded border border-input bg-background"
                                        value={config.tone_decoder_type || 'beam_search'}
                                        onChange={(e) => handleChange('tone_decoder_type', e.target.value)}
                                    >
                                        <option value="beam_search">Beam Search</option>
                                        <option value="greedy">Greedy</option>
                                    </select>
                                </div>
                                {(config.tone_decoder_type || 'beam_search') === 'beam_search' && (
                                    <div className="space-y-2">
                                        <label className="text-sm font-medium">KenLM Path</label>
                                        <input
                                            type="text"
                                            className="w-full p-2 rounded border border-input bg-background"
                                            value={config.tone_kenlm_path || ''}
                                            onChange={(e) => handleChange('tone_kenlm_path', e.target.value)}
                                            placeholder="/app/models/stt/t-one/kenlm.bin"
                                        />
                                    </div>
                                )}
                            </>
                        )}

                        {config.stt_backend === 'faster_whisper' && (
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Language</label>
                                <input
                                    type="text"
                                    className="w-full p-2 rounded border border-input bg-background"
                                    value={config.faster_whisper_language || 'en'}
                                    onChange={(e) => handleChange('faster_whisper_language', e.target.value)}
                                    placeholder="en"
                                />
                                <p className="text-xs text-muted-foreground">
                                    ISO 639-1 code (e.g. en, ru, es). Leave as &quot;en&quot; for English.
                                </p>
                            </div>
                        )}

                        {config.stt_backend === 'whisper_cpp' && (
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Language</label>
                                <input
                                    type="text"
                                    className="w-full p-2 rounded border border-input bg-background"
                                    value={config.whisper_cpp_language || 'en'}
                                    onChange={(e) => handleChange('whisper_cpp_language', e.target.value)}
                                    placeholder="en"
                                />
                                <p className="text-xs text-muted-foreground">
                                    ISO 639-1 code (e.g. en, ru, es). Leave as &quot;en&quot; for English.
                                </p>
                            </div>
                        )}

                        {/* Kroko settings - Cloud or Local */}
                        {config.stt_backend === 'kroko' && (
                            <>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Kroko Model</label>
                                    <div className="relative">
                                        <input
                                            type="text"
                                            className="w-full p-2 rounded border border-input bg-background"
                                            value={config.stt_model || ''}
                                            onChange={(e) => handleChange('stt_model', e.target.value)}
                                            placeholder="Leave empty for Cloud, or enter path for Embedded"
                                        />
                                        {/* Quick Select for Kroko Models */}
                                        {modelCatalog.stt.some((m: any) => m.backend === 'kroko') && (
                                            <div className="mt-1 text-xs text-muted-foreground">
                                                Available: {modelCatalog.stt
                                                    .filter((m: any) => m.backend === 'kroko')
                                                    .map((m: any) => (
                                                        <button
                                                            key={m.id}
                                                            type="button"
                                                            className="underline mr-2 text-primary"
                                                            onClick={() => {
                                                                if (m.id === 'kroko_cloud') {
                                                                    handleChange('stt_model', '');
                                                                    handleChange('kroko_url', m.path);
                                                                } else {
                                                                    handleChange('stt_model', m.path);
                                                                }
                                                            }}
                                                        >
                                                            {m.name}
                                                        </button>
                                                    ))}
                                            </div>
                                        )}
                                    </div>
                                </div>

                                {/* Only show URL/Key if NO local model path is set (Cloud Mode) */}
                                {!config.stt_model && (
                                    <>
                                        <div className="space-y-2">
                                            <label className="text-sm font-medium">Kroko URL</label>
                                            <input
                                                type="text"
                                                className="w-full p-2 rounded border border-input bg-background"
                                                value={config.kroko_url || 'wss://app.kroko.ai/api/v1/transcripts/streaming'}
                                                onChange={(e) => handleChange('kroko_url', e.target.value)}
                                            />
                                        </div>
                                        <div className="space-y-2">
                                            <label className="text-sm font-medium">Kroko API Key</label>
                                            <input
                                                type="password"
                                                className="w-full p-2 rounded border border-input bg-background"
                                                value={config.kroko_api_key || ''}
                                                onChange={(e) => handleChange('kroko_api_key', e.target.value)}
                                                placeholder="Your Kroko API key"
                                            />
                                        </div>
                                    </>
                                )}

                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Language</label>
                                    <select
                                        className="w-full p-2 rounded border border-input bg-background"
                                        value={config.kroko_language || 'en-US'}
                                        onChange={(e) => handleChange('kroko_language', e.target.value)}
                                    >
                                        <option value="en-US">English (US)</option>
                                        <option value="en-GB">English (UK)</option>
                                        <option value="es-ES">Spanish</option>
                                        <option value="fr-FR">French</option>
                                        <option value="de-DE">German</option>
                                    </select>
                                </div>
                            </>
                        )}
                    </div>
                </div>
            )}

            {/* TTS Backend Settings */}
            {isTTS && (
                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">TTS (Text-to-Speech)</h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">TTS Backend</label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.tts_backend || 'piper'}
                                onChange={(e) => handleChange('tts_backend', e.target.value)}
                            >
                                {/* Dynamic options based on available backends */}
                                {!loading && Object.keys(rawModelData.tts).map(backend => (
                                    <option key={backend} value={backend}>
                                        {backend.charAt(0).toUpperCase() + backend.slice(1)}
                                    </option>
                                ))}

                                {/* Fallback options */}
                                {!loading && Object.keys(rawModelData.tts).length === 0 && (
                                    <>
                                        <option value="piper">Piper (Local)</option>
                                        <option value="kokoro">Kokoro (Local, Premium)</option>
                                    </>
                                )}
                            </select>
                        </div>

                        {/* Piper settings */}
                        {(config.tts_backend || 'piper') === 'piper' && (
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Piper Voice Path</label>
                                <input
                                    type="text"
                                    className="w-full p-2 rounded border border-input bg-background"
                                    value={config.tts_voice || ''}
                                    onChange={(e) => handleChange('tts_voice', e.target.value)}
                                    placeholder={getModelPathPlaceholder('piper', 'tts')}
                                />
                                {modelCatalog.tts.some((m: any) => m.backend === 'piper') && (
                                    <div className="mt-1 text-xs text-muted-foreground flex flex-wrap gap-2">
                                        <span>Use:</span>
                                        {modelCatalog.tts.filter((m: any) => m.backend === 'piper').map((m: any) => (
                                            <button
                                                key={m.id}
                                                type="button"
                                                className="underline text-primary"
                                                onClick={() => handleChange('tts_voice', m.path)}
                                                title={m.name}
                                            >
                                                {m.name}
                                            </button>
                                        ))}
                                    </div>
                                )}
                            </div>
                        )}

                        {/* Kokoro settings */}
                        {config.tts_backend === 'kokoro' && (
                            <>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Voice</label>
                                    <select
                                        className="w-full p-2 rounded border border-input bg-background"
                                        value={config.kokoro_voice || 'af_heart'}
                                        onChange={(e) => handleChange('kokoro_voice', e.target.value)}
                                    >
                                        {/* Use backend voice files list if available, else fallback */}
                                        {modelCatalog.tts.find((m: any) => m.id === 'kokoro_82m')?.voice_files
                                            ? Object.keys(modelCatalog.tts.find((m: any) => m.id === 'kokoro_82m').voice_files).map((v: string) => (
                                                <option key={v} value={v}>{v}</option>
                                            ))
                                            : (
                                                <>
                                                    <option value="af_heart">Heart (Female, American)</option>
                                                    <option value="af_bella">Bella (Female, American)</option>
                                                    <option value="af_nicole">Nicole (Female, American)</option>
                                                    <option value="af_sarah">Sarah (Female, American)</option>
                                                    <option value="af_sky">Sky (Female, American)</option>
                                                    <option value="am_adam">Adam (Male, American)</option>
                                                    <option value="am_michael">Michael (Male, American)</option>
                                                    <option value="bf_emma">Emma (Female, British)</option>
                                                    <option value="bf_isabella">Isabella (Female, British)</option>
                                                    <option value="bm_george">George (Male, British)</option>
                                                    <option value="bm_lewis">Lewis (Male, British)</option>
                                                </>
                                            )
                                        }
                                    </select>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Model Path</label>
                                    <input
                                        type="text"
                                        className="w-full p-2 rounded border border-input bg-background"
                                        value={config.kokoro_model_path || ''}
                                        onChange={(e) => handleChange('kokoro_model_path', e.target.value)}
                                        placeholder={getModelPathPlaceholder('kokoro', 'tts')}
                                    />
                                    {modelCatalog.tts.some((m: any) => m.id === 'kokoro_82m') && (
                                        <div className="mt-1 text-xs text-muted-foreground">
                                            Available: {modelCatalog.tts.filter((m: any) => m.id === 'kokoro_82m').map((m: any) => (
                                                <button
                                                    key={m.id}
                                                    type="button"
                                                    className="underline mr-2 text-primary"
                                                    onClick={() => handleChange('kokoro_model_path', m.path)}
                                                >
                                                    {m.path}
                                                </button>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            </>
                        )}
                    </div>
                </div>
            )}

            {/* LLM Settings */}
            {isLLM && (
                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">LLM (Large Language Model)</h4>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Max Tokens</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.max_tokens || 150}
                            onChange={(e) => handleChange('max_tokens', parseInt(e.target.value))}
                        />
                        <p className="text-xs text-muted-foreground">
                            Uses local model configured via Environment variables.
                        </p>
                    </div>
                </div>
            )}

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
        </div>
    );
};

export default LocalProviderForm;
