import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Activity, CheckCircle2, Cpu, RefreshCw, Settings, Terminal, XCircle, AlertCircle, Box, Play } from 'lucide-react';
import { toast } from 'sonner';
import { ConfigCard } from './ui/ConfigCard';
import axios from 'axios';

interface HealthInfo {
    local_ai_server: {
        status: string;
        details: any;
    };
    ai_engine: {
        status: string;
        details: any;
    };
}

interface ModelInfo {
    name: string;
    path: string;
    type: string;
    backend?: string;
    size_mb?: number;
}

interface AvailableModels {
    stt: Record<string, ModelInfo[]>;
    tts: Record<string, ModelInfo[]>;
    llm: ModelInfo[];
}

interface PendingChanges {
    stt?: { backend: string; modelPath?: string; embedded?: boolean; language?: string; sherpa_model_type?: string; sherpa_vad_model_path?: string; tone_decoder_type?: string; tone_kenlm_path?: string };
    tts?: { backend: string; modelPath?: string; voice?: string; mode?: string };
    llm?: { modelPath: string };
}

interface BackendCapabilities {
    stt: {
        vosk: { available: boolean; reason: string };
        sherpa: { available: boolean; reason: string };
        kroko_embedded: { available: boolean; reason: string };
        kroko_cloud: { available: boolean; reason: string };
        tone: { available: boolean; reason: string };
        faster_whisper: { available: boolean; reason: string };
    };
    tts: {
        piper: { available: boolean; reason: string };
        kokoro: { available: boolean; reason: string };
        melotts: { available: boolean; reason: string };
    };
    llm: { available: boolean; reason: string };
}

export const HealthWidget = () => {
    const [health, setHealth] = useState<HealthInfo | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [availableModels, setAvailableModels] = useState<AvailableModels | null>(null);
    const [capabilities, setCapabilities] = useState<BackendCapabilities | null>(null);
    const [restarting, setRestarting] = useState(false);
    const [pendingChanges, setPendingChanges] = useState<PendingChanges>({});
    const [applyingChanges, setApplyingChanges] = useState(false);
    const [startingLocalAI, setStartingLocalAI] = useState(false);
    const [rebuilding, setRebuilding] = useState(false);
    const [rebuildProgress, setRebuildProgress] = useState<string>('');

    const handleStartContainer = async (containerName: string, setStarting: (v: boolean) => void) => {
        setStarting(true);
        try {
            await axios.post(`/api/system/containers/${containerName}/start`);
            // Wait a bit for container to start
            setTimeout(() => {
                setStarting(false);
            }, 5000);
        } catch (err: any) {
            console.error('Failed to start container', err);
            toast.error(`Failed to start ${containerName}`, { description: err.response?.data?.detail || err.message });
            setStarting(false);
        }
    };

    useEffect(() => {
        const fetchHealth = async () => {
            try {
                const res = await axios.get('/api/system/health');
                setHealth(res.data);
                setError(null);
            } catch (err) {
                console.error('Failed to fetch health', err);
                setError('Failed to load health status');
            } finally {
                setLoading(false);
            }
        };
        fetchHealth();
        // Refresh every 5 seconds
        const interval = setInterval(fetchHealth, 5000);
        return () => clearInterval(interval);
    }, []);

    // Fetch available models and capabilities
    useEffect(() => {
        const fetchModels = async () => {
            try {
                const res = await axios.get('/api/local-ai/models');
                setAvailableModels(res.data);
            } catch (err) {
                console.error('Failed to fetch available models', err);
            }
        };
        const fetchCapabilities = async () => {
            try {
                const res = await axios.get('/api/local-ai/capabilities');
                setCapabilities(res.data);
            } catch (err) {
                console.error('Failed to fetch capabilities', err);
            }
        };
        fetchModels();
        fetchCapabilities();
    }, []);


    // Queue a model change (doesn't apply until user confirms)
    const queueChange = (modelType: 'stt' | 'tts' | 'llm', change: any) => {
        setPendingChanges(prev => ({
            ...prev,
            [modelType]: change
        }));
    };

    // Check if there are pending changes
    const hasPendingChanges = Object.keys(pendingChanges).length > 0;

    // Get the displayed value (pending or current) - returns backend:path format for model selection
    const getDisplayedBackend = (modelType: 'stt' | 'tts') => {
        if (modelType === 'stt') {
            if (pendingChanges.stt?.backend) {
                if (pendingChanges.stt.backend === 'kroko') {
                    return pendingChanges.stt.embedded ? 'kroko_embedded' : 'kroko_cloud';
                }
                // Return backend:path format for specific model
                if (pendingChanges.stt.modelPath) {
                    return `${pendingChanges.stt.backend}:${pendingChanges.stt.modelPath}`;
                }
                return pendingChanges.stt.backend;
            }
            const currentBackend = health?.local_ai_server.details.models?.stt?.backend || health?.local_ai_server.details.stt_backend || 'vosk';
            const currentPath = health?.local_ai_server.details.models?.stt?.path;
            if (currentBackend === 'kroko') {
                // kroko.embedded is nested in status response
                const krokoEmbedded = health?.local_ai_server.details.kroko?.embedded ?? health?.local_ai_server.details.kroko_embedded;
                return krokoEmbedded ? 'kroko_embedded' : 'kroko_cloud';
            }
            // Return backend:path format to match selected model
            if (currentPath) {
                return `${currentBackend}:${currentPath}`;
            }
            return currentBackend;
        } else {
            // TTS
            if (pendingChanges.tts?.backend) {
                if (pendingChanges.tts.backend === 'kokoro') {
                    return pendingChanges.tts.mode === 'local' ? 'kokoro_local' : 'kokoro_cloud';
                }
                // Return backend:path format for specific model
                if (pendingChanges.tts.modelPath) {
                    return `${pendingChanges.tts.backend}:${pendingChanges.tts.modelPath}`;
                }
                return pendingChanges.tts.backend;
            }
            const currentBackend = health?.local_ai_server.details.models?.tts?.backend || health?.local_ai_server.details.tts_backend || 'piper';
            const currentPath = health?.local_ai_server.details.models?.tts?.path;
            if (currentBackend === 'kokoro') {
                // kokoro.mode is nested in status response
                const kokoroMode = health?.local_ai_server.details.kokoro?.mode || health?.local_ai_server.details.kokoro_mode || 'local';
                return kokoroMode === 'local' || kokoroMode === 'hf' ? 'kokoro_local' : 'kokoro_cloud';
            }
            // Return backend:path format to match selected model
            if (currentPath) {
                return `${currentBackend}:${currentPath}`;
            }
            return currentBackend;
        }
    };

    const getDisplayedLlmPath = () => {
        if (pendingChanges.llm?.modelPath) {
            return pendingChanges.llm.modelPath;
        }
        return health?.local_ai_server.details.models?.llm?.path || '';
    };

    // Apply all pending changes and restart
    const applyChanges = async () => {
        if (!hasPendingChanges) return;

        setApplyingChanges(true);
        setRestarting(true);

        try {
            // Apply each pending change (last one triggers the restart)
            const changes = Object.entries(pendingChanges);

            for (let i = 0; i < changes.length; i++) {
                const [modelType, change] = changes[i];
                const isLast = i === changes.length - 1;

                if (modelType === 'stt' || modelType === 'tts') {
                    const payload: any = {
                        model_type: modelType,
                        backend: change.backend,
                        model_path: change.modelPath,
                        voice: change.voice
                    };

                    // Add mode params if applicable
                    if (modelType === 'stt' && change.backend === 'kroko') {
                        payload.kroko_embedded = change.embedded;
                    }
                    if (modelType === 'stt' && change.backend === 'faster_whisper' && change.language) {
                        payload.faster_whisper_language = change.language;
                    }
                    if (modelType === 'stt' && change.backend === 'whisper_cpp' && change.language) {
                        payload.whisper_cpp_language = change.language;
                    }
                    if (modelType === 'stt' && change.backend === 'sherpa') {
                        if (change.sherpa_model_type) payload.sherpa_model_type = change.sherpa_model_type;
                        if (change.sherpa_vad_model_path) payload.sherpa_vad_model_path = change.sherpa_vad_model_path;
                    }
                    if (modelType === 'stt' && change.backend === 'tone') {
                        if (change.tone_decoder_type) payload.tone_decoder_type = change.tone_decoder_type;
                        if (change.tone_kenlm_path) payload.tone_kenlm_path = change.tone_kenlm_path;
                    }
                    if (modelType === 'tts' && change.backend === 'kokoro') {
                        payload.kokoro_mode = change.mode;
                    }

                    const res = await axios.post('/api/local-ai/switch', payload);

                    // Only check success on last change (which triggers restart)
                    if (isLast && !res.data.success) {
                        throw new Error(res.data.message || 'Failed to switch model');
                    }
                } else if (modelType === 'llm') {
                    const res = await axios.post('/api/local-ai/switch', {
                        model_type: 'llm',
                        backend: '',
                        model_path: change.modelPath
                    });

                    if (isLast && !res.data.success) {
                        throw new Error(res.data.message || 'Failed to switch model');
                    }
                }
            }

            // Clear pending changes
            setPendingChanges({});

            // Wait for the switch API to complete (it handles restart internally)
            // Add extra buffer time for model loading (can take up to 3 minutes)
            setTimeout(() => {
                setRestarting(false);
                setApplyingChanges(false);
            }, 30000);  // 30 seconds UI timeout (API handles the actual wait)
        } catch (err: any) {
            console.error('Failed to apply changes', err);
            toast.error(err.message || 'Failed to apply changes');
            setApplyingChanges(false);
            setRestarting(false);
        }
    };

    // Cancel pending changes
    const cancelChanges = () => {
        setPendingChanges({});
    };

    // Check if pending changes require a rebuild (Faster-Whisper or MeloTTS when not available)
    const needsRebuild = () => {
        const needsFasterWhisper = pendingChanges.stt?.backend === 'faster_whisper' && 
            capabilities && !capabilities.stt?.faster_whisper?.available;
        const needsTone = pendingChanges.stt?.backend === 'tone' &&
            capabilities && !capabilities.stt?.tone?.available;
        const needsMeloTTS = pendingChanges.tts?.backend === 'melotts' && 
            capabilities && !capabilities.tts?.melotts?.available;
        return { needsFasterWhisper, needsTone, needsMeloTTS, any: needsFasterWhisper || needsTone || needsMeloTTS };
    };

    // Rebuild and enable new backends
    const rebuildAndEnable = async () => {
        const rebuild = needsRebuild();
        if (!rebuild.any) return;

        setRebuilding(true);
        setRebuildProgress('Starting Docker rebuild... This may take 5-10 minutes.');

        try {
            const res = await axios.post('/api/local-ai/rebuild', {
                include_faster_whisper: rebuild.needsFasterWhisper,
                include_tone: rebuild.needsTone,
                include_melotts: rebuild.needsMeloTTS,
                stt_backend: pendingChanges.stt?.backend,
                stt_model: pendingChanges.stt?.modelPath || 'base',
                tts_backend: pendingChanges.tts?.backend,
                tts_voice: pendingChanges.tts?.modelPath || 'EN-US',
            });

            if (res.data.success) {
                setRebuildProgress('✅ ' + res.data.message);
                // Clear pending changes
                setPendingChanges({});
                // Wait a moment then clear progress
                setTimeout(() => {
                    setRebuilding(false);
                    setRebuildProgress('');
                }, 3000);
            } else {
                setRebuildProgress('❌ ' + res.data.message);
                setTimeout(() => {
                    setRebuilding(false);
                }, 5000);
            }
        } catch (err: any) {
            console.error('Rebuild failed', err);
            setRebuildProgress('❌ Rebuild failed: ' + (err.response?.data?.message || err.message));
            setTimeout(() => {
                setRebuilding(false);
            }, 5000);
        }
    };


    if (loading) return <div className="animate-pulse h-48 bg-muted rounded-lg mb-6"></div>;

    if (error) {
        return (
            <div className="bg-destructive/10 border border-destructive/20 text-destructive p-4 rounded-md mb-6 flex items-center">
                <AlertCircle className="w-5 h-5 mr-2" />
                {error}
            </div>
        );
    }

    if (!health) return null;

    const renderStatus = (status: string) => {
        if (status === 'connected') return <span className="text-green-500 font-medium flex items-center gap-1"><CheckCircle2 className="w-4 h-4" /> Connected</span>;
        if (status === 'degraded') return <span className="text-yellow-500 font-medium flex items-center gap-1"><Activity className="w-4 h-4" /> Degraded</span>;
        return <span className="text-red-500 font-medium flex items-center gap-1"><XCircle className="w-4 h-4" /> Error</span>;
    };

    const getModelName = (path: string) => {
        if (!path) return 'Unknown';
        const parts = path.split('/');
        return parts[parts.length - 1];
    };

    const getModelDisplay = (model: any) => {
        if (!model) return 'Not configured';
        if (model.display) return model.display;
        if (model.path) return getModelName(String(model.path));
        return 'Not configured';
    };



    return (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
            {/* Local AI Server Card */}
            <ConfigCard className="p-6">
                <div className="flex justify-between items-start mb-6">
                    <div className="flex items-center gap-4">
                        <div className="p-3 bg-blue-500/10 rounded-xl">
                            <Cpu className="w-6 h-6 text-blue-500" />
                        </div>
                        <div>
                            <h3 className="font-semibold text-lg">Local AI Server</h3>
                            <div className="mt-1">{renderStatus(health.local_ai_server.status)}</div>
                        </div>
                    </div>
                    <div className="flex gap-2">
                        <Link
                            to="/env"
                            className="p-2 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground transition-colors cursor-pointer inline-flex items-center justify-center"
                            title="Configure"
                        >
                            <Settings className="w-4 h-4" />
                        </Link>
                        <button
                            type="button"
                            onClick={async () => {
                                if (!window.confirm('Are you sure you want to restart the Local AI Server?')) return;
                                setRestarting(true);
                                try {
                                    await axios.post('/api/system/containers/local_ai_server/restart');
                                    // Poll for health
                                    setTimeout(() => setRestarting(false), 5000);
                                } catch (err) {
                                    console.error('Failed to restart', err);
                                    setRestarting(false);
                                }
                            }}
                            className="p-2 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                            title="Restart"
                        >
                            <RefreshCw className={`w-4 h-4 ${restarting ? 'animate-spin' : ''}`} />
                        </button>
                        <Link
                            to="/logs?container=local_ai_server"
                            className="p-2 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground transition-colors cursor-pointer inline-flex items-center justify-center"
                            title="View Logs"
                        >
                            <Terminal className="w-4 h-4" />
                        </Link>
                    </div>
                </div>

                {/* Start button when not connected */}
                {health.local_ai_server.status === 'error' && (
                    <div className="mt-4 p-4 bg-yellow-500/10 border border-yellow-500/30 rounded-lg">
                        <p className="text-sm text-yellow-600 dark:text-yellow-400 mb-3">
                            Local AI Server is not reachable from Admin UI. The container may still be running.
                        </p>
                        {health?.local_ai_server?.details?.error && (
                            <div className="mb-3 text-xs text-muted-foreground break-words">
                                <span className="font-mono">{String(health.local_ai_server.details.error)}</span>
                            </div>
                        )}
                        <p className="text-xs text-muted-foreground mb-3">
                            Tier 3/best-effort hosts may require custom health URLs. Set <span className="font-mono">HEALTH_CHECK_LOCAL_AI_URL</span> and <span className="font-mono">HEALTH_CHECK_AI_ENGINE_URL</span> in <span className="font-mono">.env</span> (see Env page).
                        </p>
                        <button
                            onClick={() => handleStartContainer('local_ai_server', setStartingLocalAI)}
                            disabled={startingLocalAI}
                            className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700 disabled:opacity-50 transition-colors"
                        >
                            {startingLocalAI ? (
                                <>
                                    <RefreshCw className="w-4 h-4 animate-spin" />
                                    Starting...
                                </>
                            ) : (
                                <>
                                    <Play className="w-4 h-4" />
                                    Start Local AI Server
                                </>
                            )}
                        </button>
                    </div>
                )}

                {health.local_ai_server.status === 'connected' && (
                    <div className="space-y-4">
                        {health?.local_ai_server?.warning && (
                            <div className="p-3 bg-yellow-500/10 border border-yellow-500/30 rounded-lg text-xs text-yellow-700 dark:text-yellow-300">
                                {String(health.local_ai_server.warning)}
                            </div>
                        )}
                        {/* Degraded / mock-mode banner */}
                        {(() => {
                            const degraded = !!health?.local_ai_server?.details?.config?.degraded;
                            const mockModels = !!health?.local_ai_server?.details?.config?.mock_models;
                            const startupErrors = health?.local_ai_server?.details?.config?.startup_errors || {};
                            const startupErrorEntries = Object.entries(startupErrors || {});

                            if (!degraded && !mockModels) return null;

                            return (
                                <div className="space-y-2">
                                    {mockModels && (
                                        <div className="p-3 bg-blue-500/10 border border-blue-500/30 rounded-lg text-sm text-blue-700 dark:text-blue-300 flex items-start gap-2">
                                            <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                                            <div>
                                                <div className="font-medium">Mock models enabled</div>
                                                <div className="text-xs opacity-80">
                                                    `LOCAL_AI_MOCK_MODELS=1` is set; status may not reflect real model loading.
                                                </div>
                                            </div>
                                        </div>
                                    )}

                                    {degraded && (
                                        <div className="p-3 bg-yellow-500/10 border border-yellow-500/30 rounded-lg text-sm text-yellow-700 dark:text-yellow-300">
                                            <div className="flex items-start gap-2">
                                                <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                                                <div className="flex-1">
                                                    <div className="font-medium">Degraded mode</div>
                                                    <div className="text-xs opacity-80">
                                                        Local AI Server started but some components failed to initialize.
                                                    </div>
                                                </div>
                                            </div>
                                            {startupErrorEntries.length > 0 && (
                                                <details className="mt-2">
                                                    <summary className="cursor-pointer text-xs opacity-90">
                                                        Startup errors ({startupErrorEntries.length})
                                                    </summary>
                                                    <ul className="mt-2 space-y-1 text-xs opacity-90">
                                                        {startupErrorEntries.map(([k, v]) => (
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
                            );
                        })()}

                        {/* STT Section */}
                        <div className="space-y-2">
                            <div className="flex justify-between items-center text-sm">
                                <span className="text-muted-foreground font-medium">STT</span>
                                <div className="flex items-center gap-2">
                                    {pendingChanges.stt && (
                                        <span className="px-2 py-1 rounded-md text-xs font-medium bg-yellow-500/10 text-yellow-500">
                                            Pending
                                        </span>
                                    )}
                                    <span className={`px-2 py-1 rounded-md text-xs font-medium ${health.local_ai_server.details.models?.stt?.loaded ? "bg-green-500/10 text-green-500" : "bg-yellow-500/10 text-yellow-500"}`}>
                                        {health.local_ai_server.details.models?.stt?.loaded ? "Loaded" : "Not Loaded"}
                                    </span>
                                </div>
                            </div>
                            <div className="flex gap-2">
                                <select
                                    className={`flex-1 text-xs p-2 rounded border bg-background ${pendingChanges.stt ? 'border-yellow-500' : 'border-border'}`}
                                    value={getDisplayedBackend('stt')}
                                    onChange={(e) => {
                                        const val = e.target.value;
                                        let backend = '';
                                        let modelPath = '';
                                        let embedded = false;

                                        if (val === 'kroko_embedded') {
                                            backend = 'kroko';
                                            embedded = true;
                                        } else if (val === 'kroko_cloud') {
                                            backend = 'kroko';
                                            embedded = false;
                                        } else if (val.includes(':')) {
                                            // Format: backend:path (e.g., "vosk:/app/models/stt/vosk-model-hi-0.22")
                                            const parts = val.split(':');
                                            backend = parts[0];
                                            modelPath = parts.slice(1).join(':'); // Handle paths with colons
                                        } else {
                                            backend = val;
                                        }

                                        const currentBackend = health?.local_ai_server.details.models?.stt?.backend || health?.local_ai_server.details.stt_backend;
                                        const currentPath = health?.local_ai_server.details.models?.stt?.path;
                                        const currentEmbedded = health?.local_ai_server.details.kroko?.embedded ?? health?.local_ai_server.details.kroko_embedded;

                                        // Check if changed
                                        const isBackendChanged = backend !== currentBackend;
                                        const isPathChanged = modelPath && modelPath !== currentPath;
                                        const isModeChanged = backend === 'kroko' && embedded !== currentEmbedded;

                                        if (isBackendChanged || isPathChanged || isModeChanged) {
                                            queueChange('stt', { backend, modelPath: modelPath || undefined, embedded });
                                        } else {
                                            setPendingChanges(prev => {
                                                const { stt, ...rest } = prev;
                                                return rest;
                                            });
                                        }
                                    }}
                                    disabled={applyingChanges}
                                >
                                    {availableModels?.stt && Object.entries(availableModels.stt).map(([backend, models]) => {
                                        if (backend === 'kroko') {
                                            // Only show Kroko options if available
                                            const krokoEmbeddedAvailable = capabilities?.stt?.kroko_embedded?.available;
                                            const krokoCloudAvailable = capabilities?.stt?.kroko_cloud?.available;
                                            if (!krokoEmbeddedAvailable && !krokoCloudAvailable) return null;
                                            return (
                                                <optgroup key="kroko" label="Kroko">
                                                    {krokoEmbeddedAvailable && (
                                                        <option key="kroko_embedded" value="kroko_embedded">Kroko (Embedded)</option>
                                                    )}
                                                    {krokoCloudAvailable && (
                                                        <option key="kroko_cloud" value="kroko_cloud">Kroko (Cloud API)</option>
                                                    )}
                                                </optgroup>
                                            );
                                        }
                                        if (backend === 'sherpa') {
                                            // Only show Sherpa if available and has models
                                            if (!capabilities?.stt?.sherpa?.available || models.length === 0) return null;
                                        }
                                        if (backend === 'tone') {
                                            if (models.length === 0 && !capabilities?.stt?.tone?.available) return null;
                                        }
                                        if (backend === 'faster_whisper') {
                                            // Show Faster-Whisper option (requires rebuild)
                                            return (
                                                <optgroup key="faster_whisper" label="Faster-Whisper">
                                                    <option key="faster_whisper_base" value="faster_whisper:base">Whisper Base (Recommended)</option>
                                                    <option key="faster_whisper_tiny" value="faster_whisper:tiny">Whisper Tiny (Fast)</option>
                                                    <option key="faster_whisper_small" value="faster_whisper:small">Whisper Small</option>
                                                    <option key="faster_whisper_medium" value="faster_whisper:medium">Whisper Medium</option>
                                                    <option key="faster_whisper_large" value="faster_whisper:large-v3">Whisper Large v3</option>
                                                </optgroup>
                                            );
                                        }
                                        // Show individual models in optgroup by backend (only if models exist)
                                        return models.length > 0 && (
                                            <optgroup key={backend} label={backend.charAt(0).toUpperCase() + backend.slice(1)}>
                                                {models.map((model: any) => (
                                                    <option key={model.id || model.path} value={`${backend}:${model.path}`}>
                                                        {model.name}
                                                    </option>
                                                ))}
                                            </optgroup>
                                        );
                                    })}
                                    {/* Always show Faster-Whisper option even if not in availableModels */}
                                    {!availableModels?.stt?.faster_whisper && (
                                        <optgroup key="faster_whisper" label="Faster-Whisper (Requires Rebuild)">
                                            <option key="faster_whisper_base" value="faster_whisper:base">Whisper Base (Recommended)</option>
                                            <option key="faster_whisper_tiny" value="faster_whisper:tiny">Whisper Tiny (Fast)</option>
                                            <option key="faster_whisper_small" value="faster_whisper:small">Whisper Small</option>
                                        </optgroup>
                                    )}
                                    {!availableModels?.stt?.tone && (
                                        <optgroup key="tone" label="T-one">
                                            <option key="tone_default" value="tone:/app/models/stt/t-one">
                                                T-one Russian {!capabilities?.stt?.tone?.available ? '(requires rebuild)' : ''}
                                            </option>
                                        </optgroup>
                                    )}
                                </select>
                            </div>
                            <div className="text-xs text-muted-foreground bg-muted/50 p-2 rounded border border-border/50 truncate flex justify-between">
                                <span>{getModelDisplay(health.local_ai_server.details.models?.stt)}</span>
                                <span className="opacity-75 flex gap-2">
                                    {health.local_ai_server.details.models?.stt?.language && (
                                        <span>Lang: {health.local_ai_server.details.models.stt.language}</span>
                                    )}
                                    {health.local_ai_server.details.stt_backend === 'kroko' && (
                                        <span>
                                            {(health.local_ai_server.details.kroko?.embedded ?? health.local_ai_server.details.kroko_embedded) ? `Embedded (Port ${health.local_ai_server.details.kroko?.port || health.local_ai_server.details.kroko_port || 6006})` : 'Cloud API'}
                                        </span>
                                    )}
                                </span>
                            </div>
                            {/* Language / mode quick-switch for STT backends that support it */}
                            {(() => {
                                const currentBackend = pendingChanges.stt?.backend || health.local_ai_server.details.models?.stt?.backend || health.local_ai_server.details.stt_backend || '';
                                const currentLang = health.local_ai_server.details.models?.stt?.language || 'en';
                                if (currentBackend === 'faster_whisper' || currentBackend === 'whisper_cpp') {
                                    return (
                                        <div className="flex gap-2 items-end">
                                            <div className="flex-1">
                                                <label className="text-[10px] text-muted-foreground">Language</label>
                                                <input
                                                    type="text"
                                                    className={`w-full text-xs p-1.5 rounded border bg-background ${pendingChanges.stt?.language ? 'border-yellow-500' : 'border-border'}`}
                                                    value={pendingChanges.stt?.language ?? currentLang}
                                                    onChange={(e) => {
                                                        const lang = e.target.value.trim().toLowerCase();
                                                        const backend = currentBackend;
                                                        const existing = pendingChanges.stt || { backend };
                                                        queueChange('stt', { ...existing, backend, language: lang });
                                                    }}
                                                    placeholder="en"
                                                    disabled={applyingChanges}
                                                />
                                            </div>
                                        </div>
                                    );
                                }
                                if (currentBackend === 'sherpa') {
                                    return (
                                        <div className="space-y-1.5">
                                            <div>
                                                <label className="text-[10px] text-muted-foreground">Model Type</label>
                                                <select
                                                    className={`w-full text-xs p-1.5 rounded border bg-background ${pendingChanges.stt?.sherpa_model_type ? 'border-yellow-500' : 'border-border'}`}
                                                    value={pendingChanges.stt?.sherpa_model_type ?? health.local_ai_server.details.models?.stt?.sherpa_model_type ?? 'online'}
                                                    onChange={(e) => {
                                                        const existing = pendingChanges.stt || { backend: 'sherpa' };
                                                        queueChange('stt', { ...existing, backend: 'sherpa', sherpa_model_type: e.target.value });
                                                    }}
                                                    disabled={applyingChanges}
                                                >
                                                    <option value="online">Online (Streaming)</option>
                                                    <option value="offline">Offline (VAD-gated)</option>
                                                </select>
                                            </div>
                                            {(pendingChanges.stt?.sherpa_model_type ?? health.local_ai_server.details.models?.stt?.sherpa_model_type) === 'offline' && (
                                                <div>
                                                    <label className="text-[10px] text-muted-foreground">Silero VAD Path</label>
                                                    <input
                                                        type="text"
                                                        className={`w-full text-xs p-1.5 rounded border bg-background ${pendingChanges.stt?.sherpa_vad_model_path ? 'border-yellow-500' : 'border-border'}`}
                                                        value={pendingChanges.stt?.sherpa_vad_model_path || ''}
                                                        onChange={(e) => {
                                                            const existing = pendingChanges.stt || { backend: 'sherpa' };
                                                            queueChange('stt', { ...existing, backend: 'sherpa', sherpa_vad_model_path: e.target.value });
                                                        }}
                                                        placeholder="/app/models/vad/silero_vad.onnx"
                                                        disabled={applyingChanges}
                                                    />
                                                </div>
                                            )}
                                        </div>
                                    );
                                }
                                if (currentBackend === 'tone') {
                                    return (
                                        <div className="space-y-1.5">
                                            <div>
                                                <label className="text-[10px] text-muted-foreground">Decoder</label>
                                                <select
                                                    className={`w-full text-xs p-1.5 rounded border bg-background ${pendingChanges.stt?.tone_decoder_type ? 'border-yellow-500' : 'border-border'}`}
                                                    value={pendingChanges.stt?.tone_decoder_type ?? 'beam_search'}
                                                    onChange={(e) => {
                                                        const currentPath = health.local_ai_server.details.models?.stt?.backend === 'tone'
                                                            ? health.local_ai_server.details.models?.stt?.path
                                                            : undefined;
                                                        const existing = pendingChanges.stt || { backend: 'tone', modelPath: currentPath };
                                                        queueChange('stt', { ...existing, backend: 'tone', tone_decoder_type: e.target.value });
                                                    }}
                                                    disabled={applyingChanges}
                                                >
                                                    <option value="beam_search">Beam Search</option>
                                                    <option value="greedy">Greedy</option>
                                                </select>
                                            </div>
                                            {(pendingChanges.stt?.tone_decoder_type ?? 'beam_search') === 'beam_search' && (
                                                <div>
                                                    <label className="text-[10px] text-muted-foreground">KenLM Path</label>
                                                    <input
                                                        type="text"
                                                        className={`w-full text-xs p-1.5 rounded border bg-background ${pendingChanges.stt?.tone_kenlm_path ? 'border-yellow-500' : 'border-border'}`}
                                                        value={pendingChanges.stt?.tone_kenlm_path || ''}
                                                        onChange={(e) => {
                                                            const currentPath = health.local_ai_server.details.models?.stt?.backend === 'tone'
                                                                ? health.local_ai_server.details.models?.stt?.path
                                                                : undefined;
                                                            const existing = pendingChanges.stt || { backend: 'tone', modelPath: currentPath };
                                                            queueChange('stt', { ...existing, backend: 'tone', tone_kenlm_path: e.target.value });
                                                        }}
                                                        placeholder="/app/models/stt/t-one/kenlm.bin"
                                                        disabled={applyingChanges}
                                                    />
                                                </div>
                                            )}
                                        </div>
                                    );
                                }
                                return null;
                            })()}
                            {/* Warning when Kroko embedded not available */}
                            {capabilities && !capabilities.stt?.kroko_embedded?.available && (
                                <div className="text-xs p-2 rounded bg-amber-500/10 border border-amber-500/20 text-amber-600 dark:text-amber-400 space-y-1">
                                    <div>
                                        <span className="font-medium">Kroko Embedded not available.</span>
                                        <span className="opacity-75"> First download a Kroko ONNX model from the </span>
                                        <Link to="/models" className="underline hover:text-amber-500">Models Page</Link>
                                        <span className="opacity-75">, then add to .env and rebuild:</span>
                                    </div>
                                    <code className="block bg-black/20 dark:bg-white/10 px-2 py-1 rounded text-[10px] font-mono select-all">
                                        echo "INCLUDE_KROKO_EMBEDDED=true" &gt;&gt; .env && docker compose build --no-cache local_ai_server && docker compose up -d local_ai_server
                                    </code>
                                </div>
                            )}
                            {/* Warning when Faster-Whisper selected but not available */}
                            {(pendingChanges.stt?.backend === 'faster_whisper' || getDisplayedBackend('stt').startsWith('faster_whisper')) && 
                             capabilities && !capabilities.stt?.faster_whisper?.available && (
                                <div className="text-xs p-2 rounded bg-amber-500/10 border border-amber-500/20 text-amber-600 dark:text-amber-400 space-y-1">
                                    <div>
                                        <span className="font-medium">⚠️ Faster-Whisper requires Docker rebuild.</span>
                                        <span className="opacity-75"> Models auto-download from HuggingFace on first use. Run:</span>
                                    </div>
                                    <code className="block bg-black/20 dark:bg-white/10 px-2 py-1 rounded text-[10px] font-mono select-all">
                                        docker compose build --build-arg INCLUDE_FASTER_WHISPER=true local_ai_server && docker compose up -d local_ai_server
                                    </code>
                                </div>
                            )}
                        </div>

                        {/* LLM Section */}
                        <div className="space-y-2">
                            <div className="flex justify-between items-center text-sm">
                                <span className="text-muted-foreground font-medium">LLM</span>
                                <div className="flex items-center gap-2">
                                    {pendingChanges.llm && (
                                        <span className="px-2 py-1 rounded-md text-xs font-medium bg-yellow-500/10 text-yellow-500">
                                            Pending
                                        </span>
                                    )}
                                    <span className={`px-2 py-1 rounded-md text-xs font-medium ${health.local_ai_server.details.models?.llm?.loaded ? "bg-green-500/10 text-green-500" : "bg-yellow-500/10 text-yellow-500"}`}>
                                        {health.local_ai_server.details.models?.llm?.loaded ? "Loaded" : "Not Loaded"}
                                    </span>
                                </div>
                            </div>
                            <select
                                className={`w-full text-xs p-2 rounded border bg-background ${pendingChanges.llm ? 'border-yellow-500' : 'border-border'}`}
                                value={getDisplayedLlmPath()}
                                onChange={(e) => {
                                    const modelPath = e.target.value;
                                    const currentPath = health?.local_ai_server.details.models?.llm?.path;
                                    if (modelPath !== currentPath) {
                                        queueChange('llm', { modelPath });
                                    } else {
                                        setPendingChanges(prev => {
                                            const { llm, ...rest } = prev;
                                            return rest;
                                        });
                                    }
                                }}
                                disabled={applyingChanges}
                            >
                                {availableModels?.llm?.map((model) => (
                                    <option key={model.path} value={model.path}>
                                        {model.name} {model.size_mb ? `(${model.size_mb} MB)` : ''}
                                    </option>
                                ))}
                            </select>
                            <div className="text-xs text-muted-foreground bg-muted/50 p-2 rounded border border-border/50 truncate">
                                {getModelDisplay(health.local_ai_server.details.models?.llm)}
                            </div>
                        </div>

                        {/* TTS Section */}
                        <div className="space-y-2">
                            <div className="flex justify-between items-center text-sm">
                                <span className="text-muted-foreground font-medium">TTS</span>
                                <div className="flex items-center gap-2">
                                    {pendingChanges.tts && (
                                        <span className="px-2 py-1 rounded-md text-xs font-medium bg-yellow-500/10 text-yellow-500">
                                            Pending
                                        </span>
                                    )}
                                    <span className={`px-2 py-1 rounded-md text-xs font-medium ${health.local_ai_server.details.models?.tts?.loaded ? "bg-green-500/10 text-green-500" : "bg-yellow-500/10 text-yellow-500"}`}>
                                        {health.local_ai_server.details.models?.tts?.loaded ? "Loaded" : "Not Loaded"}
                                    </span>
                                </div>
                            </div>
                            <div className="flex gap-2">
                                <select
                                    className={`flex-1 text-xs p-2 rounded border bg-background ${pendingChanges.tts ? 'border-yellow-500' : 'border-border'}`}
                                    value={getDisplayedBackend('tts')}
                                    onChange={(e) => {
                                        const val = e.target.value;
                                        let backend = '';
                                        let modelPath = '';
                                        let mode = 'local';

                                        if (val === 'kokoro_local') {
                                            backend = 'kokoro';
                                            mode = 'local';
                                        } else if (val === 'kokoro_cloud') {
                                            backend = 'kokoro';
                                            mode = 'api';
                                        } else if (val.includes(':')) {
                                            // Format: backend:path (e.g., "piper:/app/models/tts/en_US-lessac-medium.onnx")
                                            const parts = val.split(':');
                                            backend = parts[0];
                                            modelPath = parts.slice(1).join(':');
                                        } else {
                                            backend = val;
                                        }

                                        const currentBackend = health?.local_ai_server.details.models?.tts?.backend || health?.local_ai_server.details.tts_backend;
                                        const currentPath = health?.local_ai_server.details.models?.tts?.path;
                                        const currentMode = health?.local_ai_server.details.kokoro?.mode || health?.local_ai_server.details.kokoro_mode;

                                        const isBackendChanged = backend !== currentBackend;
                                        const isPathChanged = modelPath && modelPath !== currentPath;
                                        const isModeChanged = backend === 'kokoro' && mode !== currentMode;

                                        if (isBackendChanged || isPathChanged || isModeChanged) {
                                            const change: any = { backend, modelPath: modelPath || undefined };
                                            if (backend === 'kokoro') {
                                                const currentVoice = health?.local_ai_server.details.kokoro_voice || 'af_heart';
                                                change.voice = currentVoice;
                                                change.mode = mode;
                                            }
                                            queueChange('tts', change);
                                        } else {
                                            setPendingChanges(prev => {
                                                const { tts, ...rest } = prev;
                                                return rest;
                                            });
                                        }
                                    }}
                                    disabled={applyingChanges}
                                >
                                    {availableModels?.tts && Object.entries(availableModels.tts).map(([backend, models]) => {
                                        if (backend === 'kokoro') {
                                            // Only show Kokoro if available
                                            if (!capabilities?.tts?.kokoro?.available && models.length === 0) return null;
                                            const kokoroApiConfigured = !!health?.local_ai_server?.details?.kokoro?.api_key_set;
                                            return (
                                                <optgroup key="kokoro" label="Kokoro">
                                                    {(capabilities?.tts?.kokoro?.available || models.length > 0) && (
                                                        <option key="kokoro_local" value="kokoro_local">Kokoro (Local)</option>
                                                    )}
                                                    {kokoroApiConfigured && (
                                                        <option key="kokoro_cloud" value="kokoro_cloud">Kokoro (Cloud/API)</option>
                                                    )}
                                                </optgroup>
                                            );
                                        }
                                        if (backend === 'melotts') {
                                            // Show MeloTTS option (requires rebuild)
                                            return (
                                                <optgroup key="melotts" label="MeloTTS">
                                                    <option key="melotts_en_us" value="melotts:EN-US">MeloTTS American English</option>
                                                    <option key="melotts_en_br" value="melotts:EN-BR">MeloTTS British English</option>
                                                    <option key="melotts_en_au" value="melotts:EN-AU">MeloTTS Australian English</option>
                                                    <option key="melotts_en_in" value="melotts:EN-IN">MeloTTS Indian English</option>
                                                </optgroup>
                                            );
                                        }
                                        // Show individual models in optgroup by backend (only if models exist)
                                        return models.length > 0 && (
                                            <optgroup key={backend} label={backend.charAt(0).toUpperCase() + backend.slice(1)}>
                                                {models.map((model: any) => (
                                                    <option key={model.id || model.path} value={`${backend}:${model.path}`}>
                                                        {model.name}
                                                    </option>
                                                ))}
                                            </optgroup>
                                        );
                                    })}
                                    {/* Always show MeloTTS option even if not in availableModels */}
                                    {!availableModels?.tts?.melotts && (
                                        <optgroup key="melotts" label="MeloTTS (Requires Rebuild)">
                                            <option key="melotts_en_us" value="melotts:EN-US">MeloTTS American English</option>
                                            <option key="melotts_en_br" value="melotts:EN-BR">MeloTTS British English</option>
                                            <option key="melotts_en_au" value="melotts:EN-AU">MeloTTS Australian</option>
                                        </optgroup>
                                    )}
                                </select>
                            </div>
                            <div className="text-xs text-muted-foreground bg-muted/50 p-2 rounded border border-border/50 truncate flex justify-between">
                                <span>{getModelDisplay(health.local_ai_server.details.models?.tts)}</span>
                                {health.local_ai_server.details.tts_backend === 'kokoro' && health.local_ai_server.details.kokoro_voice && (
                                    <span className="opacity-75">Voice: {health.local_ai_server.details.kokoro_voice}</span>
                                )}
                            </div>
                            {!!capabilities?.tts?.kokoro?.available && !health?.local_ai_server?.details?.kokoro?.api_key_set && (
                                <div className="text-xs p-2 rounded bg-muted/30 border border-border/50 text-muted-foreground">
                                    Enable Kokoro Cloud/API by setting `KOKORO_API_KEY` (and `KOKORO_MODE=api`) in <Link to="/env" className="underline">Env</Link>.
                                </div>
                            )}
                            {health?.local_ai_server?.details?.models?.tts?.backend === 'kokoro' &&
                                !health?.local_ai_server?.details?.kokoro?.api_key_set &&
                                (health?.local_ai_server?.details?.kokoro?.mode || health?.local_ai_server?.details?.kokoro_mode) !== 'local' && (
                                    <div className="text-xs p-2 rounded bg-amber-500/10 border border-amber-500/20 text-amber-600 dark:text-amber-400">
                                        Kokoro Cloud/API requires `KOKORO_API_KEY`. Configure it in <Link to="/env" className="underline">Env</Link>.
                                    </div>
                                )}
                            {/* Warning when MeloTTS selected but not available */}
                            {(pendingChanges.tts?.backend === 'melotts' || getDisplayedBackend('tts').startsWith('melotts')) && 
                             capabilities && !capabilities.tts?.melotts?.available && (
                                <div className="text-xs p-2 rounded bg-amber-500/10 border border-amber-500/20 text-amber-600 dark:text-amber-400 space-y-1">
                                    <div>
                                        <span className="font-medium">⚠️ MeloTTS requires Docker rebuild.</span>
                                        <span className="opacity-75"> Lightweight CPU-optimized TTS. Run:</span>
                                    </div>
                                    <code className="block bg-black/20 dark:bg-white/10 px-2 py-1 rounded text-[10px] font-mono select-all">
                                        docker compose build --build-arg INCLUDE_MELOTTS=true local_ai_server && docker compose up -d local_ai_server
                                    </code>
                                </div>
                            )}
                        </div>

                        {/* Apply Changes Banner */}
                        {(hasPendingChanges || restarting || rebuilding) && (
                            <div className={`border rounded-lg p-3 space-y-2 ${
                                rebuilding ? 'bg-purple-500/10 border-purple-500/30' :
                                restarting ? 'bg-blue-500/10 border-blue-500/30' : 
                                'bg-yellow-500/10 border-yellow-500/30'
                            }`}>
                                <div className={`flex items-center gap-2 text-sm font-medium ${
                                    rebuilding ? 'text-purple-600 dark:text-purple-400' :
                                    restarting ? 'text-blue-600 dark:text-blue-400' : 
                                    'text-yellow-600 dark:text-yellow-400'
                                }`}>
                                    {rebuilding ? (
                                        <>
                                            <RefreshCw className="w-4 h-4 animate-spin" />
                                            {rebuildProgress || 'Rebuilding Docker image...'}
                                        </>
                                    ) : restarting ? (
                                        <>
                                            <RefreshCw className="w-4 h-4 animate-spin" />
                                            Restarting Local AI Server...
                                        </>
                                    ) : (
                                        <>
                                            <AlertCircle className="w-4 h-4" />
                                            {Object.keys(pendingChanges).length} change(s) pending
                                            {needsRebuild().any && <span className="text-amber-500 ml-1">(requires rebuild)</span>}
                                        </>
                                    )}
                                </div>
                                {!restarting && !rebuilding && (
                                    <div className="flex gap-2">
                                        {needsRebuild().any ? (
                                            <button
                                                onClick={rebuildAndEnable}
                                                disabled={applyingChanges}
                                                className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-purple-600 text-white rounded text-sm font-medium hover:bg-purple-700 disabled:opacity-50 transition-colors"
                                            >
                                                <Box className="w-4 h-4" />
                                                Rebuild & Enable (~5-10 min)
                                            </button>
                                        ) : (
                                            <button
                                                onClick={applyChanges}
                                                disabled={applyingChanges}
                                                className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-green-600 text-white rounded text-sm font-medium hover:bg-green-700 disabled:opacity-50 transition-colors"
                                            >
                                                {applyingChanges ? (
                                                    <>
                                                        <RefreshCw className="w-4 h-4 animate-spin" />
                                                        Applying...
                                                    </>
                                                ) : (
                                                    <>
                                                        <CheckCircle2 className="w-4 h-4" />
                                                        Apply & Restart
                                                    </>
                                                )}
                                            </button>
                                        )}
                                        <button
                                            onClick={cancelChanges}
                                            disabled={applyingChanges || rebuilding}
                                            className="flex items-center gap-1 px-3 py-2 bg-muted text-muted-foreground rounded text-sm font-medium hover:bg-muted/80 disabled:opacity-50 transition-colors"
                                        >
                                            <XCircle className="w-4 h-4" />
                                            Cancel
                                        </button>
                                    </div>
                                )}
                                {restarting && (
                                    <div className="text-xs text-muted-foreground">
                                        Please wait, this may take 10-15 seconds...
                                    </div>
                                )}
                                {rebuilding && (
                                    <div className="text-xs text-muted-foreground">
                                        ⚠️ Do not close this page. Building Docker image with new packages...
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                )}
            </ConfigCard>

        </div>
    );
};
