import { useState, useEffect } from 'react';
import { HardDrive, Download, Trash2, RefreshCw, CheckCircle2, XCircle, Loader2, Mic, Volume2, Brain, AlertTriangle, Cpu, Terminal, Settings, Play, Wrench } from 'lucide-react';
import { Link } from 'react-router-dom';
import { ConfigCard } from '../../components/ui/ConfigCard';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import { RebuildBackendDialog } from '../../components/models/RebuildBackendDialog';
import axios from 'axios';

interface ModelInfo {
    id: string;
    name: string;
    description?: string;
    language?: string;
    region?: string;
    backend?: string;
    size_mb: number;
    size_display: string;
    model_path?: string;
    download_url?: string;
    config_url?: string;  // For TTS models that need JSON config
    voice_files?: Record<string, string>;  // For Kokoro TTS voice files
    installed?: boolean;
    quality?: string;
    gender?: string;
    auto_download?: boolean;  // Models that auto-download from HuggingFace on first use
    note?: string;  // Info note about the model
    recommended?: boolean;
    system_recommended?: boolean;
    recommended_ram_gb?: number;
    tool_calling?: 'recommended' | 'experimental' | 'none' | string;
    tool_calling_note?: string;
    chat_format?: string;
}

interface InstalledModel {
    name: string;
    path: string;
    size_mb: number;
    type: 'stt' | 'tts' | 'llm';
}

interface Toast {
    id: number;
    message: string;
    type: 'success' | 'error' | 'warning';
}

interface DownloadProgress {
    bytes_downloaded: number;
    total_bytes: number;
    percent: number;
    speed_bps: number;
    eta_seconds: number | null;
    current_file: string;
}

interface ActiveModels {
    stt: { backend: string; path: string; loaded: boolean; display?: string; language?: string | null; sherpa_model_type?: string | null; tone_decoder_type?: string | null };
    tts: { backend: string; path: string; loaded: boolean; display?: string };
    llm: {
        path: string;
        loaded: boolean;
        display?: string;
        config?: {
            context?: number;
            batch?: number;
            threads?: number;
            max_tokens?: number;
            gpu_layers?: number | null;
        };
        prompt_fit?: {
            system_prompt_chars?: number;
            system_prompt_tokens?: number | null;
            safe_max_tokens?: number | null;
        };
        auto_context?: {
            enabled?: boolean;
            source?: string;
            selected_context?: number;
            candidates?: number[];
        };
        tool_capability?: {
            level?: string;
            source?: string;
            model?: string;
            notes?: string;
        };
    };
}

interface AvailableModels {
    stt: Record<string, { name: string; path: string }[]>;
    tts: Record<string, { name: string; path: string }[]>;
    llm: { name: string; path: string }[];
}

interface BackendCapabilities {
    stt?: {
        tone?: { available: boolean; reason?: string };
        faster_whisper?: { available: boolean; reason?: string };
        kroko_embedded?: { available: boolean; reason?: string };
        whisper_cpp?: { available: boolean; reason?: string };
    };
    tts?: {
        melotts?: { available: boolean; reason?: string };
    };
}

interface CompatibilityIssue {
    key: string;
    message: string;
    requiresRebuild: boolean;
}

interface RuntimeGpuStatus {
    host_preflight_detected?: boolean | null;
    host_preflight_raw?: string | null;
    runtime_detected?: boolean;
    runtime_usable?: boolean;
    source?: string;
    name?: string | null;
    memory_gb?: number | null;
    error?: string | null;
    checked_at_epoch_ms?: number | null;
}

interface ApplyProgressState {
    phase: 'preparing' | 'rebuilding' | 'switching' | 'restarting' | 'verifying' | 'done' | 'error';
    percent: number;
    message: string;
    startedAt: number;
    elapsedSeconds: number;
    details: string[];
}

const ModelsPage = () => {
    const { confirm } = useConfirmDialog();
    const [catalog, setCatalog] = useState<{ stt: ModelInfo[]; tts: ModelInfo[]; llm: ModelInfo[] }>({ stt: [], tts: [], llm: [] });
    const [installedModels, setInstalledModels] = useState<InstalledModel[]>([]);
    const [languageNames, setLanguageNames] = useState<Record<string, string>>({});
    const [regionNames, setRegionNames] = useState<Record<string, string>>({});
    const [loading, setLoading] = useState(true);
    const [downloadingModel, setDownloadingModel] = useState<string | null>(null);
    const [downloadProgress, setDownloadProgress] = useState<DownloadProgress | null>(null);
    const [deletingModel, setDeletingModel] = useState<string | null>(null);
    const [selectedTab, setSelectedTab] = useState<'installed' | 'stt' | 'tts' | 'llm'>('installed');
    const [selectedRegion, setSelectedRegion] = useState<string>('all');
    const [toasts, setToasts] = useState<Toast[]>([]);

    // Active models state (from Local AI Server)
    const [activeModels, setActiveModels] = useState<ActiveModels | null>(null);
    const [availableModels, setAvailableModels] = useState<AvailableModels | null>(null);
    const [serverStatus, setServerStatus] = useState<'connected' | 'error' | 'loading'>('loading');
    const [restarting, setRestarting] = useState(false);
    const [pendingChanges, setPendingChanges] = useState<{ stt?: string; tts?: string; llm?: string }>({});
    const [pendingSttExtra, setPendingSttExtra] = useState<{ language?: string; sherpa_model_type?: string; sherpa_vad_model_path?: string; tone_decoder_type?: string; tone_kenlm_path?: string }>({});
    const [pendingLlmConfig, setPendingLlmConfig] = useState<{ context?: number; max_tokens?: number }>({});
    const [startingServer, setStartingServer] = useState(false);
    const [capabilities, setCapabilities] = useState<BackendCapabilities | null>(null);
    const [envConfig, setEnvConfig] = useState<Record<string, string>>({});
    const [forceIncompatibleApply, setForceIncompatibleApply] = useState(false);
    const [runtimeGpu, setRuntimeGpu] = useState<RuntimeGpuStatus | null>(null);
    const [applyProgress, setApplyProgress] = useState<ApplyProgressState | null>(null);

    // Rebuild dialog state
    const [rebuildDialog, setRebuildDialog] = useState<{
        isOpen: boolean;
        backend: string;
        backendDisplayName: string;
        estimatedSeconds: number;
    }>({ isOpen: false, backend: '', backendDisplayName: '', estimatedSeconds: 180 });

    const openRebuildDialog = (backend: string, displayName: string, estimatedSeconds: number = 180) => {
        setRebuildDialog({
            isOpen: true,
            backend,
            backendDisplayName: displayName,
            estimatedSeconds,
        });
    };

    const closeRebuildDialog = () => {
        setRebuildDialog(prev => ({ ...prev, isOpen: false }));
    };

    const handleRebuildComplete = (success: boolean) => {
        if (success) {
            showToast('Backend enabled successfully! Refresh models to see changes.', 'success');
            fetchModels();
            fetchActiveModels();
        }
    };

    const showToast = (message: string, type: 'success' | 'error' | 'warning') => {
        const id = Date.now();
        setToasts(prev => [...prev, { id, message, type }]);
        setTimeout(() => {
            setToasts(prev => prev.filter(t => t.id !== id));
        }, 4000);
    };

    const fetchModels = async () => {
        setLoading(true);
        try {
            // Fetch catalog
            const catalogRes = await axios.get('/api/wizard/local/available-models');
            if (catalogRes.data) {
                setCatalog(catalogRes.data.catalog);
                setLanguageNames(catalogRes.data.language_names || {});
                setRegionNames(catalogRes.data.region_names || {});
            }

            // Fetch installed models from local-ai-server
            const installedRes = await axios.get('/api/local-ai/models');
            if (installedRes.data) {
                // Flatten the nested response into a single array
                const models: InstalledModel[] = [];

                // Process STT models (grouped by backend)
                if (installedRes.data.stt) {
                    Object.entries(installedRes.data.stt).forEach(([_, backendModels]: [string, any]) => {
                        if (Array.isArray(backendModels)) {
                            backendModels.forEach((m: any) => {
                                models.push({
                                    name: m.name,
                                    path: m.path,
                                    size_mb: m.size_mb || 0,
                                    type: 'stt'
                                });
                            });
                        }
                    });
                }

                // Process TTS models (grouped by backend)
                if (installedRes.data.tts) {
                    Object.entries(installedRes.data.tts).forEach(([_, backendModels]: [string, any]) => {
                        if (Array.isArray(backendModels)) {
                            backendModels.forEach((m: any) => {
                                models.push({
                                    name: m.name,
                                    path: m.path,
                                    size_mb: m.size_mb || 0,
                                    type: 'tts'
                                });
                            });
                        }
                    });
                }

                // Process LLM models (flat array)
                if (Array.isArray(installedRes.data.llm)) {
                    installedRes.data.llm.forEach((m: any) => {
                        models.push({
                            name: m.name,
                            path: m.path,
                            size_mb: m.size_mb || 0,
                            type: 'llm'
                        });
                    });
                }

                setInstalledModels(models);
            }
        } catch (err) {
            console.error('Failed to fetch models', err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchModels();
        fetchActiveModels();
    }, []);

    // Resume download on mount if active
    useEffect(() => {
        let mounted = true;
        const checkActiveDownload = async () => {
            try {
                const res = await axios.get('/api/wizard/local/download-progress');
                if (res.data && res.data.running && mounted) {
                    setDownloadingModel(res.data.job_id || 'unknown');
                    setDownloadProgress({
                        bytes_downloaded: res.data.bytes_downloaded || 0,
                        total_bytes: res.data.total_bytes || 0,
                        percent: res.data.percent || 0,
                        speed_bps: res.data.speed_bps || 0,
                        eta_seconds: res.data.eta_seconds,
                        current_file: res.data.current_file || ''
                    });

                    const pollDownload = async () => {
                        if (!mounted) return;
                        try {
                            const pRes = await axios.get('/api/wizard/local/download-progress');
                            if (pRes.data.running) {
                                setDownloadProgress({
                                    bytes_downloaded: pRes.data.bytes_downloaded || 0,
                                    total_bytes: pRes.data.total_bytes || 0,
                                    percent: pRes.data.percent || 0,
                                    speed_bps: pRes.data.speed_bps || 0,
                                    eta_seconds: pRes.data.eta_seconds,
                                    current_file: pRes.data.current_file || ''
                                });
                            }

                            if (pRes.data.completed) {
                                showToast(`Model downloaded successfully!`, 'success');
                                setDownloadingModel(null);
                                setDownloadProgress(null);
                                fetchModels();
                            } else if (pRes.data.error) {
                                showToast(`Download failed: ${pRes.data.error}`, 'error');
                                setDownloadingModel(null);
                                setDownloadProgress(null);
                            } else if (pRes.data.running) {
                                window.setTimeout(pollDownload, 1000);
                            } else {
                                setDownloadingModel(null);
                                setDownloadProgress(null);
                            }
                        } catch {
                            window.setTimeout(pollDownload, 2000);
                        }
                    };
                    pollDownload();
                }
            } catch {
                // Ignore errors on mount check
            }
        };
        checkActiveDownload();
        return () => { mounted = false; };
    }, []);

    // Fetch active models from Local AI Server health
    const fetchActiveModels = async () => {
        const [healthRes, modelsRes, capabilitiesRes, envRes] = await Promise.allSettled([
            axios.get('/api/system/health'),
            axios.get('/api/local-ai/models'),
            axios.get('/api/local-ai/capabilities'),
            axios.get('/api/config/env')
        ]);

        if (healthRes.status === 'fulfilled') {
            const localAI = healthRes.value.data?.local_ai_server;
            if (localAI?.status === 'connected') {
                setServerStatus('connected');
                setRuntimeGpu((localAI.details?.gpu || null) as RuntimeGpuStatus | null);
                setActiveModels({
                    stt: {
                        backend: localAI.details?.models?.stt?.backend || 'unknown',
                        path: localAI.details?.models?.stt?.path || '',
                        loaded: localAI.details?.models?.stt?.loaded || false,
                        display: localAI.details?.models?.stt?.display || '',
                        language: localAI.details?.models?.stt?.language || null,
                        sherpa_model_type: localAI.details?.models?.stt?.sherpa_model_type || null,
                        tone_decoder_type: localAI.details?.models?.stt?.tone_decoder_type || null,
                    },
                    tts: {
                        backend: localAI.details?.models?.tts?.backend || 'unknown',
                        path: localAI.details?.models?.tts?.path || '',
                        loaded: localAI.details?.models?.tts?.loaded || false,
                        display: localAI.details?.models?.tts?.display || ''
                    },
                    llm: {
                        path: localAI.details?.models?.llm?.path || '',
                        loaded: localAI.details?.models?.llm?.loaded || false,
                        display: localAI.details?.models?.llm?.display || '',
                        config: localAI.details?.models?.llm?.config || {},
                        prompt_fit: localAI.details?.models?.llm?.prompt_fit || {},
                        auto_context: localAI.details?.models?.llm?.auto_context || {},
                        tool_capability: localAI.details?.models?.llm?.tool_capability || {}
                    }
                });
            } else {
                setServerStatus('error');
                setRuntimeGpu(null);
            }
        } else {
            setServerStatus('error');
            setRuntimeGpu(null);
        }

        if (modelsRes.status === 'fulfilled' && modelsRes.value.data) {
            setAvailableModels(modelsRes.value.data);
        }
        if (capabilitiesRes.status === 'fulfilled' && capabilitiesRes.value.data) {
            setCapabilities(capabilitiesRes.value.data);
        }
        if (envRes.status === 'fulfilled' && envRes.value.data) {
            setEnvConfig(envRes.value.data || {});
        }
    };

    // Handle model switch
    const handleModelSwitch = async (
        modelType: 'stt' | 'tts' | 'llm',
        backend: string,
        modelPath: string,
        forceIncompatibleApplyRequest = false,
        extra?: Record<string, any>
    ) => {
        const payload: any = {
            model_type: modelType,
            backend: backend,
            model_path: modelPath,
            force_incompatible_apply: forceIncompatibleApplyRequest
        };
        if (extra) {
            Object.assign(payload, extra);
        }
        return axios.post('/api/local-ai/switch', payload);
    };

    // Get model name from path
    const getModelName = (path: string) => {
        if (!path) return 'None';
        const parts = path.split('/');
        return parts[parts.length - 1] || path;
    };

    const resolveToolPolicy = () => {
        const configured = String(envConfig['LOCAL_TOOL_CALL_POLICY'] || 'auto').trim().toLowerCase();
        if (configured && configured !== 'auto') return configured;
        const level = String(activeModels?.llm?.tool_capability?.level || '').trim().toLowerCase();
        if (level === 'strict') return 'strict';
        if (level === 'none') return 'off';
        return 'compatible';
    };

    const handleDownload = async (model: ModelInfo, type: 'stt' | 'tts' | 'llm') => {
        if (!model.download_url) {
            showToast('This model requires an API key and cannot be downloaded', 'error');
            return;
        }

        setDownloadingModel(model.id);
        setDownloadProgress(null);
        try {
            const startRes = await axios.post('/api/wizard/local/download-model', {
                model_id: model.id,
                type: type,
                download_url: model.download_url,
                model_path: model.model_path,
                config_url: model.config_url,  // For TTS models (Piper JSON config)
                voice_files: model.voice_files  // For Kokoro TTS voice files
            });
            const jobId = startRes.data?.job_id;
            const diskWarning = startRes.data?.disk_warning;
            if (diskWarning) showToast(diskWarning, 'warning');
            showToast(`Started downloading ${model.name}`, 'success');
            // Poll for completion with progress updates
            const pollDownload = async () => {
                try {
                    const res = await axios.get('/api/wizard/local/download-progress', {
                        params: jobId ? { job_id: jobId } : undefined
                    });
                    // Update progress state - always set if running to show progress bar
                    if (res.data.running) {
                        setDownloadProgress({
                            bytes_downloaded: res.data.bytes_downloaded || 0,
                            total_bytes: res.data.total_bytes || 0,
                            percent: res.data.percent || 0,
                            speed_bps: res.data.speed_bps || 0,
                            eta_seconds: res.data.eta_seconds,
                            current_file: res.data.current_file || ''
                        });
                    }

                    if (res.data.completed) {
                        showToast(`${model.name} downloaded successfully!`, 'success');
                        setDownloadingModel(null);
                        setDownloadProgress(null);
                        fetchModels();
                    } else if (res.data.error) {
                        showToast(`Download failed: ${res.data.error}`, 'error');
                        setDownloadingModel(null);
                        setDownloadProgress(null);
                    } else if (res.data.running) {
                        setTimeout(pollDownload, 1000);
                    } else {
                        setDownloadingModel(null);
                        setDownloadProgress(null);
                    }
                } catch (err) {
                    setTimeout(pollDownload, 2000);
                }
            };
            setTimeout(pollDownload, 500);
        } catch (err: any) {
            const message = err.response?.data?.detail || err.response?.data?.message || err.message || 'Unknown error';
            showToast(`Failed to start download: ${message}`, 'error');
            setDownloadingModel(null);
            setDownloadProgress(null);
        }
    };

    const handleDelete = async (model: InstalledModel) => {
        const confirmed = await confirm({
            title: 'Delete Model?',
            description: `Are you sure you want to delete "${model.name}"? This cannot be undone.`,
            confirmText: 'Delete',
            variant: 'destructive'
        });
        if (!confirmed) return;

        setDeletingModel(model.name);
        try {
            await axios.delete('/api/local-ai/models', {
                data: { model_path: model.path, type: model.type }
            });
            showToast(`${model.name} deleted successfully`, 'success');
            fetchModels();
        } catch (err: any) {
            const message = err.response?.data?.detail || err.message || 'Unknown error';
            showToast(`Failed to delete model: ${message}`, 'error');
        } finally {
            setDeletingModel(null);
        }
    };

    const getTypeIcon = (type: string) => {
        switch (type) {
            case 'stt': return <Mic className="w-4 h-4" />;
            case 'tts': return <Volume2 className="w-4 h-4" />;
            case 'llm': return <Brain className="w-4 h-4" />;
            default: return <HardDrive className="w-4 h-4" />;
        }
    };

    const filterByRegion = (models: ModelInfo[]) => {
        if (selectedRegion === 'all') return models;
        return models.filter(m => m.region === selectedRegion);
    };

    const getUniqueRegions = () => {
        const regions = new Set<string>();
        [...catalog.stt, ...catalog.tts].forEach(m => {
            if (m.region) regions.add(m.region);
        });
        return Array.from(regions);
    };

    const normalizeModelKey = (value: string) => {
        const raw = (value || '').trim();
        if (!raw) return '';
        const parts = raw.split('/').filter(Boolean);
        return (parts[parts.length - 1] || raw).trim();
    };

    const isModelInstalled = (modelPath: string) => {
        const key = normalizeModelKey(modelPath);
        if (!key) return false;
        return installedModels.some(m => normalizeModelKey(m.path) === key);
    };

    // Get friendly display name for installed model by matching against catalog
    const getModelDisplayName = (model: InstalledModel): string => {
        const allCatalogModels = [...catalog.stt, ...catalog.tts, ...catalog.llm];
        const installedKey = normalizeModelKey(model.path);
        const catalogMatch = allCatalogModels.find(cm =>
            cm.model_path && normalizeModelKey(cm.model_path) === installedKey
        );
        return catalogMatch?.name || model.name;
    };

    const isTruthy = (value: string | undefined | null): boolean => {
        const raw = (value || '').trim().toLowerCase();
        return ['1', 'true', 'yes', 'on'].includes(raw);
    };

    const parseSelection = (value: string | undefined): { backend: string; modelPath: string } => {
        if (!value) return { backend: '', modelPath: '' };
        const [backend, ...pathParts] = value.split(':');
        return { backend, modelPath: pathParts.join(':') };
    };

    const gpuDetected = isTruthy(envConfig.GPU_AVAILABLE);
    const fasterWhisperDevice = (envConfig.FASTER_WHISPER_DEVICE || 'cpu').trim().toLowerCase();
    const melottsDevice = (envConfig.MELOTTS_DEVICE || 'cpu').trim().toLowerCase();
    const gpuStatusKnown = typeof envConfig.GPU_AVAILABLE !== 'undefined';
    const runtimeGpuKnown = runtimeGpu !== null && typeof runtimeGpu.runtime_detected === 'boolean';
    const runtimeGpuDetected = runtimeGpu?.runtime_detected === true;
    const runtimeGpuUsable = runtimeGpu?.runtime_usable === true;

    const isBackendAvailable = (backend: string | undefined) => {
        const b = (backend || '').trim().toLowerCase();
        if (!b) return true;
        if (b === 'faster_whisper') return !!capabilities?.stt?.faster_whisper?.available;
        if (b === 'whisper_cpp') return !!capabilities?.stt?.whisper_cpp?.available;
        if (b === 'tone') return !!capabilities?.stt?.tone?.available;
        if (b === 'kroko') return true; // cloud always available; embedded availability is checked separately at apply time
        if (b === 'vosk') return true;
        if (b === 'sherpa') return true;
        return true;
    };

    const getCompatibilityIssues = (changes: { stt?: string; tts?: string; llm?: string }): CompatibilityIssue[] => {
        const issues: CompatibilityIssue[] = [];
        const sttSel = parseSelection(changes.stt);
        const ttsSel = parseSelection(changes.tts);

        if (sttSel.backend === 'faster_whisper' && capabilities && !capabilities.stt?.faster_whisper?.available) {
            issues.push({
                key: 'fw_rebuild',
                message: 'Faster-Whisper is not installed in this Local AI image. Full container rebuild is required.',
                requiresRebuild: true
            });
        }
        if (sttSel.backend === 'whisper_cpp' && capabilities && !capabilities.stt?.whisper_cpp?.available) {
            issues.push({
                key: 'whispercpp_rebuild',
                message: 'Whisper.cpp is not installed in this Local AI image. Full container rebuild is required.',
                requiresRebuild: true
            });
        }
        if (sttSel.backend === 'tone' && capabilities && !capabilities.stt?.tone?.available) {
            issues.push({
                key: 'tone_rebuild',
                message: 'T-one is not installed in this Local AI image. Full container rebuild is required.',
                requiresRebuild: true
            });
        }
        if (sttSel.backend === 'kroko' && capabilities && !capabilities.stt?.kroko_embedded?.available) {
            issues.push({
                key: 'kroko_rebuild',
                message: 'Kroko embedded binary is not installed in this Local AI image. Rebuild is required. For production hardening, set KROKO_SERVER_SHA256 in Env to pin the downloaded binary.',
                requiresRebuild: true
            });
        }
        if (ttsSel.backend === 'melotts' && capabilities && !capabilities.tts?.melotts?.available) {
            issues.push({
                key: 'melotts_rebuild',
                message: 'MeloTTS is not installed in this Local AI image. Full container rebuild is required.',
                requiresRebuild: true
            });
        }
        if (ttsSel.backend === 'silero' && capabilities && !capabilities.tts?.silero?.available) {
            issues.push({
                key: 'silero_rebuild',
                message: 'Silero TTS is not installed in this Local AI image. Full container rebuild with INCLUDE_SILERO=true is required.',
                requiresRebuild: true
            });
        }
        if (!gpuDetected && sttSel.backend === 'faster_whisper' && fasterWhisperDevice === 'cuda') {
            issues.push({
                key: 'fw_cuda_without_gpu',
                message: 'FASTER_WHISPER_DEVICE is set to CUDA but preflight reports no GPU. Use CPU in Env page unless forcing this config.',
                requiresRebuild: false
            });
        }
        if (!gpuDetected && ttsSel.backend === 'melotts' && melottsDevice === 'cuda') {
            issues.push({
                key: 'melotts_cuda_without_gpu',
                message: 'MELOTTS_DEVICE is set to CUDA but preflight reports no GPU. Use CPU in Env page unless forcing this config.',
                requiresRebuild: false
            });
        }
        if (runtimeGpuKnown && !runtimeGpuUsable && sttSel.backend === 'faster_whisper' && fasterWhisperDevice === 'cuda') {
            issues.push({
                key: 'fw_cuda_runtime_unavailable',
                message: `Runtime GPU is unavailable in local_ai_server${runtimeGpu?.error ? ` (${runtimeGpu.error})` : ''}. Faster-Whisper on CUDA is likely to fail.`,
                requiresRebuild: false
            });
        }
        if (runtimeGpuKnown && !runtimeGpuUsable && ttsSel.backend === 'melotts' && melottsDevice === 'cuda') {
            issues.push({
                key: 'melotts_cuda_runtime_unavailable',
                message: `Runtime GPU is unavailable in local_ai_server${runtimeGpu?.error ? ` (${runtimeGpu.error})` : ''}. MeloTTS on CUDA is likely to fail.`,
                requiresRebuild: false
            });
        }

        return issues;
    };

    const compatibilityIssues = getCompatibilityIssues(pendingChanges);
    const requiresRebuild = {
        fasterWhisper: compatibilityIssues.some(issue => issue.key === 'fw_rebuild'),
        meloTts: compatibilityIssues.some(issue => issue.key === 'melotts_rebuild'),
        krokoEmbedded: compatibilityIssues.some(issue => issue.key === 'kroko_rebuild'),
        whisperCpp: compatibilityIssues.some(issue => issue.key === 'whispercpp_rebuild'),
        tone: compatibilityIssues.some(issue => issue.key === 'tone_rebuild'),
        silero: compatibilityIssues.some(issue => issue.key === 'silero_rebuild')
    };
    const requiresAnyRebuild = requiresRebuild.fasterWhisper || requiresRebuild.whisperCpp || requiresRebuild.tone || requiresRebuild.meloTts || requiresRebuild.krokoEmbedded || requiresRebuild.silero;

    const applyPendingChanges = async () => {
        const hasModelChanges = Object.keys(pendingChanges).length > 0;
        const hasLlmTuningChanges = Object.keys(pendingLlmConfig).length > 0;
        if (!hasModelChanges && !hasLlmTuningChanges) return;
        if (compatibilityIssues.length > 0 && !forceIncompatibleApply) {
            showToast('Resolve compatibility warnings or enable force apply.', 'warning');
            return;
        }

        setRestarting(true);
        const startTime = Date.now();
        const progressTimer = window.setInterval(() => {
            setApplyProgress(prev => {
                if (!prev) return prev;
                return { ...prev, elapsedSeconds: Math.max(0, Math.floor((Date.now() - prev.startedAt) / 1000)) };
            });
        }, 1000);

        const updateApplyProgress = (
            phase: ApplyProgressState['phase'],
            percent: number,
            message: string,
            detail?: string
        ) => {
            setApplyProgress(prev => {
                const details = detail
                    ? [...(prev?.details || []), detail].slice(-6)
                    : (prev?.details || []);
                return {
                    phase,
                    percent,
                    message,
                    startedAt: prev?.startedAt || startTime,
                    elapsedSeconds: Math.max(0, Math.floor((Date.now() - startTime) / 1000)),
                    details,
                };
            });
        };

        updateApplyProgress('preparing', 5, 'Validating local model changes...', 'Starting apply flow');
        try {
            const remainingChanges = { ...pendingChanges };

            if (requiresAnyRebuild && forceIncompatibleApply) {
                updateApplyProgress('rebuilding', 20, 'Rebuilding local_ai_server image (this can take several minutes)...', 'Triggered forced rebuild for missing backends');
                const sttSel = parseSelection(remainingChanges.stt);
                const ttsSel = parseSelection(remainingChanges.tts);

                // For Silero, parse speaker and model_id from the synthetic path "silero:<speaker>:<model_id>"
                let sileroSpeaker: string | undefined;
                let sileroLanguage: string | undefined;
                let sileroModelId: string | undefined;
                if (ttsSel.backend === 'silero' && ttsSel.modelPath) {
                    const parts = ttsSel.modelPath.split(':');
                    sileroSpeaker = parts[0];
                    sileroModelId = parts[1];
                    // Derive language from model_id: v3_1_ru -> ru, v3_en -> en, etc.
                    const modelIdParts = (sileroModelId || '').split('_');
                    sileroLanguage = modelIdParts[modelIdParts.length - 1];
                }

                const rebuildRes = await axios.post('/api/local-ai/rebuild', {
                    include_faster_whisper: requiresRebuild.fasterWhisper,
                    include_whisper_cpp: requiresRebuild.whisperCpp,
                    include_tone: requiresRebuild.tone,
                    include_melotts: requiresRebuild.meloTts,
                    include_kroko_embedded: requiresRebuild.krokoEmbedded,
                    include_silero: requiresRebuild.silero,
                    stt_backend: sttSel.backend || undefined,
                    stt_model: sttSel.modelPath || undefined,
                    tts_backend: ttsSel.backend || undefined,
                    tts_voice: (() => {
                        if (!ttsSel.backend) return undefined;
                        if (ttsSel.backend === 'kokoro') {
                            return (envConfig.KOKORO_VOICE || 'af_heart').trim();
                        }
                        if (ttsSel.backend === 'silero') {
                            return sileroSpeaker || undefined;
                        }
                        return ttsSel.modelPath || undefined;
                    })(),
                    silero_speaker: sileroSpeaker,
                    silero_language: sileroLanguage,
                    silero_model_id: sileroModelId,
                });

                if (!rebuildRes.data?.success) {
                    throw new Error(rebuildRes.data?.message || 'Local AI rebuild failed.');
                }
                updateApplyProgress('restarting', 72, 'Rebuild complete. Restarting Local AI service...', rebuildRes.data?.message || 'Rebuild completed');
                showToast(rebuildRes.data?.message || 'Local AI rebuild completed.', 'success');

                if (requiresRebuild.fasterWhisper) delete remainingChanges.stt;
                if (requiresRebuild.meloTts || requiresRebuild.silero) delete remainingChanges.tts;
            }

            // Apply LLM changes (model and/or tuning) in one request to avoid multiple reloads.
            if (remainingChanges.llm || hasLlmTuningChanges) {
                updateApplyProgress('switching', 82, 'Applying LLM changes...', 'Sending LLM switch request');

                // Auto-set chat_format from catalog when switching LLM model
                if (remainingChanges.llm && availableModels?.llm) {
                    const matchedModel = (availableModels.llm as any[]).find(
                        (m: any) => m.model_path === remainingChanges.llm || m.path === remainingChanges.llm || m.id === remainingChanges.llm
                    );
                    const catalogChatFormat = matchedModel?.chat_format || '';
                    try {
                        const currentEnv = (await axios.get('/api/config/env')).data || {};
                        currentEnv['LOCAL_LLM_CHAT_FORMAT'] = catalogChatFormat;
                        await axios.post('/api/config/env', currentEnv);
                        updateApplyProgress('switching', 84, 'Applying LLM changes...', `Chat format auto-set to: ${catalogChatFormat || '(legacy)'}`);
                    } catch (envErr) {
                        console.warn('Failed to auto-set LOCAL_LLM_CHAT_FORMAT', envErr);
                    }
                }

                await axios.post('/api/local-ai/switch', {
                    model_type: 'llm',
                    model_path: remainingChanges.llm || undefined,
                    llm_context: pendingLlmConfig.context || undefined,
                    llm_max_tokens: pendingLlmConfig.max_tokens || undefined,
                    force_incompatible_apply: forceIncompatibleApply
                });
                delete remainingChanges.llm;
            }

            for (const [type, value] of Object.entries(remainingChanges)) {
                if (!value) continue;
                updateApplyProgress('switching', 88, `Applying ${type.toUpperCase()} change...`, `Switching ${type} backend/model`);
                const [backend, ...pathParts] = value.split(':');
                const extra: Record<string, any> = {};
                if (type === 'stt') {
                    if (backend === 'faster_whisper' && pendingSttExtra.language) {
                        extra.faster_whisper_language = pendingSttExtra.language;
                    } else if (backend === 'whisper_cpp' && pendingSttExtra.language) {
                        extra.whisper_cpp_language = pendingSttExtra.language;
                    } else if (backend === 'sherpa') {
                        if (pendingSttExtra.sherpa_model_type) extra.sherpa_model_type = pendingSttExtra.sherpa_model_type;
                        if (pendingSttExtra.sherpa_vad_model_path) extra.sherpa_vad_model_path = pendingSttExtra.sherpa_vad_model_path;
                    } else if (backend === 'tone') {
                        if (pendingSttExtra.tone_decoder_type) extra.tone_decoder_type = pendingSttExtra.tone_decoder_type;
                        if (pendingSttExtra.tone_kenlm_path) extra.tone_kenlm_path = pendingSttExtra.tone_kenlm_path;
                    }
                }
                if (type === 'tts' && backend === 'silero') {
                    // Parse Silero fields from synthetic path "speaker:model_id"
                    const silParts = pathParts.join(':').split(':');
                    extra.silero_speaker = silParts[0];
                    extra.silero_model_id = silParts[1];
                    // Derive language from model_id: v3_1_ru -> ru, v3_en -> en
                    const midParts = (silParts[1] || '').split('_');
                    extra.silero_language = midParts[midParts.length - 1];
                }
                await handleModelSwitch(type as 'stt' | 'tts', backend, pathParts.join(':'), forceIncompatibleApply, Object.keys(extra).length > 0 ? extra : undefined);
            }

            showToast(requiresAnyRebuild ? 'Compatibility override applied. Local AI has been rebuilt/restarted.' : 'Model switch requested. Server will restart.', 'success');
            setPendingChanges({});
            setPendingSttExtra({});
            setPendingLlmConfig({});
            setForceIncompatibleApply(false);
            updateApplyProgress('verifying', 95, 'Waiting for Local AI to come back online...', 'Refreshing active model status');
            setTimeout(() => {
                fetchActiveModels();
                updateApplyProgress('done', 100, 'Apply completed successfully.', 'Local model configuration is now active');
                window.setTimeout(() => setApplyProgress(null), 2000);
                setRestarting(false);
                window.clearInterval(progressTimer);
            }, 15000);
        } catch (err: any) {
            const errorMsg = err.response?.data?.detail || err.response?.data?.message || err.message || 'Failed to apply changes';
            updateApplyProgress('error', 100, 'Apply failed.', errorMsg);
            showToast(`Failed to apply changes: ${err.response?.data?.detail || err.response?.data?.message || err.message}`, 'error');
            setRestarting(false);
            window.clearInterval(progressTimer);
        }
    };

    return (
        <>
            <div className="p-6 space-y-6">
                {/* Toast notifications */}
                <div className="fixed top-4 right-4 z-50 space-y-2">
                    {toasts.map(toast => (
                        <div
                            key={toast.id}
                            className={`px-4 py-3 rounded-lg shadow-lg flex items-center gap-2 ${toast.type === 'success'
                                ? 'bg-green-600 text-white'
                                : toast.type === 'warning'
                                    ? 'bg-yellow-600 text-white'
                                    : 'bg-red-600 text-white'
                                }`}
                        >
                            {toast.type === 'success' ? (
                                <CheckCircle2 className="w-4 h-4" />
                            ) : toast.type === 'warning' ? (
                                <AlertTriangle className="w-4 h-4" />
                            ) : (
                                <XCircle className="w-4 h-4" />
                            )}
                            {toast.message}
                        </div>
                    ))}
                </div>

                {/* Local AI Server Section - Compact Header */}
                <div className="rounded-lg border border-border bg-card">
                    <div className="flex justify-between items-center px-4 py-3 border-b border-border">
                        <div className="flex items-center gap-3">
                            <Cpu className="w-5 h-5 text-blue-500" />
                            <h3 className="font-semibold">Local AI Server</h3>
                            <span className={`px-2 py-0.5 rounded-full text-xs font-medium flex items-center gap-1 ${serverStatus === 'connected' ? 'bg-green-500/10 text-green-500' :
                                serverStatus === 'error' ? 'bg-red-500/10 text-red-500' : 'bg-yellow-500/10 text-yellow-500'
                                }`}>
                                {serverStatus === 'connected' ? (
                                    <><CheckCircle2 className="w-3 h-3" /> Connected</>
                                ) : serverStatus === 'error' ? (
                                    <><XCircle className="w-3 h-3" /> Error</>
                                ) : (
                                    'Loading...'
                                )}
                            </span>
                            <div className="flex items-center gap-1 text-xs">
                                <span className="text-muted-foreground">GPU Detected:</span>
                                <span
                                    className={`px-2 py-0.5 rounded-full font-medium ${gpuStatusKnown
                                        ? (gpuDetected ? 'bg-green-500/10 text-green-500' : 'bg-amber-500/10 text-amber-500')
                                        : 'bg-muted text-muted-foreground'
                                        }`}
                                    title={`Host/preflight signal from .env GPU_AVAILABLE=${envConfig.GPU_AVAILABLE ?? 'unset'}`}
                                >
                                    Host
                                </span>
                                <span className="text-muted-foreground">/</span>
                                <span
                                    className={`px-2 py-0.5 rounded-full font-medium ${runtimeGpuKnown
                                        ? (runtimeGpuDetected ? 'bg-green-500/10 text-green-500' : 'bg-amber-500/10 text-amber-500')
                                        : 'bg-muted text-muted-foreground'
                                        }`}
                                    title={runtimeGpu?.error || 'Runtime probe from local_ai_server status'}
                                >
                                    Runtime
                                </span>
                            </div>
                        </div>
                        <div className="flex gap-1">
                            <Link
                                to="/env"
                                className="p-2 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground transition-colors"
                                title="Configure"
                            >
                                <Settings className="w-4 h-4" />
                            </Link>
                            <button
                                onClick={async () => {
                                    const confirmed = await confirm({
                                        title: 'Restart Local AI Server?',
                                        description: 'Are you sure you want to restart the Local AI Server? This will temporarily interrupt model inference.',
                                        confirmText: 'Restart',
                                        variant: 'destructive'
                                    });
                                    if (!confirmed) return;
                                    setRestarting(true);
                                    axios.post('/api/system/containers/local_ai_server/restart')
                                        .then(() => setTimeout(() => { fetchActiveModels(); setRestarting(false); }, 5000))
                                        .catch(() => setRestarting(false));
                                }}
                                disabled={restarting}
                                className="p-2 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground transition-colors"
                                title="Restart"
                            >
                                <RefreshCw className={`w-4 h-4 ${restarting ? 'animate-spin' : ''}`} />
                            </button>
                            <Link
                                to="/logs?container=local_ai_server"
                                className="p-2 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground transition-colors"
                                title="View Logs"
                            >
                                <Terminal className="w-4 h-4" />
                            </Link>
                        </div>
                    </div>

                    {serverStatus === 'connected' && activeModels && (
                        <div className="p-4 space-y-4">
                            <div className="text-xs text-muted-foreground">
                                {runtimeGpuKnown ? (
                                    <span>
                                        Runtime probe: {runtimeGpuUsable ? 'GPU usable' : 'GPU not usable'}
                                        {runtimeGpu?.source ? ` via ${runtimeGpu.source}` : ''}
                                        {runtimeGpu?.name ? ` (${runtimeGpu.name}${runtimeGpu.memory_gb ? `, ${runtimeGpu.memory_gb} GB` : ''})` : ''}
                                        {runtimeGpu?.error ? ` • ${runtimeGpu.error}` : ''}
                                    </span>
                                ) : (
                                    <span>Runtime probe: unavailable (Local AI status did not report GPU details)</span>
                                )}
                            </div>
                            {!gpuDetected && (fasterWhisperDevice === 'cuda' || melottsDevice === 'cuda') && (
                                <div className="p-3 rounded-md border border-amber-500/40 bg-amber-500/10 text-xs text-amber-700 dark:text-amber-300">
                                    CUDA device is configured for Local AI while preflight reports no GPU. This can cause degraded startup. Update device settings in <Link to="/env" className="underline">Env</Link> or force apply changes knowingly.
                                </div>
                            )}
                            {runtimeGpuKnown && !runtimeGpuUsable && (fasterWhisperDevice === 'cuda' || melottsDevice === 'cuda') && (
                                <div className="p-3 rounded-md border border-amber-500/40 bg-amber-500/10 text-xs text-amber-700 dark:text-amber-300">
                                    Runtime probe reports GPU unavailable in local_ai_server{runtimeGpu?.error ? ` (${runtimeGpu.error})` : ''}. CUDA-based STT/TTS may fail until runtime GPU is fixed.
                                </div>
                            )}
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                {/* STT Model */}
                                <div className="p-4 rounded-lg border border-border bg-muted/30">
                                    <div className="flex items-center gap-2 mb-2">
                                        <Mic className="w-4 h-4 text-blue-500" />
                                        <span className="text-sm font-medium">STT</span>
                                        <span className={`ml-auto px-2 py-0.5 rounded text-xs ${activeModels.stt.loaded ? 'bg-green-500/10 text-green-500' : 'bg-yellow-500/10 text-yellow-500'
                                            }`}>
                                            {activeModels.stt.loaded ? 'Loaded' : 'Not Loaded'}
                                        </span>
                                    </div>
                                    <select
                                        className="w-full text-xs p-2 rounded border border-border bg-background"
                                        value={pendingChanges.stt || `${activeModels.stt.backend}:${activeModels.stt.path}`}
                                        onChange={(e) => {
                                            const val = e.target.value;
                                            setPendingChanges(prev => ({ ...prev, stt: val }));
                                        }}
                                        disabled={restarting}
                                    >
                                        {availableModels?.stt && Object.entries(availableModels.stt).map(([backend, models]) => (
                                            backend === 'faster_whisper' ? null : (
                                                <optgroup key={backend} label={backend.charAt(0).toUpperCase() + backend.slice(1)}>
                                                    {models.map((m: any) => (
                                                        <option key={m.path} value={`${backend}:${m.path}`}>{m.name}</option>
                                                    ))}
                                                </optgroup>
                                            )
                                        ))}
                                        {!availableModels?.stt?.tone && (
                                            <optgroup label="T-one">
                                                <option value="tone:/app/models/stt/t-one">
                                                    T-one Russian {!capabilities?.stt?.tone?.available ? '(requires rebuild)' : ''}
                                                </option>
                                            </optgroup>
                                        )}
                                        <optgroup label="Faster Whisper">
                                            <option value="faster_whisper:base">
                                                Whisper Base {!capabilities?.stt?.faster_whisper?.available ? '(requires rebuild)' : ''}
                                            </option>
                                            <option value="faster_whisper:small">Whisper Small</option>
                                            <option value="faster_whisper:medium">Whisper Medium</option>
                                        </optgroup>
                                    </select>
                                    <div className="mt-2 text-xs text-muted-foreground truncate" title={activeModels.stt.display || activeModels.stt.path}>
                                        {activeModels.stt.display || getModelName(activeModels.stt.path)}
                                    </div>
                                    {activeModels.stt.language && (
                                        <div className="mt-1 text-xs text-muted-foreground">
                                            Language: <span className="font-medium">{activeModels.stt.language}</span>
                                        </div>
                                    )}
                                    {/* Language / mode quick-switch for STT backends that support it */}
                                    {(() => {
                                        const selectedStt = pendingChanges.stt || `${activeModels.stt.backend}:${activeModels.stt.path}`;
                                        const selectedBackend = selectedStt.split(':')[0];
                                        if (selectedBackend === 'faster_whisper' || selectedBackend === 'whisper_cpp') {
                                            return (
                                                <div className="mt-2">
                                                    <label className="text-[10px] text-muted-foreground">Language (ISO 639-1)</label>
                                                    <input
                                                        type="text"
                                                        className={`w-full text-xs p-1.5 rounded border bg-background ${pendingSttExtra.language ? 'border-yellow-500' : 'border-border'}`}
                                                        value={pendingSttExtra.language ?? (activeModels.stt.language || 'en')}
                                                        onChange={(e) => {
                                                            const lang = e.target.value.trim().toLowerCase();
                                                            setPendingSttExtra(prev => ({ ...prev, language: lang }));
                                                            if (!pendingChanges.stt) setPendingChanges(prev => ({ ...prev, stt: selectedStt }));
                                                        }}
                                                        placeholder="en"
                                                        disabled={restarting}
                                                    />
                                                </div>
                                            );
                                        }
                                        if (selectedBackend === 'sherpa') {
                                            return (
                                                <div className="mt-2 space-y-1.5">
                                                    <div>
                                                        <label className="text-[10px] text-muted-foreground">Model Type</label>
                                                        <select
                                                            className={`w-full text-xs p-1.5 rounded border bg-background ${pendingSttExtra.sherpa_model_type ? 'border-yellow-500' : 'border-border'}`}
                                                            value={pendingSttExtra.sherpa_model_type ?? (activeModels.stt as any).sherpa_model_type ?? 'online'}
                                                            onChange={(e) => {
                                                                setPendingSttExtra(prev => ({ ...prev, sherpa_model_type: e.target.value }));
                                                                if (!pendingChanges.stt) setPendingChanges(prev => ({ ...prev, stt: selectedStt }));
                                                            }}
                                                            disabled={restarting}
                                                        >
                                                            <option value="online">Online (Streaming)</option>
                                                            <option value="offline">Offline (VAD-gated)</option>
                                                        </select>
                                                    </div>
                                                    {((pendingSttExtra.sherpa_model_type ?? (activeModels.stt as any).sherpa_model_type) === 'offline') && (
                                                        <div>
                                                            <label className="text-[10px] text-muted-foreground">Silero VAD Path</label>
                                                            <input
                                                                type="text"
                                                                className={`w-full text-xs p-1.5 rounded border bg-background ${pendingSttExtra.sherpa_vad_model_path ? 'border-yellow-500' : 'border-border'}`}
                                                                value={pendingSttExtra.sherpa_vad_model_path || ''}
                                                                onChange={(e) => {
                                                                    setPendingSttExtra(prev => ({ ...prev, sherpa_vad_model_path: e.target.value }));
                                                                }}
                                                                placeholder="/app/models/vad/silero_vad.onnx"
                                                                disabled={restarting}
                                                            />
                                                        </div>
                                                    )}
                                                </div>
                                            );
                                        }
                                        if (selectedBackend === 'tone') {
                                            return (
                                                <div className="mt-2 space-y-1.5">
                                                    <div>
                                                        <label className="text-[10px] text-muted-foreground">Decoder</label>
                                                        <select
                                                            className={`w-full text-xs p-1.5 rounded border bg-background ${pendingSttExtra.tone_decoder_type ? 'border-yellow-500' : 'border-border'}`}
                                                            value={pendingSttExtra.tone_decoder_type ?? (activeModels.stt as any).tone_decoder_type ?? 'beam_search'}
                                                            onChange={(e) => {
                                                                setPendingSttExtra(prev => ({ ...prev, tone_decoder_type: e.target.value }));
                                                                if (!pendingChanges.stt) setPendingChanges(prev => ({ ...prev, stt: selectedStt }));
                                                            }}
                                                            disabled={restarting}
                                                        >
                                                            <option value="beam_search">Beam Search</option>
                                                            <option value="greedy">Greedy</option>
                                                        </select>
                                                    </div>
                                                    {(pendingSttExtra.tone_decoder_type ?? (activeModels.stt as any).tone_decoder_type ?? 'beam_search') === 'beam_search' && (
                                                        <div>
                                                            <label className="text-[10px] text-muted-foreground">KenLM Path</label>
                                                            <input
                                                                type="text"
                                                                className={`w-full text-xs p-1.5 rounded border bg-background ${pendingSttExtra.tone_kenlm_path ? 'border-yellow-500' : 'border-border'}`}
                                                                value={pendingSttExtra.tone_kenlm_path || ''}
                                                                onChange={(e) => {
                                                                    setPendingSttExtra(prev => ({ ...prev, tone_kenlm_path: e.target.value }));
                                                                }}
                                                                placeholder="/app/models/stt/t-one/kenlm.bin"
                                                                disabled={restarting}
                                                            />
                                                        </div>
                                                    )}
                                                </div>
                                            );
                                        }
                                        return null;
                                    })()}
                                </div>

                                {/* LLM Model */}
                                <div className="p-4 rounded-lg border border-border bg-muted/30">
                                    <div className="flex items-center gap-2 mb-3">
                                        <Brain className="w-4 h-4 text-purple-500" />
                                        <span className="text-sm font-medium">LLM</span>
                                        <span className={`ml-auto px-2 py-0.5 rounded text-xs ${activeModels.llm.loaded ? 'bg-green-500/10 text-green-500' : 'bg-yellow-500/10 text-yellow-500'
                                            }`}>
                                            {activeModels.llm.loaded ? 'Loaded' : 'Not Loaded'}
                                        </span>
                                    </div>
                                    <select
                                        className="w-full text-xs p-2 rounded border border-border bg-background"
                                        value={pendingChanges.llm || activeModels.llm.path}
                                        onChange={(e) => {
                                            setPendingChanges(prev => ({ ...prev, llm: e.target.value }));
                                        }}
                                        disabled={restarting}
                                    >
                                        {availableModels?.llm?.map((m: any) => (
                                            <option key={m.path} value={m.path}>{m.name}</option>
                                        ))}
                                    </select>

                                    {/* Tuning Controls */}
                                    <div className="mt-3 flex gap-2">
                                        <div className="flex-1 min-w-0">
                                            <label className="block text-[10px] uppercase tracking-wide text-muted-foreground mb-1">Context</label>
                                            <select
                                                value={pendingLlmConfig.context ?? activeModels.llm.config?.context ?? ''}
                                                onChange={(e) => {
                                                    const v = e.target.value ? parseInt(e.target.value, 10) : undefined;
                                                    setPendingLlmConfig(prev => ({ ...prev, context: v }));
                                                }}
                                                className="w-full px-2 py-1.5 text-xs rounded-md border border-border bg-background"
                                                disabled={restarting}
                                                title="Change requires LLM reload. Leave blank to keep current value."
                                            >
                                                <option value="">(unchanged)</option>
                                                {[768, 1024, 1536, 2048, 3072, 4096].map(v => (
                                                    <option key={v} value={v}>{v}</option>
                                                ))}
                                            </select>
                                        </div>
                                        <div className="flex-1 min-w-0">
                                            <label className="block text-[10px] uppercase tracking-wide text-muted-foreground mb-1">Max Tokens</label>
                                            <input
                                                type="number"
                                                min={1}
                                                value={pendingLlmConfig.max_tokens ?? activeModels.llm.config?.max_tokens ?? ''}
                                                onChange={(e) => {
                                                    const v = e.target.value ? parseInt(e.target.value, 10) : undefined;
                                                    setPendingLlmConfig(prev => ({ ...prev, max_tokens: v }));
                                                }}
                                                className="w-full px-2 py-1.5 text-xs rounded-md border border-border bg-background"
                                                disabled={restarting}
                                                title="Upper bound for each local LLM response."
                                            />
                                        </div>
                                    </div>

                                    {/* Runtime Stats */}
                                    {(activeModels.llm.prompt_fit?.system_prompt_tokens != null || activeModels.llm.prompt_fit?.safe_max_tokens != null) && (
                                        <div className="mt-3 rounded-md border border-border bg-muted/20 px-3 py-2">
                                            <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
                                                <div>
                                                    <span className="text-muted-foreground">Prompt tokens</span>
                                                    <span className="ml-1 font-mono font-medium">{activeModels.llm.prompt_fit?.system_prompt_tokens ?? '—'}</span>
                                                </div>
                                                <div>
                                                    <span className="text-muted-foreground">Safe max</span>
                                                    <span className="ml-1 font-mono font-medium">{activeModels.llm.prompt_fit?.safe_max_tokens ?? '—'}</span>
                                                </div>
                                                <div>
                                                    <span className="text-muted-foreground">Tools</span>
                                                    <span className="ml-1 font-mono font-medium">{activeModels.llm.tool_capability?.level || 'unknown'}</span>
                                                </div>
                                                <div>
                                                    <span className="text-muted-foreground">Policy</span>
                                                    <span className="ml-1 font-mono font-medium">{resolveToolPolicy()}</span>
                                                </div>
                                                {activeModels.llm.auto_context?.enabled && (
                                                    <div className="col-span-2">
                                                        <span className="text-muted-foreground">Auto ctx</span>
                                                        <span className="ml-1 font-mono font-medium">{activeModels.llm.auto_context?.source || 'auto'}</span>
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    )}
                                </div>

                                {/* TTS Model */}
                                <div className="p-4 rounded-lg border border-border bg-muted/30">
                                    <div className="flex items-center gap-2 mb-2">
                                        <Volume2 className="w-4 h-4 text-green-500" />
                                        <span className="text-sm font-medium">TTS</span>
                                        <span className={`ml-auto px-2 py-0.5 rounded text-xs ${activeModels.tts.loaded ? 'bg-green-500/10 text-green-500' : 'bg-yellow-500/10 text-yellow-500'
                                            }`}>
                                            {activeModels.tts.loaded ? 'Loaded' : 'Not Loaded'}
                                        </span>
                                    </div>
                                    <select
                                        className="w-full text-xs p-2 rounded border border-border bg-background"
                                        value={pendingChanges.tts || `${activeModels.tts.backend}:${activeModels.tts.path}`}
                                        onChange={(e) => {
                                            const val = e.target.value;
                                            setPendingChanges(prev => ({ ...prev, tts: val }));
                                        }}
                                        disabled={restarting}
                                    >
                                        {availableModels?.tts && Object.entries(availableModels.tts).map(([backend, models]) => (
                                            backend === 'melotts' ? null : (
                                                <optgroup key={backend} label={backend.charAt(0).toUpperCase() + backend.slice(1)}>
                                                    {models.map((m: any) => (
                                                        <option key={m.path} value={`${backend}:${m.path}`}>{m.name}</option>
                                                    ))}
                                                </optgroup>
                                            )
                                        ))}
                                        <optgroup label="MeloTTS">
                                            <option value="melotts:EN-US">
                                                MeloTTS US {!capabilities?.tts?.melotts?.available ? '(requires rebuild)' : ''}
                                            </option>
                                            <option value="melotts:EN-BR">MeloTTS UK</option>
                                            <option value="melotts:EN-AU">MeloTTS AU</option>
                                        </optgroup>
                                    </select>
                                    <div className="mt-2 text-xs text-muted-foreground truncate" title={activeModels.tts.display || activeModels.tts.path}>
                                        {activeModels.tts.display || getModelName(activeModels.tts.path)}
                                    </div>
                                </div>
                            </div>
                        </div>
                    )}

                    {serverStatus === 'error' && (
                        <div className="p-4 bg-yellow-500/10 border border-yellow-500/30 rounded-lg">
                            <p className="text-sm text-yellow-600 dark:text-yellow-400 mb-3">
                                Local AI Server is not reachable. The container may still be running.
                            </p>
                            <button
                                onClick={() => {
                                    setStartingServer(true);
                                    axios.post('/api/system/containers/local_ai_server/start')
                                        .then(() => setTimeout(() => { fetchActiveModels(); setStartingServer(false); }, 5000))
                                        .catch(() => setStartingServer(false));
                                }}
                                disabled={startingServer}
                                className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700 disabled:opacity-50 transition-colors"
                            >
                                {startingServer ? (
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

                    {/* Apply Changes Button */}
                    {Object.keys(pendingChanges).length > 0 && (
                        <div className="mt-4 space-y-3">
                            {restarting && applyProgress && (
                                <div className="p-3 rounded-md border border-blue-500/30 bg-blue-500/10 text-sm space-y-2">
                                    <div className="flex items-center justify-between gap-2">
                                        <div className="font-medium text-blue-700 dark:text-blue-300">
                                            {applyProgress.message}
                                        </div>
                                        <div className="text-xs text-blue-700 dark:text-blue-300">
                                            {applyProgress.percent}%
                                        </div>
                                    </div>
                                    <div className="h-2 rounded-full bg-blue-200/70 dark:bg-blue-900/50 overflow-hidden">
                                        <div
                                            className={`h-full transition-all duration-300 ${applyProgress.phase === 'error' ? 'bg-red-500' : 'bg-blue-500'
                                                }`}
                                            style={{ width: `${applyProgress.percent}%` }}
                                        />
                                    </div>
                                    <div className="flex items-center justify-between text-xs text-blue-800 dark:text-blue-200">
                                        <span>Phase: {applyProgress.phase}</span>
                                        <span>Elapsed: {applyProgress.elapsedSeconds}s</span>
                                    </div>
                                    {applyProgress.details.length > 0 && (
                                        <div className="text-xs text-blue-900 dark:text-blue-100 space-y-0.5 max-h-20 overflow-auto">
                                            {applyProgress.details.map((detail, idx) => (
                                                <div key={`${detail}-${idx}`} className="truncate">• {detail}</div>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            )}
                            {!restarting && compatibilityIssues.length > 0 && (
                                <div className="p-3 rounded-md border border-amber-500/40 bg-amber-500/10 text-sm">
                                    <div className="font-medium text-amber-700 dark:text-amber-300 mb-1">
                                        Compatibility checks found warnings
                                    </div>
                                    <ul className="list-disc pl-5 space-y-1 text-amber-700 dark:text-amber-300">
                                        {compatibilityIssues.map(issue => (
                                            <li key={issue.key}>{issue.message}</li>
                                        ))}
                                    </ul>
                                    {requiresAnyRebuild && (
                                        <div className="mt-2 text-xs text-amber-800 dark:text-amber-200">
                                            Force apply will trigger a full `local_ai_server` image rebuild and recreate.
                                        </div>
                                    )}
                                    <label className="mt-2 flex items-center gap-2 text-xs text-amber-800 dark:text-amber-200">
                                        <input
                                            type="checkbox"
                                            className="rounded border-amber-500/50"
                                            checked={forceIncompatibleApply}
                                            onChange={(e) => setForceIncompatibleApply(e.target.checked)}
                                            disabled={restarting}
                                        />
                                        Force apply incompatible selections
                                    </label>
                                </div>
                            )}
                            <div className="flex gap-2">
                                <button
                                    onClick={applyPendingChanges}
                                    disabled={restarting || (compatibilityIssues.length > 0 && !forceIncompatibleApply)}
                                    className="flex-1 flex items-center justify-center gap-2 px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700 disabled:opacity-50 transition-colors"
                                >
                                    {restarting ? (
                                        <>
                                            <RefreshCw className="w-4 h-4 animate-spin" />
                                            Restarting...
                                        </>
                                    ) : (
                                        <>
                                            <CheckCircle2 className="w-4 h-4" />
                                            {requiresAnyRebuild && forceIncompatibleApply ? 'Apply (Force + Rebuild)' : 'Apply Changes & Restart'}
                                        </>
                                    )}
                                </button>
                                <button
                                    onClick={() => {
                                        setPendingChanges({});
                                        setForceIncompatibleApply(false);
                                    }}
                                    disabled={restarting}
                                    className="px-4 py-2 bg-muted text-muted-foreground rounded-md hover:bg-muted/80 transition-colors"
                                >
                                    Cancel
                                </button>
                            </div>
                        </div>
                    )}
                </div>

                {/* Model Library Section - Full Width */}
                <div className="rounded-lg border border-border bg-card">
                    <div className="flex justify-between items-center px-4 py-3 border-b border-border">
                        <div>
                            <h3 className="font-semibold">Model Library</h3>
                            <p className="text-sm text-muted-foreground">Download and manage STT, TTS, and LLM models</p>
                        </div>
                        <button
                            onClick={fetchModels}
                            disabled={loading}
                            className="p-2 rounded-md hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"
                            title="Refresh"
                        >
                            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
                        </button>
                    </div>
                    {/* Tabs and Region Filter */}
                    <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-border">
                        <button
                            onClick={() => setSelectedTab('installed')}
                            className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${selectedTab === 'installed'
                                ? 'bg-primary text-primary-foreground'
                                : 'bg-muted hover:bg-muted/80'
                                }`}
                        >
                            Installed ({installedModels.length})
                        </button>
                        <button
                            onClick={() => setSelectedTab('stt')}
                            className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors flex items-center gap-1.5 ${selectedTab === 'stt'
                                ? 'bg-primary text-primary-foreground'
                                : 'bg-muted hover:bg-muted/80'
                                }`}
                        >
                            <Mic className="w-3.5 h-3.5" /> STT ({catalog.stt.length})
                        </button>
                        <button
                            onClick={() => setSelectedTab('tts')}
                            className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors flex items-center gap-1.5 ${selectedTab === 'tts'
                                ? 'bg-primary text-primary-foreground'
                                : 'bg-muted hover:bg-muted/80'
                                }`}
                        >
                            <Volume2 className="w-3.5 h-3.5" /> TTS ({catalog.tts.length})
                        </button>
                        <button
                            onClick={() => setSelectedTab('llm')}
                            className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors flex items-center gap-1.5 ${selectedTab === 'llm'
                                ? 'bg-primary text-primary-foreground'
                                : 'bg-muted hover:bg-muted/80'
                                }`}
                        >
                            <Brain className="w-3.5 h-3.5" /> LLM ({catalog.llm.length})
                        </button>
                        {selectedTab !== 'installed' && selectedTab !== 'llm' && (
                            <select
                                value={selectedRegion}
                                onChange={e => setSelectedRegion(e.target.value)}
                                className="ml-auto px-3 py-1.5 rounded-md border border-input bg-background text-sm"
                            >
                                <option value="all">All Regions</option>
                                {getUniqueRegions().map(region => (
                                    <option key={region} value={region}>
                                        {regionNames[region] || region}
                                    </option>
                                ))}
                            </select>
                        )}
                    </div>

                    {/* Content Area */}
                    <div className="p-4">
                        {/* Download Progress Bar */}
                        {downloadingModel && downloadProgress && (
                            <div className="mb-4 p-4 bg-blue-50 dark:bg-blue-900/20 rounded-lg border border-blue-200 dark:border-blue-800">
                                <div className="flex justify-between items-center mb-2">
                                    <span className="text-sm font-medium text-blue-800 dark:text-blue-300">
                                        Downloading: {downloadProgress.current_file || downloadingModel}
                                    </span>
                                    <span className="text-sm text-blue-600 dark:text-blue-400">
                                        {downloadProgress.total_bytes > 0 ? `${downloadProgress.percent}%` : 'Downloading...'}
                                    </span>
                                </div>
                                <div className="w-full bg-blue-200 dark:bg-blue-800 rounded-full h-2 mb-2 overflow-hidden">
                                    {downloadProgress.total_bytes > 0 ? (
                                        <div
                                            className="bg-blue-600 dark:bg-blue-400 h-2 rounded-full transition-all duration-300"
                                            style={{ width: `${downloadProgress.percent}%` }}
                                        />
                                    ) : (
                                        <div className="bg-blue-600 dark:bg-blue-400 h-2 rounded-full animate-pulse w-full opacity-50" />
                                    )}
                                </div>
                                <div className="flex justify-between text-xs text-blue-600 dark:text-blue-400">
                                    <span>
                                        {(downloadProgress.bytes_downloaded / (1024 * 1024)).toFixed(1)} MB
                                        {downloadProgress.total_bytes > 0 && ` / ${(downloadProgress.total_bytes / (1024 * 1024)).toFixed(1)} MB`}
                                    </span>
                                    <span>
                                        {downloadProgress.speed_bps > 0 && `${(downloadProgress.speed_bps / (1024 * 1024)).toFixed(2)} MB/s`}
                                        {downloadProgress.eta_seconds !== null && downloadProgress.eta_seconds > 0 && (
                                            <> • ETA: {Math.floor(downloadProgress.eta_seconds / 60)}m {downloadProgress.eta_seconds % 60}s</>
                                        )}
                                    </span>
                                </div>
                            </div>
                        )}

                        {loading ? (
                            <div className="flex justify-center items-center py-12">
                                <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
                            </div>
                        ) : (
                            <>
                                {/* Installed Models Tab */}
                                {selectedTab === 'installed' && (
                                    <div className="space-y-4">
                                        {installedModels.length === 0 ? (
                                            <div className="text-center py-12 text-muted-foreground">
                                                <HardDrive className="w-12 h-12 mx-auto mb-4 opacity-50" />
                                                <p>No models installed yet.</p>
                                                <p className="text-sm mt-2">Browse the STT, TTS, and LLM tabs to download models.</p>
                                            </div>
                                        ) : (
                                            <div className="grid gap-4">
                                                {installedModels.map(model => (
                                                    <ConfigCard key={model.path}>
                                                        <div className="flex justify-between items-center">
                                                            <div className="flex items-center gap-3">
                                                                <div className={`p-2 rounded-lg ${model.type === 'stt' ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-600' :
                                                                    model.type === 'tts' ? 'bg-green-100 dark:bg-green-900/30 text-green-600' :
                                                                        'bg-purple-100 dark:bg-purple-900/30 text-purple-600'
                                                                    }`}>
                                                                    {getTypeIcon(model.type)}
                                                                </div>
                                                                <div>
                                                                    <p className="font-medium">{getModelDisplayName(model)}</p>
                                                                    <p className="text-sm text-muted-foreground">
                                                                        {model.type.toUpperCase()} • {model.size_mb.toFixed(0)} MB • {model.name}
                                                                    </p>
                                                                </div>
                                                            </div>
                                                            <button
                                                                onClick={() => handleDelete(model)}
                                                                disabled={deletingModel === model.name}
                                                                className="p-2 rounded-md bg-red-100 dark:bg-red-900/30 text-red-600 hover:bg-red-200 dark:hover:bg-red-900/50 transition-colors"
                                                            >
                                                                {deletingModel === model.name ? (
                                                                    <Loader2 className="w-4 h-4 animate-spin" />
                                                                ) : (
                                                                    <Trash2 className="w-4 h-4" />
                                                                )}
                                                            </button>
                                                        </div>
                                                    </ConfigCard>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                )}

                                {/* STT Models Tab */}
                                {selectedTab === 'stt' && (
                                    <div className="grid gap-4">
                                        {filterByRegion(catalog.stt).map(model => (
                                            <ConfigCard key={model.id}>
                                                <div className="flex justify-between items-center">
                                                    <div className="flex items-center gap-3">
                                                        <div className="p-2 rounded-lg bg-blue-100 dark:bg-blue-900/30 text-blue-600">
                                                            <Mic className="w-4 h-4" />
                                                        </div>
                                                        <div>
                                                            <div className="flex items-center gap-2">
                                                                <p className="font-medium">{model.name}</p>
                                                                {!model.auto_download && isModelInstalled(model.model_path || '') && (
                                                                    <span className="px-2 py-0.5 text-xs bg-green-100 dark:bg-green-900/30 text-green-600 rounded-full">
                                                                        Installed
                                                                    </span>
                                                                )}
                                                                {model.system_recommended && (
                                                                    <span className="px-2 py-0.5 text-xs bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 rounded-full">
                                                                        Recommended
                                                                    </span>
                                                                )}
                                                            </div>
                                                            <p className="text-sm text-muted-foreground">
                                                                {languageNames[model.language || ''] || model.language} • {model.size_display} • {model.backend}
                                                            </p>
                                                            {(model.description || model.note) && (
                                                                <div className="mt-1 space-y-1">
                                                                    {model.description && (
                                                                        <p className="text-xs text-muted-foreground">
                                                                            {model.description}
                                                                        </p>
                                                                    )}
                                                                    {model.note && (
                                                                        <p className="text-xs text-amber-600 dark:text-amber-500">
                                                                            {model.note}
                                                                        </p>
                                                                    )}
                                                                </div>
                                                            )}
                                                        </div>
                                                    </div>
                                                    {!isModelInstalled(model.model_path || '') && model.download_url && (
                                                        <button
                                                            onClick={() => handleDownload(model, 'stt')}
                                                            disabled={downloadingModel === model.id}
                                                            className="px-3 py-2 rounded-md bg-blue-600 text-white hover:bg-blue-700 transition-colors flex items-center gap-2 text-sm"
                                                        >
                                                            {downloadingModel === model.id ? (
                                                                <Loader2 className="w-4 h-4 animate-spin" />
                                                            ) : (
                                                                <Download className="w-4 h-4" />
                                                            )}
                                                            Download
                                                        </button>
                                                    )}
                                                    {model.auto_download && !model.download_url && !isBackendAvailable(model.backend) && (
                                                        <div className="flex flex-col items-end gap-1">
                                                            <button
                                                                onClick={() => openRebuildDialog(
                                                                    model.backend || 'faster_whisper',
                                                                    model.backend === 'faster_whisper' ? 'Faster Whisper' :
                                                                        model.backend === 'whisper_cpp' ? 'Whisper.cpp' :
                                                                            model.backend || 'Backend',
                                                                    model.backend === 'faster_whisper' ? 180 :
                                                                        model.backend === 'whisper_cpp' ? 240 : 180
                                                                )}
                                                                className="px-3 py-2 rounded-md bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 hover:bg-amber-200 dark:hover:bg-amber-900/50 text-sm flex items-center gap-2 transition-colors cursor-pointer"
                                                            >
                                                                <Wrench className="w-4 h-4" />
                                                                Enable Backend
                                                            </button>
                                                            <span className="text-[10px] text-amber-600 dark:text-amber-500 max-w-[200px] text-right">
                                                                {model.note || 'Requires container rebuild (~3 min)'}
                                                            </span>
                                                        </div>
                                                    )}
                                                </div>
                                            </ConfigCard>
                                        ))}
                                    </div>
                                )}

                                {/* TTS Models Tab */}
                                {selectedTab === 'tts' && (
                                    <div className="grid gap-4">
                                        {filterByRegion(catalog.tts).map(model => (
                                            <ConfigCard key={model.id}>
                                                <div className="flex justify-between items-center">
                                                    <div className="flex items-center gap-3">
                                                        <div className="p-2 rounded-lg bg-green-100 dark:bg-green-900/30 text-green-600">
                                                            <Volume2 className="w-4 h-4" />
                                                        </div>
                                                        <div>
                                                            <div className="flex items-center gap-2">
                                                                <p className="font-medium">{model.name}</p>
                                                                {model.gender && (
                                                                    <span className="px-2 py-0.5 text-xs bg-muted text-muted-foreground rounded-full">
                                                                        {model.gender}
                                                                    </span>
                                                                )}
                                                                {isModelInstalled(model.model_path || '') && (
                                                                    <span className="px-2 py-0.5 text-xs bg-green-100 dark:bg-green-900/30 text-green-600 rounded-full">
                                                                        Installed
                                                                    </span>
                                                                )}
                                                            </div>
                                                            <p className="text-sm text-muted-foreground">
                                                                {languageNames[model.language || ''] || model.language} • {model.size_display} • {model.quality || 'medium'}
                                                            </p>
                                                        </div>
                                                    </div>
                                                    {!isModelInstalled(model.model_path || '') && model.download_url && (
                                                        <button
                                                            onClick={() => handleDownload(model, 'tts')}
                                                            disabled={downloadingModel === model.id}
                                                            className="px-3 py-2 rounded-md bg-green-600 text-white hover:bg-green-700 transition-colors flex items-center gap-2 text-sm"
                                                        >
                                                            {downloadingModel === model.id ? (
                                                                <Loader2 className="w-4 h-4 animate-spin" />
                                                            ) : (
                                                                <Download className="w-4 h-4" />
                                                            )}
                                                            Download
                                                        </button>
                                                    )}
                                                    {!isModelInstalled(model.model_path || '') && model.auto_download && !model.download_url && (
                                                        <div className="flex flex-col items-end gap-1">
                                                            <button
                                                                onClick={() => openRebuildDialog(
                                                                    model.backend || 'melotts',
                                                                    model.backend === 'melotts' ? 'MeloTTS' :
                                                                        model.backend === 'kokoro' ? 'Kokoro' :
                                                                            model.backend || 'Backend',
                                                                    model.backend === 'melotts' ? 300 : 180
                                                                )}
                                                                className="px-3 py-2 rounded-md bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 hover:bg-amber-200 dark:hover:bg-amber-900/50 text-sm flex items-center gap-2 transition-colors cursor-pointer"
                                                            >
                                                                <Wrench className="w-4 h-4" />
                                                                Enable Backend
                                                            </button>
                                                            <span className="text-[10px] text-amber-600 dark:text-amber-500 max-w-[200px] text-right">
                                                                {model.note || 'Requires container rebuild (~5 min)'}
                                                            </span>
                                                        </div>
                                                    )}
                                                </div>
                                            </ConfigCard>
                                        ))}
                                    </div>
                                )}

                                {/* LLM Models Tab */}
                                {selectedTab === 'llm' && (
                                    <div className="grid gap-4">
                                        {catalog.llm.map(model => (
                                            <ConfigCard key={model.id}>
                                                <div className="flex justify-between items-center">
                                                    <div className="flex items-center gap-3">
                                                        <div className="p-2 rounded-lg bg-purple-100 dark:bg-purple-900/30 text-purple-600">
                                                            <Brain className="w-4 h-4" />
                                                        </div>
                                                        <div>
                                                            <div className="flex items-center gap-2">
                                                                <p className="font-medium">{model.name}</p>
                                                                {isModelInstalled(model.model_path || '') && (
                                                                    <span className="px-2 py-0.5 text-xs bg-green-100 dark:bg-green-900/30 text-green-600 rounded-full">
                                                                        Installed
                                                                    </span>
                                                                )}
                                                                {model.tool_calling === 'recommended' && (
                                                                    <span className="px-2 py-0.5 text-xs bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 rounded-full">
                                                                        Tool calls
                                                                    </span>
                                                                )}
                                                                {model.tool_calling === 'experimental' && (
                                                                    <span className="px-2 py-0.5 text-xs bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 rounded-full">
                                                                        Tool calls (exp)
                                                                    </span>
                                                                )}
                                                            </div>
                                                            <p className="text-sm text-muted-foreground">
                                                                {model.size_display}
                                                                {model.recommended_ram_gb ? ` • RAM ${model.recommended_ram_gb}GB+` : ''}
                                                            </p>
                                                            {(model.description || model.tool_calling_note || model.note) && (
                                                                <div className="mt-1 space-y-1">
                                                                    {model.description && (
                                                                        <p className="text-xs text-muted-foreground">
                                                                            {model.description}
                                                                        </p>
                                                                    )}
                                                                    {model.tool_calling_note && (
                                                                        <p className="text-xs text-muted-foreground">
                                                                            {model.tool_calling_note}
                                                                        </p>
                                                                    )}
                                                                    {model.note && (
                                                                        <p className="text-xs text-amber-600 dark:text-amber-500">
                                                                            {model.note}
                                                                        </p>
                                                                    )}
                                                                </div>
                                                            )}
                                                        </div>
                                                    </div>
                                                    {!isModelInstalled(model.model_path || '') && model.download_url && (
                                                        <button
                                                            onClick={() => handleDownload(model, 'llm')}
                                                            disabled={downloadingModel === model.id}
                                                            className="px-3 py-2 rounded-md bg-purple-600 text-white hover:bg-purple-700 transition-colors flex items-center gap-2 text-sm"
                                                        >
                                                            {downloadingModel === model.id ? (
                                                                <Loader2 className="w-4 h-4 animate-spin" />
                                                            ) : (
                                                                <Download className="w-4 h-4" />
                                                            )}
                                                            Download
                                                        </button>
                                                    )}
                                                </div>
                                            </ConfigCard>
                                        ))}
                                    </div>
                                )}
                            </>
                        )}
                    </div>
                </div>
            </div>

            {/* Rebuild Backend Dialog */}
            <RebuildBackendDialog
                isOpen={rebuildDialog.isOpen}
                backend={rebuildDialog.backend}
                backendDisplayName={rebuildDialog.backendDisplayName}
                estimatedSeconds={rebuildDialog.estimatedSeconds}
                onClose={closeRebuildDialog}
                onComplete={handleRebuildComplete}
            />
        </>
    );
};

export default ModelsPage;
