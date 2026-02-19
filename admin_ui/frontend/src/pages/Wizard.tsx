import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertCircle, ArrowRight, Loader2, Cloud, Server, Shield, Zap, SkipForward, CheckCircle, CheckCircle2, XCircle, Terminal, Copy, HardDrive, Play, RefreshCw, Info, AlertTriangle } from 'lucide-react';
import axios from 'axios';
import { useConfirmDialog } from '../hooks/useConfirmDialog';

interface SetupConfig {
    provider: string;
    asterisk_host: string;
    asterisk_username: string;
    asterisk_password: string;
    asterisk_port?: number;
    asterisk_scheme?: string;
    asterisk_app?: string;
    asterisk_server_ip?: string;  // Required when asterisk_host is a hostname (for RTP security)
    asterisk_ssl_verify?: boolean;  // Skip SSL cert verification for self-signed certs
    openai_key?: string;
    groq_key?: string;
    deepgram_key?: string;
    google_key?: string;
    elevenlabs_key?: string;
    elevenlabs_agent_id?: string;
    cartesia_key?: string;
    greeting: string;
    ai_name: string;
    ai_role: string;
    hybrid_llm_provider?: string;
    // Local AI Config
    local_stt_backend?: string;
    local_stt_model?: string;
    kroko_embedded?: boolean;
    kroko_api_key?: string;
    local_tts_backend?: string;
    local_tts_model?: string;
    kokoro_mode?: string;
    kokoro_voice?: string;
    kokoro_api_key?: string;
    kokoro_api_base_url?: string;
    local_llm_model?: string;
    local_llm_custom_url?: string;
    local_llm_custom_filename?: string;
}

type LocalModelsStatus = {
    ready?: boolean;
    stt_models?: string[];
    tts_models?: string[];
    llm_models?: string[];
    stt_backends?: Record<string, string[]>;
    tts_backends?: Record<string, string[]>;
    status?: { stt_ready?: boolean; llm_ready?: boolean; tts_ready?: boolean };
};

const Wizard = () => {
    const navigate = useNavigate();
    const { confirm } = useConfirmDialog();
    const [step, setStep] = useState(1);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [showAdvancedKokoro, setShowAdvancedKokoro] = useState(false);

    const [config, setConfig] = useState<SetupConfig>({
        provider: 'openai_realtime',
        asterisk_host: '127.0.0.1',
        asterisk_username: 'asterisk',
        asterisk_password: '',
        asterisk_port: 8088,
        asterisk_scheme: 'http',
        asterisk_app: 'asterisk-ai-voice-agent',
        asterisk_server_ip: '',
        asterisk_ssl_verify: true,
        openai_key: '',
        groq_key: '',
        deepgram_key: '',
        google_key: '',
        greeting: 'Hello, how can I help you today?',
        ai_name: 'Asterisk Agent',
        ai_role: 'Helpful Assistant',
        hybrid_llm_provider: 'groq',
        // Defaults
        local_stt_backend: 'vosk',
        local_stt_model: '',
        kroko_embedded: true,
        local_tts_backend: 'piper',
        local_tts_model: '',
        kokoro_mode: 'local',
        kokoro_voice: 'af_heart',
        kokoro_api_base_url: 'https://voice-generator.pages.dev/api/v1',
        local_llm_model: 'phi3_mini',
        local_llm_custom_url: '',
        local_llm_custom_filename: ''
    });

    useEffect(() => {
        if ((config.kokoro_mode || '').toLowerCase() === 'hf') {
            setShowAdvancedKokoro(true);
        }
    }, [config.kokoro_mode]);



    const [showSkipConfirm, setShowSkipConfirm] = useState(false);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' | 'warning' } | null>(null);

    // Helper to detect if asterisk_host is an IP address or hostname
    const isIPAddress = (host: string): boolean => {
        if (!host) return false;
        // IPv4 pattern
        const ipv4Pattern = /^(\d{1,3}\.){3}\d{1,3}$/;
        // IPv6 pattern (simplified)
        const ipv6Pattern = /^([0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}$/;
        return ipv4Pattern.test(host) || ipv6Pattern.test(host);
    };

    // Check if hostname is being used (requires server IP for RTP security)
    const isUsingHostname = !isIPAddress(config.asterisk_host) && config.asterisk_host !== 'localhost';

    const getDialplanProviderOverride = (provider: string): string => {
        const supported = new Set([
            'google_live',
            'openai_realtime',
            'deepgram',
            'local_hybrid',
            'local',
            'elevenlabs_agent',
        ]);
        return supported.has(provider) ? provider : 'openai_realtime';
    };
    const dialplanContextOverride = 'default';
    const dialplanProviderOverride = getDialplanProviderOverride(config.provider);
    const nonLocalDialplanSnippet = `; extensions_custom.conf
[from-ai-agent]
exten => s,1,NoOp(AI Agent Call)
 same => n,Set(AI_CONTEXT=${dialplanContextOverride})
 same => n,Set(AI_PROVIDER=${dialplanProviderOverride})
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()`;

    const showToast = (message: string, type: 'success' | 'error' | 'warning') => {
        setToast({ message, type });
        setTimeout(() => setToast(null), 4000);
    };
    const [engineStatus, setEngineStatus] = useState<{
        running: boolean;
        exists: boolean;
        checked: boolean;
    }>({ running: false, exists: false, checked: false });
    const [startingEngine, setStartingEngine] = useState(false);
    const [reloadingEngine, setReloadingEngine] = useState(false);
    const [startingLocalServer, setStartingLocalServer] = useState(false);
    const localServerPollRef = useRef<{ cancelled: boolean; timeouts: number[]; startedAt: number | null }>({
        cancelled: false,
        timeouts: [],
        startedAt: null
    });
    const [engineProgress, setEngineProgress] = useState<{
        steps: Array<{ name: string; status: string; message: string }>;
        currentStep: string;
    }>({ steps: [], currentStep: '' });

    // Model selection state
    const [selectedLanguage, setSelectedLanguage] = useState<string>('en-US');
    const [availableLanguages, setAvailableLanguages] = useState<{
        languages: Record<string, { stt: string[]; tts: string[]; region: string }>;
        language_names: Record<string, string>;
        region_names: Record<string, string>;
    }>({ languages: {}, language_names: {}, region_names: {} });
    const [modelCatalog, setModelCatalog] = useState<{
        stt: any[];
        tts: any[];
        llm: any[];
    }>({ stt: [], tts: [], llm: [] });

    // Local AI Server state
    const [localAIStatus, setLocalAIStatus] = useState<{
        tier: string;
        tierInfo: any;
        cpuCores: number;
        ramGb: number;
        gpuDetected: boolean;
        modelsReady: boolean;
        existingModels: { stt: string[]; llm: string[]; tts: string[] };
        downloading: boolean;
        downloadOutput: string[];
        downloadProgress: { bytes_downloaded: number; total_bytes: number; percent: number; speed_bps: number; eta_seconds: number | null; current_file: string } | null;
        downloadCompleted: boolean;
        serverStarted: boolean;
        serverLogs: string[];
        serverReady: boolean;
        systemDetected: boolean;
    }>({
        tier: '',
        tierInfo: {},
        cpuCores: 0,
        ramGb: 0,
        gpuDetected: false,
        existingModels: { stt: [] as string[], llm: [] as string[], tts: [] as string[] },
        modelsReady: false,
        systemDetected: false,
        downloading: false,
        downloadOutput: [] as string[],
        downloadProgress: null,
        downloadCompleted: false,
        serverStarted: false,
        serverLogs: [] as string[],
        serverReady: false
    });

    const [modelsStatus, setModelsStatus] = useState<LocalModelsStatus | null>(null);

    // Load existing config from .env on mount
    useEffect(() => {
        const loadExistingConfig = async () => {
            try {
                const res = await axios.get('/api/wizard/load-config');
                if (res.data) {
                    setConfig(prev => ({
                        ...prev,
                        ...res.data,
                        // Keep provider selection if not set in loaded config
                        provider: res.data.provider || prev.provider
                    }));
                }
            } catch (err) {
                // Non-fatal - continue with defaults
                console.log('No existing config found');
            }
        };
        loadExistingConfig();
    }, []);

    // Load available languages and models when reaching local AI step
    useEffect(() => {
        const loadModelsAndLanguages = async () => {
            try {
                const res = await axios.get('/api/wizard/local/available-models');
                if (res.data) {
                    setModelCatalog(res.data.catalog);
                    setAvailableLanguages({
                        languages: res.data.languages,
                        language_names: res.data.language_names,
                        region_names: res.data.region_names
                    });
                }
            } catch (err) {
                console.log('Failed to load model catalog');
            }
        };
        if (step === 3) {
            loadModelsAndLanguages();
        }
    }, [step]);

    const refreshModelsStatus = useCallback(async () => {
        try {
            const res = await axios.get('/api/wizard/local/models-status');
            setModelsStatus(res.data);
            setLocalAIStatus(prev => ({
                ...prev,
                existingModels: {
                    stt: res.data?.stt_models || [],
                    llm: res.data?.llm_models || [],
                    tts: res.data?.tts_models || []
                }
            }));
        } catch {
            // Non-fatal; models status is best-effort (host may not have models dir yet).
        }
    }, []);

    useEffect(() => {
        if (step !== 3) return;
        if (config.provider !== 'local' && config.provider !== 'local_hybrid') return;
        refreshModelsStatus();
    }, [step, config.provider, refreshModelsStatus]);

    // Auto-select first available model when language changes
    useEffect(() => {
        if (modelCatalog?.stt?.length > 0) {
            const sttModels = modelCatalog.stt.filter((m: any) => 
                m.language === selectedLanguage || m.language === 'multi'
            );
            const ttsModels = modelCatalog.tts.filter((m: any) => 
                m.language === selectedLanguage || m.language === 'multi'
            );
            
            // Auto-select first STT model for the language
            if (sttModels.length > 0 && !sttModels.find((m: any) => m.id === config.local_stt_model)) {
                setConfig(prev => ({
                    ...prev,
                    local_stt_model: sttModels[0].id,
                    local_stt_backend: sttModels[0].backend
                }));
            }
            
            // Auto-select first TTS model for the language
            if (ttsModels.length > 0 && !ttsModels.find((m: any) => m.id === config.local_tts_model)) {
                setConfig(prev => ({
                    ...prev,
                    local_tts_model: ttsModels[0].id,
                    local_tts_backend: ttsModels[0].backend
                }));
            }
        }
    }, [selectedLanguage, modelCatalog]);

    const pickRecommendedLlmId = () => {
        const llms = (modelCatalog?.llm || []).filter((m: any) => !m.requires_api_key);
        if (!llms.length) return 'phi3_mini';

        const tier = (localAIStatus.tier || '').toUpperCase();
        const hasGpu = !!localAIStatus.gpuDetected;

        // Prefer explicitly system-recommended models first.
        const recommended = llms.filter((m: any) => m.system_recommended || m.recommended);

        // Lightweight tier: prefer smaller models for responsiveness.
        if (tier.includes('LIGHT') || (!hasGpu && localAIStatus.ramGb > 0 && localAIStatus.ramGb < 12)) {
            const tiny = llms.find((m: any) => m.id === 'tinyllama');
            if (tiny) return tiny.id;
        }

        // CPU tiers: Phi-3 is the default.
        const phi3 = llms.find((m: any) => m.id === 'phi3_mini');
        if (!hasGpu && phi3) return phi3.id;

        // GPU tiers: prefer a larger option if available, otherwise Phi-3.
        const llama32 = llms.find((m: any) => m.id === 'llama32_3b');
        if (hasGpu && llama32) return llama32.id;

        if (phi3) return phi3.id;
        return (recommended[0]?.id || llms[0]?.id || 'phi3_mini');
    };

    const handleSkip = () => {
        setShowSkipConfirm(true);
    };

    const confirmSkip = async () => {
        try {
            await axios.post('/api/wizard/skip');
            navigate('/');
        } catch (err: any) {
            setError('Failed to skip setup: ' + err.message);
            setShowSkipConfirm(false);
        }
    };

    const handleTestConnection = async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await axios.post('/api/wizard/validate-connection', {
                host: config.asterisk_host,
                username: config.asterisk_username,
                password: config.asterisk_password,
                port: config.asterisk_port,
                scheme: config.asterisk_scheme,
                ssl_verify: config.asterisk_ssl_verify !== false
            });
            if (!res.data.valid) {
                throw new Error(res.data.error || 'Connection failed');
            }
            showToast(res.data.message || 'Successfully connected to Asterisk!', 'success');
        } catch (err: any) {
            setError('Connection failed: ' + (err.response?.data?.error || err.message));
        } finally {
            setLoading(false);
        }
    };

    const handleTestKey = async (provider: string, key: string, agentId?: string) => {
        if (!key) {
            setError(`${provider} API Key is required`);
            return;
        }
        setLoading(true);
        setError(null);
        try {
            const payload: any = {
                provider: provider === 'openai_realtime' ? 'openai' : provider,
                api_key: key
            };
            // Include agent_id for ElevenLabs Conversational AI
            if (provider === 'elevenlabs' && agentId) {
                payload.agent_id = agentId;
            }
            const res = await axios.post('/api/wizard/validate-key', payload);
            if (!res.data.valid) throw new Error(`${provider} Key Invalid: ${res.data.error}`);

            // Show detailed message from backend (includes model availability for Google)
            showToast(res.data.message || `${provider} API Key is valid!`, 'success');
        } catch (err: any) {
            showToast(err.message, 'error');
        } finally {
            setLoading(false);
        }
    };

    const verifyLocalAIHealth = async () => {
        try {
            const res = await axios.get('/api/system/health');
            const status = res.data?.local_ai_server?.status;
            if (status === 'connected') return;

            // Best-effort: start local_ai_server automatically for local_hybrid.
            try {
                await axios.post('/api/wizard/local/start-server');
            } catch (startErr: any) {
                throw new Error(startErr?.response?.data?.message || startErr?.message || 'Failed to start local_ai_server');
            }

            // Poll logs endpoint until ready (or timeout).
            const deadlineMs = Date.now() + 120_000;
            // eslint-disable-next-line no-constant-condition
            while (true) {
                if (Date.now() > deadlineMs) break;
                try {
                    const logRes = await axios.get('/api/wizard/local/server-logs');
                    if (logRes.data?.ready) break;
                } catch {
                    // ignore and retry
                }
                await new Promise((r) => setTimeout(r, 2000));
            }

            const res2 = await axios.get('/api/system/health');
            const status2 = res2.data?.local_ai_server?.status;
            if (status2 !== 'connected') {
                throw new Error(`Local AI Server is not reachable (Status: ${status2}). Please ensure it is running.`);
            }
        } catch (err: any) {
            throw new Error(err?.message || 'Local AI Server health check failed.');
        }
    };

    const startLocalAIServer = useCallback(async () => {
        if (startingLocalServer) return;
        setError(null);
        setStartingLocalServer(true);

        localServerPollRef.current.cancelled = false;
        localServerPollRef.current.timeouts.forEach((id) => clearTimeout(id));
        localServerPollRef.current.timeouts = [];
        localServerPollRef.current.startedAt = Date.now();

        setLocalAIStatus((prev) => ({ ...prev, serverStarted: true, serverReady: false, serverLogs: ['Starting container...'] }));

        let isBuilding = false;
        try {
            const res = await axios.post('/api/wizard/local/start-server');
            if (!res.data.success) {
                throw new Error(res.data.message || 'Failed to start local_ai_server');
            }
            // AAVA-177: Backend signals when a full image build was kicked off
            isBuilding = !!res.data.building;
            if (isBuilding) {
                setLocalAIStatus((prev) => ({
                    ...prev,
                    serverLogs: ['Building Docker image (this can take 10-60 minutes for GPU builds)...'],
                }));
            }
        } catch (err: any) {
            setLocalAIStatus((prev) => ({ ...prev, serverStarted: false, serverReady: false }));
            throw err;
        } finally {
            setStartingLocalServer(false);
        }

        // AAVA-177: Use 60-minute timeout for image builds, 2-minute for normal starts
        const pollTimeoutMs = isBuilding ? 3_600_000 : 120_000;

        const pollLogs = async () => {
            if (localServerPollRef.current.cancelled) return;
            const startedAt = localServerPollRef.current.startedAt || Date.now();
            const elapsed = Date.now() - startedAt;
            if (elapsed >= pollTimeoutMs) return;
            try {
                const logRes = await axios.get('/api/wizard/local/server-logs');
                if (!localServerPollRef.current.cancelled) {
                    setLocalAIStatus((prev) => ({
                        ...prev,
                        serverLogs: logRes.data.logs || [],
                        serverReady: logRes.data.ready
                    }));
                }
                if (!logRes.data.ready) {
                    const id = window.setTimeout(pollLogs, 2000);
                    localServerPollRef.current.timeouts.push(id);
                }
            } catch {
                const id = window.setTimeout(pollLogs, 3000);
                localServerPollRef.current.timeouts.push(id);
            }
        };
        pollLogs();
    }, [startingLocalServer]);

    useEffect(() => {
        return () => {
            localServerPollRef.current.cancelled = true;
            localServerPollRef.current.timeouts.forEach((id) => clearTimeout(id));
            localServerPollRef.current.timeouts = [];
        };
    }, []);

    // Fully Local: auto-start local_ai_server on completion (after models are present).
    useEffect(() => {
        if (step !== 5) return;
        if (config.provider !== 'local') return;
        if (localAIStatus.serverStarted || startingLocalServer) return;
        if (!localAIStatus.modelsReady && !localAIStatus.downloadCompleted) return;

        startLocalAIServer().catch((err: any) => {
            setError(err?.response?.data?.message || err?.message || 'Failed to start local_ai_server');
        });
    }, [
        step,
        config.provider,
        localAIStatus.serverStarted,
        localAIStatus.modelsReady,
        localAIStatus.downloadCompleted,
        startingLocalServer,
        startLocalAIServer
    ]);

    const startSelectedModelsDownload = async (
        options: { skipLlmDownload?: boolean; markDownloaded?: boolean } = {}
    ) => {
        const skipLlmDownload = !!options.skipLlmDownload;
        const markDownloaded = options.markDownloaded !== false;

        setLocalAIStatus(prev => ({ ...prev, downloading: true, downloadOutput: [], downloadProgress: null }));
        setError(null);

        try {
            const startRes = await axios.post('/api/wizard/local/download-selected-models', {
                stt: config.local_stt_backend,
                llm: config.local_llm_model || pickRecommendedLlmId(),
                tts: config.local_tts_backend,
                kroko_embedded: config.kroko_embedded,
                kroko_api_key: config.kroko_api_key,
                kokoro_mode: config.kokoro_mode,
                kokoro_voice: config.kokoro_voice,
                language: selectedLanguage,
                stt_model_id: config.local_stt_model,
                tts_model_id: config.local_tts_model,
                llm_download_url: config.local_llm_custom_url,
                llm_model_path: config.local_llm_custom_filename,
                kokoro_api_base_url: config.kokoro_api_base_url,
                kokoro_api_key: config.kokoro_api_key,
                skip_llm_download: skipLlmDownload
            });

            const jobId = startRes.data?.job_id;
            const diskWarning = startRes.data?.disk_warning;
            if (diskWarning) showToast(diskWarning, 'warning');

            const pollProgress = async () => {
                try {
                    const res = await axios.get('/api/wizard/local/download-progress', {
                        params: jobId ? { job_id: jobId } : undefined
                    });
                    setLocalAIStatus(prev => ({
                        ...prev,
                        downloadOutput: res.data.output || [],
                        downloadProgress: res.data.running
                            ? {
                                  bytes_downloaded: res.data.bytes_downloaded || 0,
                                  total_bytes: res.data.total_bytes || 0,
                                  percent: res.data.percent || 0,
                                  speed_bps: res.data.speed_bps || 0,
                                  eta_seconds: res.data.eta_seconds,
                                  current_file: res.data.current_file || ''
                              }
                            : null
                    }));

                    if (res.data.completed) {
                        setLocalAIStatus(prev => ({
                            ...prev,
                            downloading: false,
                            downloadCompleted: markDownloaded ? true : prev.downloadCompleted,
                            modelsReady: markDownloaded ? true : prev.modelsReady,
                            downloadProgress: null
                        }));
                        refreshModelsStatus();
                        return;
                    }
                    if (res.data.error) {
                        setError('Download failed: ' + res.data.error);
                        setLocalAIStatus(prev => ({ ...prev, downloading: false, downloadProgress: null }));
                        return;
                    }
                    if (res.data.running) {
                        setTimeout(pollProgress, 1000);
                    }
                } catch {
                    setTimeout(pollProgress, 2000);
                }
            };
            pollProgress();
        } catch (err: any) {
            setError('Failed to start download: ' + (err.response?.data?.detail || err.message));
            setLocalAIStatus(prev => ({ ...prev, downloading: false, downloadProgress: null }));
        }
    };

    const localHybridSelectedSttModel = (modelCatalog?.stt || []).find((m: any) => m.id === config.local_stt_model);
    const localHybridSelectedTtsModel = (modelCatalog?.tts || []).find((m: any) => m.id === config.local_tts_model);

    const localHybridSttBackend = (config.local_stt_backend || localHybridSelectedSttModel?.backend || 'vosk').toLowerCase();
    const localHybridTtsBackend = (config.local_tts_backend || localHybridSelectedTtsModel?.backend || 'piper').toLowerCase();

    const localHybridSttNeedsDownload =
        !!localHybridSelectedSttModel?.download_url && !localHybridSelectedSttModel?.auto_download;
    const localHybridTtsNeedsDownload =
        localHybridTtsBackend === 'kokoro'
            ? (config.kokoro_mode || 'local').toLowerCase() === 'local' && !!localHybridSelectedTtsModel?.download_url
            : !!localHybridSelectedTtsModel?.download_url && !localHybridSelectedTtsModel?.auto_download;

    const localHybridSttInstalled = (() => {
        if (!localHybridSttNeedsDownload) return true;
        const modelPath = localHybridSelectedSttModel?.model_path;
        if (!modelPath) return false;
        const backendModels = modelsStatus?.stt_backends?.[localHybridSttBackend] || [];
        return backendModels.includes(modelPath) || backendModels.some((m) => m.includes(modelPath));
    })();

    const localHybridTtsInstalled = (() => {
        if (!localHybridTtsNeedsDownload) return true;
        if (localHybridTtsBackend === 'kokoro') {
            const voices = modelsStatus?.tts_backends?.kokoro || [];
            const voice = (config.kokoro_voice || 'af_heart').toLowerCase();
            return voices.map((v) => v.toLowerCase()).includes(voice);
        }
        const modelPath = localHybridSelectedTtsModel?.model_path;
        if (!modelPath) return false;
        const backendModels = modelsStatus?.tts_backends?.[localHybridTtsBackend] || [];
        return backendModels.includes(modelPath) || backendModels.some((m) => m.includes(modelPath));
    })();

    const localHybridAutoDownloadWarning =
        !!localHybridSelectedSttModel?.auto_download || !!localHybridSelectedTtsModel?.auto_download;

    const localHybridMissingRequired = localHybridSttNeedsDownload && !localHybridSttInstalled
        ? true
        : localHybridTtsNeedsDownload && !localHybridTtsInstalled;

    const handleNext = async () => {
        setError(null);

        // Basic required-field validation for non-technical users
        if (step === 4) {
            const missing: string[] = [];
            if (!config.asterisk_host) missing.push('Asterisk host');
            if (!config.asterisk_username) missing.push('ARI username');
            if (!config.asterisk_password) missing.push('ARI password');

            // Require server IP when using hostname (for RTP security)
            if (isUsingHostname && !config.asterisk_server_ip) {
                missing.push('Asterisk Server IP (required when using hostname)');
            }

            if (missing.length) {
                setError(`${missing.join(', ')} ${missing.length === 1 ? 'is' : 'are'} required.`);
                return;
            }

            // Validate server IP format if provided
            if (config.asterisk_server_ip && !isIPAddress(config.asterisk_server_ip)) {
                setError('Asterisk Server IP must be a valid IP address (e.g., 192.168.1.100)');
                return;
            }

            // Provider key requirement for selected provider
            if (config.provider === 'openai_realtime' && !config.openai_key) {
                setError('OpenAI API key is required for OpenAI Realtime.');
                return;
            }
            if (config.provider === 'deepgram') {
                if (!config.deepgram_key) {
                    setError('Deepgram API key is required for Deepgram.');
                    return;
                }
                if (!config.openai_key) {
                    setError('OpenAI API key is required for Deepgram Think stage.');
                    return;
                }
            }
            if (config.provider === 'google_live' && !config.google_key) {
                setError('Google API key is required for Google Live.');
                return;
            }
            if (config.provider === 'local_hybrid') {
                const llmProvider = (config.hybrid_llm_provider || 'openai').toLowerCase();
                if (llmProvider === 'openai' && !config.openai_key) {
                    setError('OpenAI API key is required for Local Hybrid when using OpenAI.');
                    return;
                }
                if (llmProvider === 'groq' && !config.groq_key) {
                    setError('Groq API key is required for Local Hybrid when using Groq.');
                    return;
                }
            }
            if (config.provider === 'elevenlabs_agent') {
                if (!config.elevenlabs_key) {
                    setError('ElevenLabs API key is required.');
                    return;
                }
                if (!config.elevenlabs_agent_id) {
                    setError('ElevenLabs Agent ID is required.');
                    return;
                }
            }
        }

        if (step === 3) {
            if (config.provider === 'local_hybrid' && localHybridMissingRequired) {
                setError('Please download the selected local STT/TTS models before continuing.');
                return;
            }
            if (config.provider === 'local_hybrid' && localHybridAutoDownloadWarning) {
                showToast(
                    'Some selected local models auto-download on first use. You can continue, but the first call may take longer and requires internet access.',
                    'warning'
                );
            }

            // Validate keys before proceeding
            setLoading(true);
            try {
                if (config.provider === 'openai_realtime') {
                    if (config.openai_key) {
                        const res = await axios.post('/api/wizard/validate-key', {
                            provider: 'openai',
                            api_key: config.openai_key
                        });
                        if (!res.data.valid) throw new Error(`OpenAI Key Invalid: ${res.data.error}`);
                    } else {
                        throw new Error('OpenAI API Key is required for OpenAI Realtime provider');
                    }
                }

                if (config.provider === 'local_hybrid') {
                    const llmProvider = (config.hybrid_llm_provider || 'openai').toLowerCase();
                    if (llmProvider === 'openai') {
                        if (config.openai_key) {
                            const res = await axios.post('/api/wizard/validate-key', {
                                provider: 'openai',
                                api_key: config.openai_key
                            });
                            if (!res.data.valid) throw new Error(`OpenAI Key Invalid: ${res.data.error}`);
                        } else {
                            throw new Error('OpenAI API Key is required for Local Hybrid when using OpenAI');
                        }
                    } else if (llmProvider === 'groq') {
                        if (config.groq_key) {
                            const res = await axios.post('/api/wizard/validate-key', {
                                provider: 'groq',
                                api_key: config.groq_key
                            });
                            if (!res.data.valid) throw new Error(`Groq Key Invalid: ${res.data.error}`);
                        } else {
                            throw new Error('Groq API Key is required for Local Hybrid when using Groq');
                        }
                    }
                }

                if (config.provider === 'deepgram') {
                    // Deepgram requires both Deepgram key AND OpenAI key (for Think stage)
                    if (config.deepgram_key) {
                        const res = await axios.post('/api/wizard/validate-key', {
                            provider: 'deepgram',
                            api_key: config.deepgram_key
                        });
                        if (!res.data.valid) throw new Error(`Deepgram Key Invalid: ${res.data.error}`);
                    } else {
                        throw new Error('Deepgram API Key is required for Deepgram provider');
                    }
                    // Also validate OpenAI key for Think stage
                    if (config.openai_key) {
                        const res = await axios.post('/api/wizard/validate-key', {
                            provider: 'openai',
                            api_key: config.openai_key
                        });
                        if (!res.data.valid) throw new Error(`OpenAI Key Invalid (for Think stage): ${res.data.error}`);
                    } else {
                        throw new Error('OpenAI API Key is required for Deepgram Think stage');
                    }
                }

                if (config.provider === 'google_live') {
                    if (config.google_key) {
                        const res = await axios.post('/api/wizard/validate-key', {
                            provider: 'google',
                            api_key: config.google_key
                        });
                        if (!res.data.valid) throw new Error(`Google Key Invalid: ${res.data.error}`);
                    } else {
                        throw new Error('Google API Key is required for Google Live provider');
                    }
                }

                if (config.provider === 'elevenlabs_agent') {
                    if (!config.elevenlabs_agent_id) {
                        throw new Error('ElevenLabs Agent ID is required');
                    }
                    if (config.elevenlabs_key) {
                        const res = await axios.post('/api/wizard/validate-key', {
                            provider: 'elevenlabs',
                            api_key: config.elevenlabs_key,
                            agent_id: config.elevenlabs_agent_id
                        });
                        if (!res.data.valid) throw new Error(`ElevenLabs Key Invalid: ${res.data.error}`);
                    } else {
                        throw new Error('ElevenLabs API Key is required');
                    }
                }

                // Only verify Local AI health for local_hybrid on step 3
                // For "local" (Full), server is started in step 5 after model download
                if (config.provider === 'local_hybrid') {
                    await verifyLocalAIHealth();
                }

                setStep(step + 1);
            } catch (err: any) {
                setError(err.message);
            } finally {
                setLoading(false);
            }
        } else if (step === 4) {
            // Validate ARI fields
            if (!config.asterisk_host) {
                setError('Asterisk Host is required');
                return;
            }
            if (!config.asterisk_username) {
                setError('ARI Username is required');
                return;
            }
            if (!config.asterisk_password) {
                setError('ARI Password is required');
                return;
            }

            // Validate secret strength (basic check)
            if (config.asterisk_password.length < 8) {
                setError('ARI Password must be at least 8 characters long');
                return;
            }

            // Health Check for Local Hybrid Provider only
            // Note: For "local" (Full) provider, server is started in step 5
            if (config.provider === 'local_hybrid') {
                setLoading(true);
                try {
                    await verifyLocalAIHealth();
                } catch (err: any) {
                    setError(err?.message || 'Local AI Server health check failed.');
                    setLoading(false);
                    return;
                }
                setLoading(false);
            }
            // Save config
            setLoading(true);
            try {
                await axios.post('/api/wizard/save', config);

                // Check engine status for completion step
                try {
                    const statusRes = await axios.get('/api/wizard/engine-status');
                    setEngineStatus({
                        running: statusRes.data.running,
                        exists: statusRes.data.exists,
                        checked: true
                    });
                } catch {
                    setEngineStatus({ running: false, exists: false, checked: true });
                }

                setStep(5); // Go to completion step
            } catch (err: any) {
                setError(err.response?.data?.detail || err.message);
            } finally {
                setLoading(false);
            }
        } else if (step === 2) {
            // Initialize .env when moving from provider selection to API keys step
            try {
                await axios.post('/api/wizard/init-env');
            } catch {
                // Non-fatal - continue anyway
            }
            setStep(step + 1);
        } else {
            setStep(step + 1);
        }
    };

    const ProviderCard = ({ id, title, description, icon: Icon, recommended = false }: any) => (
        <div
            onClick={() => setConfig({ ...config, provider: id, hybrid_llm_provider: id === 'local_hybrid' ? (config.hybrid_llm_provider || 'groq') : config.hybrid_llm_provider })}
            className={`relative p-6 rounded-lg border-2 cursor-pointer transition-all ${config.provider === id
                ? 'border-primary bg-primary/5'
                : 'border-border hover:border-primary/50'
                }`}
        >
            {recommended && (
                <div className="absolute -top-3 left-4 bg-primary text-primary-foreground text-xs px-2 py-1 rounded-full">
                    Recommended
                </div>
            )}
            <div className="flex items-start space-x-4">
                <div className={`p-2 rounded-lg ${config.provider === id ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'}`}>
                    <Icon className="w-6 h-6" />
                </div>
                <div>
                    <h3 className="font-semibold text-lg">{title}</h3>
                    <p className="text-sm text-muted-foreground mt-1">{description}</p>
                </div>
            </div>
        </div>
    );

    return (
        <div className="min-h-screen bg-background flex items-center justify-center p-4">
            <div className="max-w-3xl w-full bg-card border border-border rounded-lg shadow-lg p-8">
                <div className="mb-8 flex justify-between items-start">
                    <div>
                        <h1 className="text-3xl font-bold text-foreground mb-2">Setup Wizard</h1>
                        <div className="flex items-center space-x-2 text-sm text-muted-foreground overflow-x-auto">
                            <span className={step >= 1 ? "text-primary font-medium whitespace-nowrap" : "whitespace-nowrap"}>1. Welcome</span>
                            <span>&rarr;</span>
                            <span className={step >= 2 ? "text-primary font-medium whitespace-nowrap" : "whitespace-nowrap"}>2. Provider</span>
                            <span>&rarr;</span>
                            <span className={step >= 3 ? "text-primary font-medium whitespace-nowrap" : "whitespace-nowrap"}>3. API Keys</span>
                            <span>&rarr;</span>
                            <span className={step >= 4 ? "text-primary font-medium whitespace-nowrap" : "whitespace-nowrap"}>4. Config</span>
                            {step === 5 && (
                                <>
                                    <span>&rarr;</span>
                                    <span className="text-primary font-medium whitespace-nowrap">5. Done</span>
                                </>
                            )}
                        </div>
                    </div>
                    {step === 1 && (
                        <button
                            type="button"
                            onClick={handleSkip}
                            className="text-sm text-muted-foreground hover:text-foreground flex items-center"
                        >
                            <SkipForward className="w-4 h-4 mr-1" />
                            Skip Setup
                        </button>
                    )}
                </div>

                {error && (
                    <div className="mb-6 p-4 bg-destructive/10 border border-destructive/20 rounded-md flex items-center text-destructive">
                        <AlertCircle className="w-5 h-5 mr-2" />
                        {error}
                    </div>
                )}

                {step === 1 && (
                    <div className="space-y-6">
                        <div className="prose dark:prose-invert">
                            <p className="text-lg">Welcome to the Asterisk AI Voice Agent setup.</p>
                            <p>This wizard will help you configure the essential settings to get your agent up and running in minutes.</p>
                            <div className="bg-muted p-4 rounded-lg">
                                <h3 className="font-medium mb-2">You will need:</h3>
                                <ul className="list-disc list-inside space-y-1">
                                    <li>API Keys (OpenAI, Deepgram, or Google)</li>
                                    <li>Asterisk Connection Details (Host, Username, Password)</li>
                                </ul>
                            </div>
                        </div>
                    </div>
                )}

                {step === 2 && (
                    <div className="space-y-4">
                        <h2 className="text-xl font-semibold mb-4">Select Your AI Provider</h2>
                        <div className="grid gap-4">
                            <ProviderCard
                                id="google_live"
                                title="Google Gemini Live"
                                description="Real-time bidirectional streaming. Native audio processing, ultra-low latency (<1s)."
                                icon={Zap}
                                recommended={true}
                            />
                            <ProviderCard
                                id="openai_realtime"
                                title="OpenAI Realtime"
                                description="Fastest setup, natural conversations. Uses OpenAI's Realtime API for low-latency voice interactions."
                                icon={Cloud}
                            />
                            <ProviderCard
                                id="deepgram"
                                title="Deepgram Voice Agent"
                                description="Enterprise-grade with 'Think' stage. Best for complex queries and high reliability."
                                icon={Server}
                            />
                            <ProviderCard
                                id="local_hybrid"
                                title="Local Hybrid"
                                description="Privacy-focused. Audio stays local (STT/TTS), only text is sent to cloud LLM."
                                icon={Shield}
                            />
                            <ProviderCard
                                id="local"
                                title="Local (Full)"
                                description="100% on-premises. All processing stays local - STT, LLM, and TTS. No API keys required."
                                icon={HardDrive}
                            />
                            <ProviderCard
                                id="elevenlabs_agent"
                                title="ElevenLabs Conversational"
                                description="High-quality voices with pre-configured agent. Configure voice, prompt, and tools in ElevenLabs dashboard."
                                icon={Cloud}
                            />
                        </div>
                    </div>
                )}

                {step === 3 && (
                    <div className="space-y-4">
                        <h2 className="text-xl font-semibold mb-4">Configure API Keys</h2>

                        {config.provider === 'openai_realtime' && (
                            <div className="space-y-4">
                                <div className="bg-blue-50/50 dark:bg-blue-900/10 p-4 rounded-md border border-blue-100 dark:border-blue-900/20 text-sm text-blue-800 dark:text-blue-300">
                                    <p className="font-semibold mb-1">OpenAI Realtime</p>
                                    <p className="text-blue-700 dark:text-blue-400">Requires an OpenAI API key.</p>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">OpenAI API Key</label>
                                    <div className="flex space-x-2">
                                        <input
                                            type="password"
                                            className="w-full p-2 rounded-md border border-input bg-background"
                                            value={config.openai_key || ''}
                                            onChange={e => setConfig({ ...config, openai_key: e.target.value })}
                                            placeholder="sk-..."
                                        />
                                        <button
                                            type="button"
                                            onClick={() => handleTestKey('openai', config.openai_key || '')}
                                            className="px-3 py-2 rounded-md bg-secondary text-secondary-foreground hover:bg-secondary/80"
                                            disabled={loading}
                                        >
                                            Test
                                        </button>
                                    </div>
                                    <p className="text-xs text-muted-foreground">Required for OpenAI Realtime provider.</p>
                                </div>
                            </div>
                        )}

                        {config.provider === 'local_hybrid' && (
                            <div className="space-y-4">
	                                <div className="space-y-6 border-b pb-6 mb-6">
	                                    <h3 className="font-medium text-lg">Local AI Configuration</h3>

	                                    <div className="space-y-2">
	                                        <label className="text-sm font-medium">Language</label>
	                                        <select
	                                            className="w-full p-2 rounded-md border border-input bg-background"
	                                            value={selectedLanguage}
	                                            onChange={e => setSelectedLanguage(e.target.value)}
	                                        >
	                                            {Object.keys(availableLanguages.languages || {}).length === 0 && (
	                                                <option value="en-US">en-US</option>
	                                            )}
	                                            {Object.keys(availableLanguages.languages || {})
	                                                .sort()
	                                                .map((lang) => (
	                                                    <option key={lang} value={lang}>
	                                                        {availableLanguages.language_names?.[lang] || lang}
	                                                    </option>
	                                                ))}
	                                        </select>
	                                        <p className="text-xs text-muted-foreground">
	                                            Used to filter available local STT/TTS models.
	                                        </p>
	                                    </div>

	                                    {/* STT Config */}
                                    <div className="space-y-3 p-4 bg-muted/30 rounded-lg border">
                                        <h4 className="font-medium text-sm text-muted-foreground uppercase tracking-wider">Speech-to-Text (STT)</h4>
                                        <div className="grid grid-cols-2 gap-4">
                                            <div>
                                                <label className="text-sm font-medium">Backend</label>
	                                                <select
	                                                    className="w-full p-2 rounded-md border border-input bg-background mt-1"
	                                                    value={config.local_stt_backend}
	                                                    onChange={e => {
	                                                        const backend = e.target.value;
	                                                        const candidates = (modelCatalog.stt || []).filter((m: any) => {
	                                                            const langOk = m.language === selectedLanguage || m.language === 'multi';
	                                                            return langOk && (m.backend || '').toLowerCase() === backend.toLowerCase();
	                                                        });
	                                                        setConfig({
	                                                            ...config,
	                                                            local_stt_backend: backend,
	                                                            local_stt_model: candidates[0]?.id || ''
	                                                        });
	                                                    }}
	                                                >
                                                    <option value="vosk">Vosk (Local)</option>
                                                    <option value="kroko">Kroko (Local/Cloud)</option>
                                                    <option value="sherpa">Sherpa (Local)</option>
                                                    <option value="faster_whisper">Faster-Whisper (Local)</option>
                                                </select>
                                            </div>
                                            {config.local_stt_backend === 'kroko' && (
                                                <div className="flex items-center pt-6">
                                                    <label className="flex items-center space-x-2 cursor-pointer">
                                                        <input
                                                            type="checkbox"
                                                            checked={config.kroko_embedded}
                                                            onChange={e => setConfig({ ...config, kroko_embedded: e.target.checked })}
                                                            className="rounded border-gray-300"
                                                        />
                                                        <span className="text-sm">Embedded Mode (Local)</span>
                                                    </label>
                                                </div>
                                            )}
                                        </div>
	                                        {config.local_stt_backend === 'kroko' && !config.kroko_embedded && (
	                                            <div>
	                                                <label className="text-sm font-medium">Kroko API Key</label>
	                                                <input
	                                                    type="password"
	                                                    className="w-full p-2 rounded-md border border-input bg-background mt-1"
	                                                    value={config.kroko_api_key || ''}
	                                                    onChange={e => setConfig({ ...config, kroko_api_key: e.target.value })}
	                                                    placeholder="Kroko API Key"
	                                                />
	                                            </div>
	                                        )}

	                                        <div className="space-y-2">
	                                            <label className="text-sm font-medium">Model</label>
	                                            <div className="flex space-x-2">
	                                                <select
	                                                    className="w-full p-2 rounded-md border border-input bg-background"
	                                                    value={config.local_stt_model || ''}
	                                                    onChange={e => {
	                                                        const modelId = e.target.value;
	                                                        const picked = (modelCatalog.stt || []).find((m: any) => m.id === modelId);
	                                                        setConfig({
	                                                            ...config,
	                                                            local_stt_model: modelId,
	                                                            local_stt_backend: picked?.backend || config.local_stt_backend
	                                                        });
	                                                    }}
	                                                >
	                                                    {(() => {
	                                                        const backend = (config.local_stt_backend || 'vosk').toLowerCase();
	                                                        const candidates = (modelCatalog.stt || []).filter((m: any) => {
	                                                            const langOk = m.language === selectedLanguage || m.language === 'multi';
	                                                            return langOk && (m.backend || '').toLowerCase() === backend;
	                                                        });
	                                                        if (!candidates.length) {
	                                                            return <option value="">No catalog models found for {backend}</option>;
	                                                        }
	                                                        return candidates.map((m: any) => (
	                                                            <option key={m.id} value={m.id}>
	                                                                {m.name}
	                                                                {m.size_display ? ` • ${m.size_display}` : ''}
	                                                                {m.auto_download ? ' • Auto-download' : ''}
	                                                            </option>
	                                                        ));
	                                                    })()}
	                                                </select>
	                                                {localHybridSttNeedsDownload && !localHybridSttInstalled && (
	                                                    <button
	                                                        type="button"
	                                                        onClick={() =>
	                                                            startSelectedModelsDownload({ skipLlmDownload: true, markDownloaded: false })
	                                                        }
	                                                        className="px-3 py-2 rounded-md bg-blue-600 text-white hover:bg-blue-700"
	                                                        disabled={localAIStatus.downloading}
	                                                    >
	                                                        Download
	                                                    </button>
	                                                )}
	                                            </div>
	                                            {localHybridSttNeedsDownload && (
	                                                <p className="text-xs text-muted-foreground">
	                                                    {localHybridSttInstalled ? 'Installed.' : 'Not installed yet. Download required before continuing.'}
	                                                </p>
	                                            )}
	                                            {!localHybridSttNeedsDownload && localHybridSelectedSttModel?.auto_download && (
	                                                <p className="text-xs text-amber-700 dark:text-amber-400">
	                                                    This model auto-downloads on first use. You can continue, but the first call may take longer.
	                                                </p>
	                                            )}
	                                        </div>
	                                    </div>

                                    {/* TTS Config */}
                                    <div className="space-y-3 p-4 bg-muted/30 rounded-lg border">
                                        <h4 className="font-medium text-sm text-muted-foreground uppercase tracking-wider">Text-to-Speech (TTS)</h4>
                                        <div className="grid grid-cols-2 gap-4">
                                            <div>
                                                <label className="text-sm font-medium">Backend</label>
	                                                <select
	                                                    className="w-full p-2 rounded-md border border-input bg-background mt-1"
	                                                    value={
	                                                        config.local_tts_backend === 'kokoro'
	                                                            ? (config.kokoro_mode === 'local' ? 'kokoro_local' : 'kokoro_cloud')
	                                                            : config.local_tts_backend
	                                                    }
	                                                    onChange={e => {
	                                                        const val = e.target.value;
	                                                        let nextBackend = val;
	                                                        let nextKokoroMode = config.kokoro_mode;
	                                                        if (val === 'kokoro_local') {
	                                                            nextBackend = 'kokoro';
	                                                            nextKokoroMode = 'local';
	                                                        } else if (val === 'kokoro_cloud') {
	                                                            nextBackend = 'kokoro';
	                                                            nextKokoroMode = 'api';
	                                                        }
	                                                        const candidates = (modelCatalog.tts || []).filter((m: any) => {
	                                                            const langOk = m.language === selectedLanguage || m.language === 'multi';
	                                                            return langOk && (m.backend || '').toLowerCase() === String(nextBackend).toLowerCase();
	                                                        });
	                                                        setConfig({
	                                                            ...config,
	                                                            local_tts_backend: nextBackend,
	                                                            kokoro_mode: nextKokoroMode,
	                                                            local_tts_model: candidates[0]?.id || ''
	                                                        });
	                                                    }}
	                                                >
                                                    <option value="piper">Piper (Local)</option>
                                                    <option value="kokoro_local">Kokoro (Local)</option>
                                                    <option value="kokoro_cloud">Kokoro (Cloud/API)</option>
                                                    <option value="melotts">MeloTTS (Local/CPU)</option>
	                                                </select>
	                                            </div></div>

	                                        <div className="space-y-2">
	                                            <label className="text-sm font-medium">Model</label>
	                                            <div className="flex space-x-2">
	                                                <select
	                                                    className="w-full p-2 rounded-md border border-input bg-background"
	                                                    value={config.local_tts_model || ''}
	                                                    onChange={e => {
	                                                        const modelId = e.target.value;
	                                                        const picked = (modelCatalog.tts || []).find((m: any) => m.id === modelId);
	                                                        setConfig({
	                                                            ...config,
	                                                            local_tts_model: modelId,
	                                                            local_tts_backend: picked?.backend || config.local_tts_backend
	                                                        });
	                                                    }}
	                                                    disabled={(config.local_tts_backend || '').toLowerCase() === 'melotts'}
	                                                >
	                                                    {(() => {
	                                                        const backend = (config.local_tts_backend || 'piper').toLowerCase();
	                                                        if (backend === 'melotts') {
	                                                            return <option value="">MeloTTS (no downloadable model)</option>;
	                                                        }
	                                                        const candidates = (modelCatalog.tts || []).filter((m: any) => {
	                                                            const langOk = m.language === selectedLanguage || m.language === 'multi';
	                                                            return langOk && (m.backend || '').toLowerCase() === backend;
	                                                        });
	                                                        if (!candidates.length) {
	                                                            return <option value="">No catalog models found for {backend}</option>;
	                                                        }
	                                                        return candidates.map((m: any) => (
	                                                            <option key={m.id} value={m.id}>
	                                                                {m.name}
	                                                                {m.size_display ? ` • ${m.size_display}` : ''}
	                                                                {m.auto_download ? ' • Auto-download' : ''}
	                                                            </option>
	                                                        ));
	                                                    })()}
	                                                </select>
	                                                {localHybridTtsNeedsDownload && !localHybridTtsInstalled && (
	                                                    <button
	                                                        type="button"
	                                                        onClick={() =>
	                                                            startSelectedModelsDownload({ skipLlmDownload: true, markDownloaded: false })
	                                                        }
	                                                        className="px-3 py-2 rounded-md bg-blue-600 text-white hover:bg-blue-700"
	                                                        disabled={localAIStatus.downloading}
	                                                    >
	                                                        Download
	                                                    </button>
	                                                )}
	                                            </div>
	                                            {localHybridTtsNeedsDownload && (
	                                                <p className="text-xs text-muted-foreground">
	                                                    {localHybridTtsInstalled ? 'Installed.' : 'Not installed yet. Download required before continuing.'}
	                                                </p>
	                                            )}
	                                            {(config.local_tts_backend || '').toLowerCase() === 'kokoro' &&
	                                                (config.kokoro_mode || 'local').toLowerCase() !== 'local' && (
	                                                    <p className="text-xs text-amber-700 dark:text-amber-400">
	                                                        Kokoro in cloud/API mode does not require local model downloads.
	                                                    </p>
	                                                )}
	                                            {!localHybridTtsNeedsDownload && localHybridSelectedTtsModel?.auto_download && (
	                                                <p className="text-xs text-amber-700 dark:text-amber-400">
	                                                    This model auto-downloads on first use. You can continue, but the first call may take longer.
	                                                </p>
	                                            )}
	                                        </div>

	                                        {config.local_tts_backend === 'kokoro' && config.kokoro_mode === 'api' && (
	                                            <div>
	                                                <label className="text-sm font-medium">Kokoro API Key</label>
	                                                <input
                                                    type="password"
                                                    className="w-full p-2 rounded-md border border-input bg-background mt-1"
                                                    value={config.kokoro_api_key || ''}
                                                    onChange={e => setConfig({ ...config, kokoro_api_key: e.target.value })}
                                                    placeholder="Kokoro API Key"
                                                />
                                            </div>
                                        )}
	                                        {config.local_tts_backend === 'kokoro' && config.kokoro_mode === 'local' && (
	                                            <div>
	                                                <label className="text-sm font-medium">Voice</label>
                                                <select
                                                    className="w-full p-2 rounded-md border border-input bg-background mt-1"
                                                    value={config.kokoro_voice || 'af_heart'}
                                                    onChange={e => setConfig({ ...config, kokoro_voice: e.target.value })}
                                                >
                                                    <option value="af_heart">Heart (Female, US)</option>
                                                    <option value="af_bella">Bella (Female, US)</option>
                                                    <option value="af_nicole">Nicole (Female, US)</option>
                                                    <option value="af_sarah">Sarah (Female, US)</option>
                                                    <option value="af_sky">Sky (Female, US)</option>
                                                    <option value="am_adam">Adam (Male, US)</option>
                                                    <option value="am_michael">Michael (Male, US)</option>
                                                    <option value="bf_emma">Emma (Female, UK)</option>
                                                    <option value="bf_isabella">Isabella (Female, UK)</option>
                                                    <option value="bm_george">George (Male, UK)</option>
                                                    <option value="bm_lewis">Lewis (Male, UK)</option>
                                                </select>
                                                <p className="text-xs text-muted-foreground mt-1">
                                                    af=American Female, am=American Male, bf=British Female, bm=British Male
                                                </p>
	                                            </div>
	                                        )}
	                                    </div>

	                                    {(localHybridMissingRequired || localAIStatus.downloading) && (
	                                        <div className="p-4 rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50/70 dark:bg-amber-900/20 space-y-3">
	                                            {localHybridMissingRequired && (
	                                                <p className="text-sm text-amber-900 dark:text-amber-200">
	                                                    Selected local STT/TTS models are not installed yet. Download them before continuing.
	                                                </p>
	                                            )}
	                                            {localAIStatus.downloading && (
	                                                <p className="text-sm text-amber-900 dark:text-amber-200">
	                                                    Download in progress…
	                                                </p>
	                                            )}
	                                            <div className="flex justify-end">
	                                                <button
	                                                    type="button"
	                                                    onClick={() =>
	                                                        startSelectedModelsDownload({ skipLlmDownload: true, markDownloaded: false })
	                                                    }
	                                                    className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
	                                                    disabled={localAIStatus.downloading}
	                                                >
	                                                    {localAIStatus.downloading ? 'Downloading…' : 'Download Selected Models'}
	                                                </button>
	                                            </div>
	                                        </div>
	                                    )}

	                                    {/* LLM Config for Hybrid */}
	                                    <div className="space-y-3 p-4 bg-muted/30 rounded-lg border">
                                        <h4 className="font-medium text-sm text-muted-foreground uppercase tracking-wider">Large Language Model (LLM)</h4>
                                        <div>
                                            <label className="text-sm font-medium">Provider</label>
                                            <select
                                                className="w-full p-2 rounded-md border border-input bg-background mt-1"
                                                value={config.hybrid_llm_provider || 'groq'}
                                                onChange={e => setConfig({ ...config, hybrid_llm_provider: e.target.value })}
                                            >
                                                <option value="groq">Groq (Cloud) - Free tier, no credit card</option>
                                                <option value="openai">OpenAI (Cloud)</option>
                                                <option value="ollama">Ollama (Self-hosted) - No API key needed</option>
                                            </select>
                                        </div>
                                        {config.hybrid_llm_provider === 'groq' && (
                                            <div className="p-3 bg-amber-50 dark:bg-amber-900/20 rounded-md border border-amber-200 dark:border-amber-800">
                                                <p className="text-sm text-amber-800 dark:text-amber-300">
                                                    <strong>Note:</strong> Groq does not support function/tool calling reliably.
                                                </p>
                                                <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">
                                                    Tools are allowlisted per Context. If you use Groq as the LLM, keep context tools empty.
                                                </p>
                                            </div>
                                        )}
                                        {config.hybrid_llm_provider === 'ollama' && (
                                            <div className="p-3 bg-blue-50 dark:bg-blue-900/20 rounded-md border border-blue-200 dark:border-blue-800">
                                                <p className="text-sm text-blue-800 dark:text-blue-300">
                                                    <strong>Ollama:</strong> Run your own LLM on a Mac, PC, or server.
                                                </p>
                                                <p className="text-xs text-blue-600 dark:text-blue-400 mt-1">
                                                    Configure the Ollama URL in Providers → ollama_llm after setup.
                                                </p>
                                            </div>
                                        )}
                                    </div>
                                </div>

                                {config.hybrid_llm_provider !== 'ollama' && (
                                    <div className="space-y-2">
                                        <label className="text-sm font-medium">
                                            {config.hybrid_llm_provider === 'groq' ? 'Groq API Key' : 'OpenAI API Key'}
                                            <span className="text-muted-foreground font-normal ml-2">(for LLM only)</span>
                                        </label>
                                        <div className="flex space-x-2">
                                            <input
                                                type="password"
                                                className="w-full p-2 rounded-md border border-input bg-background"
                                                value={config.hybrid_llm_provider === 'groq' ? (config.groq_key || '') : (config.openai_key || '')}
                                                onChange={e => setConfig({ ...config, [config.hybrid_llm_provider === 'groq' ? 'groq_key' : 'openai_key']: e.target.value })}
                                                placeholder={config.hybrid_llm_provider === 'groq' ? 'gsk_...' : 'sk-...'}
                                            />
                                            <button
                                                type="button"
                                                onClick={() => handleTestKey(config.hybrid_llm_provider === 'groq' ? 'groq' : 'openai', config.hybrid_llm_provider === 'groq' ? (config.groq_key || '') : (config.openai_key || ''))}
                                                className="px-3 py-2 rounded-md bg-secondary text-secondary-foreground hover:bg-secondary/80"
                                                disabled={loading}
                                            >
                                                Test
                                            </button>
                                        </div>
                                        <p className="text-xs text-muted-foreground">Required for Local Hybrid cloud LLM.</p>
                                    </div>
                                )}
                            </div>
                        )}

                        {config.provider === 'deepgram' && (
                            <div className="space-y-4">
                                <div className="bg-blue-50/50 dark:bg-blue-900/10 p-4 rounded-md border border-blue-100 dark:border-blue-900/20 text-sm text-blue-800 dark:text-blue-300">
                                    <p className="font-semibold mb-1">Deepgram Voice Agent</p>
                                    <p className="text-blue-700 dark:text-blue-400">
                                        Requires both Deepgram API key (for STT/TTS) and OpenAI API key (for Think stage LLM).
                                    </p>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Deepgram API Key</label>
                                    <div className="flex space-x-2">
                                        <input
                                            type="password"
                                            className="w-full p-2 rounded-md border border-input bg-background"
                                            value={config.deepgram_key}
                                            onChange={e => setConfig({ ...config, deepgram_key: e.target.value })}
                                            placeholder="Token..."
                                        />
                                        <button
                                            type="button"
                                            onClick={() => handleTestKey('deepgram', config.deepgram_key || '')}
                                            className="px-3 py-2 rounded-md bg-secondary text-secondary-foreground hover:bg-secondary/80"
                                            disabled={loading}
                                        >
                                            Test
                                        </button>
                                    </div>
                                    <p className="text-xs text-muted-foreground">For Deepgram STT and TTS.</p>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">OpenAI API Key (for Think stage)</label>
                                    <div className="flex space-x-2">
                                        <input
                                            type="password"
                                            className="w-full p-2 rounded-md border border-input bg-background"
                                            value={config.openai_key}
                                            onChange={e => setConfig({ ...config, openai_key: e.target.value })}
                                            placeholder="sk-..."
                                        />
                                        <button
                                            type="button"
                                            onClick={() => handleTestKey('openai', config.openai_key || '')}
                                            className="px-3 py-2 rounded-md bg-secondary text-secondary-foreground hover:bg-secondary/80"
                                            disabled={loading}
                                        >
                                            Test
                                        </button>
                                    </div>
                                    <p className="text-xs text-muted-foreground">Deepgram's Think stage uses OpenAI for LLM reasoning.</p>
                                </div>
                            </div>
                        )}

                        {config.provider === 'google_live' && (
                            <div className="space-y-4">
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Google API Key</label>
                                    <div className="flex space-x-2">
                                        <input
                                            type="password"
                                            className="w-full p-2 rounded-md border border-input bg-background"
                                            value={config.google_key}
                                            onChange={e => setConfig({ ...config, google_key: e.target.value })}
                                            placeholder="AIza..."
                                        />
                                        <button
                                            type="button"
                                            onClick={() => handleTestKey('google', config.google_key || '')}
                                            className="px-3 py-2 rounded-md bg-secondary text-secondary-foreground hover:bg-secondary/80"
                                            disabled={loading}
                                        >
                                            Test
                                        </button>
                                    </div>
                                    <p className="text-xs text-muted-foreground">Required for Google Gemini Live provider.</p>
                                </div>
                                <div className="bg-blue-50/50 dark:bg-blue-900/10 p-3 rounded-md border border-blue-100 dark:border-blue-900/20 text-xs text-blue-700 dark:text-blue-400">
                                    <p className="font-semibold mb-1">Using Google Cloud / Vertex AI?</p>
                                    <p>For enterprise GCP deployments, Vertex AI offers GA models with improved function calling reliability. Configure Vertex AI via the <strong>Providers page</strong> after setup — it uses service account authentication instead of an API key.</p>
                                </div>
                            </div>
                        )}

                        {config.provider === 'elevenlabs_agent' && (
                            <div className="space-y-4">
                                <div className="bg-blue-50/50 dark:bg-blue-900/10 p-4 rounded-md border border-blue-100 dark:border-blue-900/20 text-sm text-blue-800 dark:text-blue-300">
                                    <p className="font-semibold mb-1">ElevenLabs Conversational AI</p>
                                    <p className="text-blue-700 dark:text-blue-400">
                                        This provider uses a pre-configured agent from your ElevenLabs dashboard.
                                        Voice, system prompt, and LLM model are configured there.
                                    </p>
                                </div>

                                <div className="space-y-2">
                                    <label className="text-sm font-medium">
                                        Agent ID
                                        <span className="text-destructive ml-1">*</span>
                                    </label>
                                    <input
                                        type="text"
                                        className="w-full p-2 rounded-md border border-input bg-background font-mono text-sm"
                                        value={config.elevenlabs_agent_id}
                                        onChange={e => setConfig({ ...config, elevenlabs_agent_id: e.target.value })}
                                        placeholder="agent_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                                    />
                                    <p className="text-xs text-muted-foreground">
                                        Get this from{' '}
                                        <a href="https://elevenlabs.io/app/agents" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">
                                            elevenlabs.io/app/agents
                                        </a>
                                    </p>
                                </div>

                                <div className="space-y-2">
                                    <label className="text-sm font-medium">ElevenLabs API Key</label>
                                    <div className="flex space-x-2">
                                        <input
                                            type="password"
                                            className="w-full p-2 rounded-md border border-input bg-background"
                                            value={config.elevenlabs_key}
                                            onChange={e => setConfig({ ...config, elevenlabs_key: e.target.value })}
                                            placeholder="xi-..."
                                        />
                                        <button
                                            type="button"
                                            onClick={() => handleTestKey('elevenlabs', config.elevenlabs_key || '', config.elevenlabs_agent_id)}
                                            className="px-3 py-2 rounded-md bg-secondary text-secondary-foreground hover:bg-secondary/80"
                                            disabled={loading}
                                        >
                                            Test
                                        </button>
                                    </div>
                                    <p className="text-xs text-muted-foreground">Required for ElevenLabs Conversational provider.</p>
                                </div>

                                        <div className="bg-amber-50/50 dark:bg-amber-900/10 p-4 rounded-md border border-amber-100 dark:border-amber-900/20">
                                            <h4 className="font-semibold mb-2 text-amber-800 dark:text-amber-300 text-sm">Setup Requirements</h4>
                                            <ul className="text-xs text-amber-700 dark:text-amber-400 space-y-1 list-disc list-inside">
                                                <li>Create an agent at elevenlabs.io/app/agents</li>
                                                <li>Enable "Require authentication" in security settings</li>
                                                <li>Add client tools (hangup_call, blind_transfer, etc.)</li>
                                            </ul>
                                        </div>
                                    </div>
                                )}

                        {config.provider === 'local' && (
                            <div className="space-y-6">
                                <div className="bg-green-50/50 dark:bg-green-900/10 p-4 rounded-md border border-green-100 dark:border-green-900/20">
                                    <p className="font-semibold mb-2 flex items-center gap-2 text-green-800 dark:text-green-300">
                                        <HardDrive className="w-4 h-4" />
                                        Local AI Server Setup
                                    </p>
                                    <p className="text-sm text-green-700 dark:text-green-400 mb-3">
                                        Local (Full) mode runs entirely on your infrastructure. No API keys required.
                                    </p>
                                </div>

                                {/* System Detection */}
                                <div className="bg-muted p-4 rounded-lg">
                                    <div className="flex justify-between items-center mb-3">
                                        <h4 className="font-medium">System Detection</h4>
                                        <button
                                            onClick={async () => {
                                                setLoading(true);
                                                try {
                                                    const [tierRes, modelsRes] = await Promise.all([
                                                        axios.get('/api/wizard/local/detect-tier'),
                                                        axios.get('/api/wizard/local/models-status')
                                                    ]);
                                                    setLocalAIStatus(prev => ({
                                                        ...prev,
                                                        tier: tierRes.data.tier,
                                                        tierInfo: tierRes.data.tier_info,
                                                        cpuCores: tierRes.data.cpu_cores,
                                                        ramGb: tierRes.data.ram_gb,
                                                        gpuDetected: tierRes.data.gpu_detected,
                                                        existingModels: {
                                                            stt: modelsRes.data.stt_models || [],
                                                            llm: modelsRes.data.llm_models || [],
                                                            tts: modelsRes.data.tts_models || []
                                                        },
                                                        modelsReady: modelsRes.data.ready,
                                                        systemDetected: true
                                                    }));


                                                    // Auto-select recommended models
                                                    setConfig(prev => ({
                                                        ...prev,
                                                        local_stt_backend: 'vosk',
                                                        local_tts_backend: 'piper',
                                                        local_llm_model: pickRecommendedLlmId()
                                                    }));
                                                } catch (err: any) {
                                                    setError('Failed to detect system: ' + err.message);
                                                }
                                                setLoading(false);
                                            }}
                                            disabled={loading}
                                            className="px-3 py-1 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                                        >
                                            {loading ? 'Detecting...' : 'Detect System'}
                                        </button>
                                    </div>

                                    {/* Tier Info */}
                                    {localAIStatus.systemDetected && (
                                        <div className="grid grid-cols-3 gap-4 mb-4 text-sm">
                                            <div className="p-2 bg-background rounded border">
                                                <span className="text-muted-foreground block text-xs">CPU Cores</span>
                                                <span className="font-medium">{localAIStatus.cpuCores}</span>
                                            </div>
                                            <div className="p-2 bg-background rounded border">
                                                <span className="text-muted-foreground block text-xs">RAM</span>
                                                <span className="font-medium">{localAIStatus.ramGb} GB</span>
                                            </div>
                                            <div className="p-2 bg-background rounded border">
                                                <span className="text-muted-foreground block text-xs">GPU</span>
                                                <span className={`font-medium ${localAIStatus.gpuDetected ? 'text-green-500' : 'text-muted-foreground'}`}>
                                                    {localAIStatus.gpuDetected ? 'Detected' : 'Not Detected'}
                                                </span>
                                            </div>
                                        </div>
                                    )}
                                </div>

                                {/* Configuration UI */}
                                <div className="space-y-6 border-t pt-6">
                                    <h3 className="font-medium text-lg">Local AI Configuration</h3>

                                    {/* Language Selection */}
                                    <div className="space-y-3 p-4 bg-blue-50 dark:bg-blue-900/20 rounded-lg border border-blue-200 dark:border-blue-800">
                                        <h4 className="font-medium text-sm text-blue-700 dark:text-blue-300 uppercase tracking-wider flex items-center gap-2">
                                            🌍 Language Selection
                                        </h4>
                                        <p className="text-sm text-muted-foreground">
                                            Choose your preferred language. STT and TTS models will be filtered accordingly.
                                        </p>
                                        <div className="grid grid-cols-2 gap-4">
                                            <div>
                                                <label className="text-sm font-medium">Primary Language</label>
                                                <select
                                                    className="w-full p-2 rounded-md border border-input bg-background mt-1"
                                                    value={selectedLanguage}
                                                    onChange={e => setSelectedLanguage(e.target.value)}
                                                >
                                                    <optgroup label="🌟 Popular">
                                                        <option value="en-US">English (US)</option>
                                                        <option value="en-GB">English (UK)</option>
                                                        <option value="es-ES">Spanish</option>
                                                        <option value="fr-FR">French</option>
                                                        <option value="de-DE">German</option>
                                                    </optgroup>
                                                    <optgroup label="🇪🇺 European">
                                                        <option value="it-IT">Italian</option>
                                                        <option value="pt-BR">Portuguese (Brazil)</option>
                                                        <option value="nl-NL">Dutch</option>
                                                        <option value="ru-RU">Russian</option>
                                                        <option value="pl-PL">Polish</option>
                                                        <option value="uk-UA">Ukrainian</option>
                                                        <option value="cs-CZ">Czech</option>
                                                        <option value="sv-SE">Swedish</option>
                                                        <option value="el-GR">Greek</option>
                                                        <option value="tr-TR">Turkish</option>
                                                        <option value="da-DK">Danish</option>
                                                        <option value="fi-FI">Finnish</option>
                                                        <option value="hu-HU">Hungarian</option>
                                                        <option value="no-NO">Norwegian</option>
                                                    </optgroup>
                                                    <optgroup label="🌏 Asian">
                                                        <option value="zh-CN">Chinese (Mandarin)</option>
                                                        <option value="ja-JP">Japanese</option>
                                                        <option value="ko-KR">Korean</option>
                                                        <option value="hi-IN">Hindi</option>
                                                        <option value="vi-VN">Vietnamese</option>
                                                    </optgroup>
                                                    <optgroup label="🌍 Other">
                                                        <option value="ar">Arabic</option>
                                                        <option value="fa-IR">Farsi/Persian</option>
                                                        <option value="sw">Swahili</option>
                                                    </optgroup>
                                                </select>
                                            </div>
                                            <div className="flex items-end">
                                                <p className="text-xs text-muted-foreground">
                                                    {availableLanguages.languages[selectedLanguage] ? (
                                                        <>
                                                            <span className="text-green-600 dark:text-green-400">✓</span> {availableLanguages.languages[selectedLanguage]?.stt?.length || 0} STT models, {availableLanguages.languages[selectedLanguage]?.tts?.length || 0} TTS voices available
                                                        </>
                                                    ) : (
                                                        'Loading...'
                                                    )}
                                                </p>
                                            </div>
                                        </div>
                                    </div>

                                    {/* STT Config */}
                                    <div className="space-y-3 p-4 bg-muted/30 rounded-lg border">
                                        <h4 className="font-medium text-sm text-muted-foreground uppercase tracking-wider">Speech-to-Text (STT)</h4>
                                        <div className="grid grid-cols-2 gap-4">
                                            <div>
                                                <label className="text-sm font-medium">Model</label>
                                                <select
                                                    className="w-full p-2 rounded-md border border-input bg-background mt-1"
                                                    value={config.local_stt_model || config.local_stt_backend}
                                                    onChange={e => {
                                                        const val = e.target.value;
                                                        const model = modelCatalog?.stt?.find((m: any) => m.id === val);
                                                        if (model) {
                                                            setConfig({ 
                                                                ...config, 
                                                                local_stt_backend: model.backend,
                                                                local_stt_model: model.id,
                                                                kroko_embedded: model.backend === 'kroko' && model.embedded === true
                                                            });
                                                        } else if (val === 'kroko_cloud') {
                                                            setConfig({ ...config, local_stt_backend: 'kroko', local_stt_model: val, kroko_embedded: false });
                                                        }
                                                    }}
                                                >
                                                    {/* Language-specific models */}
                                                    {modelCatalog?.stt?.filter((m: any) => 
                                                        m.language === selectedLanguage || m.language === 'multi'
                                                    ).map((model: any) => (
                                                        <option key={model.id} value={model.id}>
                                                            {model.name} ({model.backend}) - {model.size_display}
                                                        </option>
                                                    ))}
                                                    {/* Fallback if no models for language */}
                                                    {(!modelCatalog?.stt || modelCatalog.stt.filter((m: any) => 
                                                        m.language === selectedLanguage || m.language === 'multi'
                                                    ).length === 0) && (
                                                        <>
                                                            <option value="vosk">Vosk (Local)</option>
                                                            <option value="kroko_cloud">Kroko (Cloud)</option>
                                                            <option value="faster_whisper">Faster-Whisper (Local)</option>
                                                        </>
                                                    )}
                                                </select>
                                                <p className="text-xs text-muted-foreground mt-1">
                                                    Models filtered for {availableLanguages.language_names?.[selectedLanguage] || selectedLanguage}
                                                </p>
                                            </div>
                                        </div>
                                        {config.local_stt_backend === 'kroko' && !config.kroko_embedded && (
                                            <div>
                                                <label className="text-sm font-medium">Kroko API Key</label>
                                                <input
                                                    type="password"
                                                    className="w-full p-2 rounded-md border border-input bg-background mt-1"
                                                    value={config.kroko_api_key || ''}
                                                    onChange={e => setConfig({ ...config, kroko_api_key: e.target.value })}
                                                    placeholder="Kroko API Key"
                                                />
                                            </div>
                                        )}
                                    </div>

	                                    {/* TTS Config */}
	                                    <div className="space-y-3 p-4 bg-muted/30 rounded-lg border">
	                                        <h4 className="font-medium text-sm text-muted-foreground uppercase tracking-wider">Text-to-Speech (TTS)</h4>
	                                        <div className="grid grid-cols-2 gap-4">
	                                            <div>
	                                                <label className="text-sm font-medium">Voice / Model</label>
	                                                <select
	                                                    className="w-full p-2 rounded-md border border-input bg-background mt-1"
	                                                    value={config.local_tts_model || config.local_tts_backend}
	                                                    onChange={e => {
	                                                        const val = e.target.value;
	                                                        const model = modelCatalog?.tts?.find((m: any) => m.id === val);
	                                                        if (model) {
	                                                            setConfig({ 
	                                                                ...config, 
	                                                                local_tts_backend: model.backend,
	                                                                local_tts_model: model.id,
	                                                            });
	                                                        }
	                                                    }}
	                                                >
                                                    {/* Language-specific voices */}
                                                    {modelCatalog?.tts?.filter((m: any) => 
                                                        m.language === selectedLanguage || m.language === 'multi'
                                                    ).map((model: any) => (
                                                        <option key={model.id} value={model.id}>
                                                            {model.name} ({model.backend}) - {model.size_display}
                                                        </option>
                                                    ))}
                                                    {/* Fallback if no models for language */}
                                                    {(!modelCatalog?.tts || modelCatalog.tts.filter((m: any) => 
                                                        m.language === selectedLanguage || m.language === 'multi'
                                                    ).length === 0) && (
                                                        <>
                                                            <option value="piper">Piper (Local)</option>
                                                            <option value="kokoro">Kokoro (Premium)</option>
                                                            <option value="melotts">MeloTTS (Local)</option>
                                                        </>
                                                    )}
                                                </select>
	                                                <p className="text-xs text-muted-foreground mt-1">
	                                                    Voices filtered for {availableLanguages.language_names?.[selectedLanguage] || selectedLanguage}
	                                                </p>
	                                            </div>
	                                            {config.local_tts_backend === 'kokoro' && (
	                                                <div>
	                                                    <label className="text-sm font-medium">Kokoro Mode</label>
	                                                    <select
	                                                        className="w-full p-2 rounded-md border border-input bg-background mt-1"
	                                                        value={(config.kokoro_mode || 'local').toLowerCase()}
	                                                        onChange={e => setConfig({ ...config, kokoro_mode: e.target.value })}
	                                                    >
	                                                        <option value="local">Local (downloaded files)</option>
	                                                        <option value="api">Cloud/API (remote endpoint)</option>
	                                                        {(showAdvancedKokoro || (config.kokoro_mode || '').toLowerCase() === 'hf') && (
	                                                            <option value="hf">HuggingFace (auto-download, Advanced)</option>
	                                                        )}
	                                                    </select>
	                                                    <label className="flex items-center space-x-2 cursor-pointer mt-2">
	                                                        <input
	                                                            type="checkbox"
	                                                            checked={showAdvancedKokoro}
	                                                            onChange={e => setShowAdvancedKokoro(e.target.checked)}
	                                                            className="rounded border-gray-300"
	                                                        />
	                                                        <span className="text-sm text-muted-foreground">Show advanced modes</span>
	                                                    </label>
	                                                </div>
	                                            )}
	                                            {config.local_tts_backend === 'kokoro' && ['local', 'hf'].includes((config.kokoro_mode || 'local').toLowerCase()) && (
	                                                <div>
	                                                    <label className="text-sm font-medium">Voice</label>
	                                                    <select
	                                                        className="w-full p-2 rounded-md border border-input bg-background mt-1"
	                                                        value={config.kokoro_voice || 'af_heart'}
	                                                        onChange={e => setConfig({ ...config, kokoro_voice: e.target.value })}
	                                                    >
	                                                        <option value="af_heart">Heart (Female, US)</option>
	                                                        <option value="af_bella">Bella (Female, US)</option>
	                                                        <option value="af_nicole">Nicole (Female, US)</option>
	                                                        <option value="af_sarah">Sarah (Female, US)</option>
	                                                        <option value="af_sky">Sky (Female, US)</option>
	                                                        <option value="am_adam">Adam (Male, US)</option>
	                                                        <option value="am_michael">Michael (Male, US)</option>
	                                                        <option value="bf_emma">Emma (Female, UK)</option>
	                                                        <option value="bf_isabella">Isabella (Female, UK)</option>
	                                                        <option value="bm_george">George (Male, UK)</option>
	                                                        <option value="bm_lewis">Lewis (Male, UK)</option>
	                                                    </select>
	                                                    <p className="text-xs text-muted-foreground mt-1">
	                                                        af=American Female, am=American Male, bf=British Female, bm=British Male
	                                                    </p>
	                                                </div>
	                                            )}
	                                        </div>
	                                        {config.local_tts_backend === 'kokoro' && (config.kokoro_mode || '').toLowerCase() === 'api' && (
	                                            <div>
	                                                <label className="text-sm font-medium">Kokoro Web API</label>
	                                                <input
	                                                    type="text"
	                                                    className="w-full p-2 rounded-md border border-input bg-background mt-1"
	                                                    value={config.kokoro_api_base_url || ''}
	                                                    onChange={e => setConfig({ ...config, kokoro_api_base_url: e.target.value })}
	                                                    placeholder="https://voice-generator.pages.dev/api/v1"
	                                                />
	                                                <p className="text-xs text-muted-foreground mt-1">
	                                                    Supports OpenAI-compatible `audio/speech` endpoint. Recommended to self-host for reliability.
	                                                </p>
	                                                <label className="text-sm font-medium mt-3 block">Token (optional)</label>
                                                <input
                                                    type="password"
                                                    className="w-full p-2 rounded-md border border-input bg-background mt-1"
	                                                    value={config.kokoro_api_key || ''}
	                                                    onChange={e => setConfig({ ...config, kokoro_api_key: e.target.value })}
	                                                    placeholder="Bearer token (optional; Dashboard requires a token to enable Cloud/API selection)"
	                                                />
	                                            </div>
	                                        )}
	                                        {config.local_tts_backend === 'kokoro' && (config.kokoro_mode || '').toLowerCase() === 'hf' && (
	                                            <div className="text-xs text-muted-foreground">
	                                                HuggingFace mode forces Kokoro to load via the HuggingFace cache inside the container and may
	                                                download weights/voices on first use. Rebuilding the container can trigger re-downloads unless
	                                                the cache is persisted; prefer Local mode for production.
	                                            </div>
	                                        )}
	                                    </div>

                                    {/* LLM Config */}
                                    <div className="space-y-3 p-4 bg-muted/30 rounded-lg border">
                                        <h4 className="font-medium text-sm text-muted-foreground uppercase tracking-wider">Large Language Model (LLM)</h4>
                                        <div>
                                            <label className="text-sm font-medium">Model</label>
                                            <select
                                                className="w-full p-2 rounded-md border border-input bg-background mt-1"
                                                value={config.local_llm_model}
                                                onChange={e => setConfig({ ...config, local_llm_model: e.target.value })}
                                            >
                                                {(modelCatalog?.llm || []).filter((m: any) => !m.requires_api_key).map((model: any) => (
                                                    <option key={model.id} value={model.id}>
                                                        {model.name}
                                                        {model.system_recommended ? ' • Recommended' : ''}
                                                        {model.size_display ? ` • ${model.size_display}` : ''}
                                                        {model.description ? ` • ${model.description}` : ''}
                                                    </option>
                                                ))}
                                                <option value="custom_gguf_url">Custom GGUF (URL)</option>
                                            </select>
                                            {config.local_llm_model === 'custom_gguf_url' && (
                                                <div className="mt-3 p-3 bg-blue-50 dark:bg-blue-900/20 rounded-md border border-blue-200 dark:border-blue-800 space-y-3">
                                                    <p className="text-sm text-blue-800 dark:text-blue-300">
                                                        Provide a direct URL to a llama.cpp-compatible `.gguf` file. This will be downloaded into `models/llm/`.
                                                    </p>
                                                    <div className="space-y-2">
                                                        <label className="text-xs font-medium text-blue-700 dark:text-blue-300">GGUF URL</label>
                                                        <input
                                                            type="text"
                                                            className="w-full p-2 rounded-md border border-input bg-background"
                                                            value={config.local_llm_custom_url || ''}
                                                            onChange={e => setConfig({ ...config, local_llm_custom_url: e.target.value })}
                                                            placeholder="https://huggingface.co/.../resolve/main/model.Q4_K_M.gguf"
                                                        />
                                                    </div>
                                                    <div className="space-y-2">
                                                        <label className="text-xs font-medium text-blue-700 dark:text-blue-300">Filename (optional)</label>
                                                        <input
                                                            type="text"
                                                            className="w-full p-2 rounded-md border border-input bg-background"
                                                            value={config.local_llm_custom_filename || ''}
                                                            onChange={e => setConfig({ ...config, local_llm_custom_filename: e.target.value })}
                                                            placeholder="my-model.Q4_K_M.gguf"
                                                        />
                                                        <p className="text-xs text-blue-600 dark:text-blue-400">
                                                            If blank, filename is inferred from the URL.
                                                        </p>
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    </div>

                                    {/* Download Button */}
                                    <div className="bg-blue-50 dark:bg-blue-900/20 p-4 rounded-lg border border-blue-200 dark:border-blue-800">
                                        <div className="flex justify-between items-center">
                                            <div>
                                                <p className="font-medium text-blue-800 dark:text-blue-300">
                                                    Download Required Models
                                                </p>
                                                <p className="text-xs text-blue-600 dark:text-blue-400 mt-1">
                                                    Downloads models for selected backends.
                                                </p>
                                            </div>
		                                            <button
		                                                onClick={() => startSelectedModelsDownload()}
		                                                disabled={localAIStatus.downloading || localAIStatus.downloadCompleted}
		                                                className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
		                                            >
                                                {localAIStatus.downloading ? (
                                                    <span className="flex items-center gap-2">
                                                        <Loader2 className="w-4 h-4 animate-spin" />
                                                        Downloading...
                                                    </span>
                                                ) : localAIStatus.downloadCompleted ? (
                                                    <span className="flex items-center gap-2">
                                                        <CheckCircle2 className="w-4 h-4" />
                                                        Downloaded
                                                    </span>
                                                ) : (
                                                    <span className="flex items-center gap-2">
                                                        <Cloud className="w-4 h-4" />
                                                        Download Models
                                                    </span>
                                                )}
                                            </button>
                                        </div>

                                        {/* Download Progress Bar */}
                                        {localAIStatus.downloadProgress && localAIStatus.downloadProgress.total_bytes > 0 && (
                                            <div className="mt-4 space-y-2">
                                                <div className="flex justify-between text-xs text-gray-600 dark:text-gray-400">
                                                    <span>{localAIStatus.downloadProgress.current_file}</span>
                                                    <span>
                                                        {(localAIStatus.downloadProgress.bytes_downloaded / (1024 * 1024)).toFixed(1)} / {(localAIStatus.downloadProgress.total_bytes / (1024 * 1024)).toFixed(1)} MB
                                                    </span>
                                                </div>
                                                <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-3">
                                                    <div 
                                                        className="bg-blue-600 h-3 rounded-full transition-all duration-300"
                                                        style={{ width: `${localAIStatus.downloadProgress.percent}%` }}
                                                    />
                                                </div>
                                                <div className="flex justify-between text-xs text-gray-500 dark:text-gray-400">
                                                    <span>{localAIStatus.downloadProgress.percent}%</span>
                                                    <span>
                                                        {(localAIStatus.downloadProgress.speed_bps / (1024 * 1024)).toFixed(2)} MB/s
                                                        {localAIStatus.downloadProgress.eta_seconds && (
                                                            <> • ETA: {Math.floor(localAIStatus.downloadProgress.eta_seconds / 60)}m {localAIStatus.downloadProgress.eta_seconds % 60}s</>
                                                        )}
                                                    </span>
                                                </div>
                                            </div>
                                        )}

                                        {/* Download Output */}
                                        {localAIStatus.downloadOutput.length > 0 && (
                                            <div className="mt-4 bg-black/90 text-green-400 p-3 rounded-md font-mono text-xs h-32 overflow-y-auto">
                                                {localAIStatus.downloadOutput.map((line, i) => (
                                                    <div key={i}>{line}</div>
                                                ))}
                                                {localAIStatus.downloading && (
                                                    <div className="animate-pulse">_</div>
                                                )}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* Download Complete */}
                        {localAIStatus.downloadCompleted && (
                            <div className="bg-green-50 dark:bg-green-900/20 p-4 rounded-lg border border-green-200 dark:border-green-800">
                                <p className="text-green-800 dark:text-green-300 flex items-center font-medium">
                                    <CheckCircle className="w-5 h-5 mr-2" />
                                    Models downloaded successfully!
                                </p>
                                <p className="text-sm text-green-700 dark:text-green-400 mt-1">
                                    Click Next to continue with the setup.
                                </p>
                                <p className="text-sm text-blue-600 dark:text-blue-400 mt-2 bg-blue-50 dark:bg-blue-900/20 p-2 rounded">
                                    💡 <strong>Tip:</strong> You can download additional models and voices later from{' '}
                                    <span className="font-semibold">System → Models</span> in the Admin UI.
                                </p>
                            </div>
                        )}
                    </div>
                )}


                {step === 4 && (
                    <div className="space-y-4">
                        <h2 className="text-xl font-semibold mb-4">Agent Configuration</h2>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Asterisk Host</label>
                                <input
                                    type="text"
                                    className="w-full p-2 rounded-md border border-input bg-background"
                                    value={config.asterisk_host}
                                    onChange={e => setConfig({ ...config, asterisk_host: e.target.value })}
                                />
                            </div>
                            <div className="space-y-2">
                                <label className="text-sm font-medium">ARI Username</label>
                                <input
                                    type="text"
                                    className="w-full p-2 rounded-md border border-input bg-background"
                                    value={config.asterisk_username}
                                    onChange={e => setConfig({ ...config, asterisk_username: e.target.value })}
                                />
                            </div>
                            <div className="space-y-2">
                                <label className="text-sm font-medium">ARI Port</label>
                                <input
                                    type="number"
                                    className="w-full p-2 rounded-md border border-input bg-background"
                                    value={config.asterisk_port}
                                    onChange={e => setConfig({ ...config, asterisk_port: parseInt(e.target.value) || 8088 })}
                                />
                            </div>
                            <div className="space-y-2">
                                <label className="text-sm font-medium">ARI Scheme</label>
                                <select
                                    className="w-full p-2 rounded-md border border-input bg-background"
                                    value={config.asterisk_scheme}
                                    onChange={e => setConfig({ ...config, asterisk_scheme: e.target.value })}
                                >
                                    <option value="http">http</option>
                                    <option value="https">https</option>
                                </select>
                            </div>
                            {config.asterisk_scheme === 'https' && (
                                <div className="space-y-2">
                                    <label className="flex items-center gap-2 text-sm font-medium cursor-pointer">
                                        <input
                                            type="checkbox"
                                            className="w-4 h-4 rounded border border-input"
                                            checked={config.asterisk_ssl_verify !== false}
                                            onChange={e => setConfig({ ...config, asterisk_ssl_verify: e.target.checked })}
                                        />
                                        Verify SSL Certificate
                                    </label>
                                    <p className="text-xs text-muted-foreground">
                                        Uncheck for self-signed certificates or IP/hostname mismatches
                                    </p>
                                </div>
                            )}
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Stasis App Name</label>
                                <input
                                    type="text"
                                    className="w-full p-2 rounded-md border border-input bg-background"
                                    value={config.asterisk_app}
                                    onChange={e => setConfig({ ...config, asterisk_app: e.target.value })}
                                />
                            </div>
                        </div>

                        {/* Show Server IP field when using hostname for RTP security */}
                        {isUsingHostname && (
                            <div className="p-4 rounded-md border border-yellow-500/50 bg-yellow-500/10">
                                <div className="flex items-start gap-2 mb-3">
                                    <AlertTriangle className="w-5 h-5 text-yellow-500 mt-0.5" />
                                    <div>
                                        <p className="text-sm font-medium text-yellow-500">Remote Asterisk Detected</p>
                                        <p className="text-xs text-muted-foreground mt-1">
                                            You're using a hostname ({config.asterisk_host}). For RTP audio security, 
                                            please enter the actual IP address of your Asterisk server.
                                        </p>
                                    </div>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Asterisk Server IP Address</label>
                                    <input
                                        type="text"
                                        placeholder="e.g., 192.168.1.100 or 203.0.113.50"
                                        className="w-full p-2 rounded-md border border-input bg-background"
                                        value={config.asterisk_server_ip || ''}
                                        onChange={e => setConfig({ ...config, asterisk_server_ip: e.target.value })}
                                    />
                                    <p className="text-xs text-muted-foreground">
                                        This IP will be used for RTP packet validation (allowed_remote_hosts).
                                        {config.asterisk_scheme === 'https' && ' Using HTTPS/WSS for secure ARI connection.'}
                                    </p>
                                </div>
                            </div>
                        )}

                        <div className="space-y-2">
                            <label className="text-sm font-medium">ARI Password</label>
                            <input
                                type="password"
                                className="w-full p-2 rounded-md border border-input bg-background"
                                value={config.asterisk_password}
                                onChange={e => setConfig({ ...config, asterisk_password: e.target.value })}
                            />
                        </div>
                        <div className="flex justify-end gap-2">
                            {config.provider === 'local_hybrid' && (
                                <button
                                    onClick={async () => {
                                        setLoading(true);
                                        try {
                                            const res = await axios.get('/api/system/health');
                                            if (res.data.local_ai_server?.status === 'connected') {
                                                showToast('Local AI Server is running and connected!', 'success');
                                            } else {
                                                showToast(`Local AI Server is NOT connected. Status: ${res.data.local_ai_server?.status}`, 'error');
                                            }
                                        } catch (err) {
                                            showToast('Failed to contact system health endpoint.', 'error');
                                        } finally {
                                            setLoading(false);
                                        }
                                    }}
                                    className="px-3 py-2 text-sm rounded-md border border-input hover:bg-accent hover:text-accent-foreground flex items-center"
                                    disabled={loading}
                                >
                                    {loading ? <Loader2 className="w-3 h-3 mr-2 animate-spin" /> : <Server className="w-3 h-3 mr-2" />}
                                    Check Local Server
                                </button>
                            )}
                            <button
                                onClick={handleTestConnection}
                                className="px-3 py-2 text-sm rounded-md bg-secondary text-secondary-foreground hover:bg-secondary/80 flex items-center"
                                disabled={loading}
                            >
                                {loading ? <Loader2 className="w-3 h-3 mr-2 animate-spin" /> : <Zap className="w-3 h-3 mr-2" />}
                                Test Connection
                            </button>
                        </div>
                        <div className="border-t border-border my-4 pt-4">
                            <div className="flex items-center gap-2 mb-3">
                                <span className="text-sm font-semibold">Default Context Settings</span>
                                <div className="group relative">
                                    <Info className="w-4 h-4 text-muted-foreground cursor-help" />
                                    <div className="absolute left-0 bottom-full mb-2 w-72 p-2 bg-popover text-popover-foreground text-xs rounded-md shadow-lg border border-border opacity-0 group-hover:opacity-100 transition-opacity z-50 pointer-events-none">
                                        These settings become the default when no <code className="bg-muted px-1 rounded">AI_CONTEXT</code> variable is passed from the Asterisk dialplan. You can create additional contexts with different personas in the Contexts page.
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-2">
                                <label className="text-sm font-medium">AI Name</label>
                                <div className="group relative">
                                    <Info className="w-3 h-3 text-muted-foreground cursor-help" />
                                    <div className="absolute left-0 bottom-full mb-2 w-64 p-2 bg-popover text-popover-foreground text-xs rounded-md shadow-lg border border-border opacity-0 group-hover:opacity-100 transition-opacity z-50 pointer-events-none">
                                        The name your AI agent will use to identify itself to callers.
                                    </div>
                                </div>
                            </div>
                            <input
                                type="text"
                                className="w-full p-2 rounded-md border border-input bg-background"
                                value={config.ai_name}
                                onChange={e => setConfig({ ...config, ai_name: e.target.value })}
                                placeholder="e.g., Sarah, Alex, Support Agent"
                            />
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-2">
                                <label className="text-sm font-medium">AI Role</label>
                                <div className="group relative">
                                    <Info className="w-3 h-3 text-muted-foreground cursor-help" />
                                    <div className="absolute left-0 bottom-full mb-2 w-64 p-2 bg-popover text-popover-foreground text-xs rounded-md shadow-lg border border-border opacity-0 group-hover:opacity-100 transition-opacity z-50 pointer-events-none">
                                        Defines the AI's persona and behavior. This becomes part of the system prompt.
                                    </div>
                                </div>
                            </div>
                            <input
                                type="text"
                                className="w-full p-2 rounded-md border border-input bg-background"
                                value={config.ai_role}
                                onChange={e => setConfig({ ...config, ai_role: e.target.value })}
                                placeholder="e.g., You are a helpful customer service agent..."
                            />
                        </div>
                        <div className="space-y-2">
                            <div className="flex items-center gap-2">
                                <label className="text-sm font-medium">Greeting Message</label>
                                <div className="group relative">
                                    <Info className="w-3 h-3 text-muted-foreground cursor-help" />
                                    <div className="absolute left-0 bottom-full mb-2 w-64 p-2 bg-popover text-popover-foreground text-xs rounded-md shadow-lg border border-border opacity-0 group-hover:opacity-100 transition-opacity z-50 pointer-events-none">
                                        The first message spoken when a call connects. Keep it brief and welcoming.
                                    </div>
                                </div>
                            </div>
                            <textarea
                                className="w-full p-2 rounded-md border border-input bg-background min-h-[80px]"
                                value={config.greeting}
                                onChange={e => setConfig({ ...config, greeting: e.target.value })}
                                placeholder="e.g., Hello! Thank you for calling. How can I help you today?"
                            />
                        </div>
                    </div>
                )}

                {step === 5 && (
                    <div className="space-y-6 text-center">
                        <div className="w-16 h-16 bg-green-100 text-green-600 rounded-full flex items-center justify-center mx-auto mb-4">
                            <CheckCircle className="w-8 h-8" />
                        </div>
                        <h2 className="text-2xl font-bold">Setup Complete!</h2>
                        <p className="text-muted-foreground">
                            Your AI Agent is configured and ready.
                        </p>

                        {/* Local AI Server Setup - Only for Local provider */}
                        {config.provider === 'local' && (
                            <div className="space-y-4 text-left">
                                {/* Downloaded Models */}
                                <div className="bg-green-50 dark:bg-green-900/20 p-4 rounded-lg border border-green-200 dark:border-green-800">
                                    <h3 className="font-semibold mb-2 flex items-center text-green-800 dark:text-green-300">
                                        <HardDrive className="w-4 h-4 mr-2" />
                                        Downloaded Models
                                    </h3>
                                    <p className="text-sm text-green-700 dark:text-green-400">
                                        Tier: {localAIStatus.tier} | {localAIStatus.tierInfo?.models || 'Models ready'}
                                    </p>
                                </div>

                                {/* Start Local AI Server */}
                                <div className="bg-blue-50 dark:bg-blue-900/20 p-4 rounded-lg border border-blue-200 dark:border-blue-800">
                                    <h3 className="font-semibold mb-3 flex items-center text-blue-800 dark:text-blue-300">
                                        <Server className="w-4 h-4 mr-2" />
                                        Local AI Server
                                    </h3>

                                    {!localAIStatus.serverStarted ? (
                                    <button
                                            onClick={async () => {
                                                try {
                                                    await startLocalAIServer();
                                                } catch (err: any) {
                                                    setError(err?.response?.data?.message || err?.message || 'Failed to start local_ai_server');
                                                }
                                            }}
                                            disabled={startingLocalServer}
                                            className="w-full px-4 py-2 rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 flex items-center justify-center"
                                        >
                                            {startingLocalServer ? (
                                                <>
                                                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                                                    Starting...
                                                </>
                                            ) : (
                                                <>
                                                    <Play className="w-4 h-4 mr-2" />
                                                    Start Local AI Server
                                                </>
                                            )}
                                        </button>
                                    ) : (
                                        <div className="space-y-3">
                                            <div className="flex items-center gap-2">
                                                {localAIStatus.serverReady ? (
                                                    <span className="text-green-600 dark:text-green-400 flex items-center">
                                                        <CheckCircle className="w-4 h-4 mr-2" />
                                                        Server Ready!
                                                    </span>
                                                ) : (
                                                    <span className="text-blue-600 dark:text-blue-400 flex items-center">
                                                        <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-600 mr-2"></div>
                                                        Starting up... (loading models)
                                                    </span>
                                                )}
                                            </div>

                                            {/* Server Logs */}
                                            <div className="bg-black/90 rounded p-3 max-h-48 overflow-y-auto font-mono text-xs text-green-400">
                                                {localAIStatus.serverLogs.length > 0 ? (
                                                    localAIStatus.serverLogs.map((line, i) => (
                                                        <div key={i} className="whitespace-pre-wrap">{line}</div>
                                                    ))
                                                ) : (
                                                    <div className="text-gray-500">Waiting for logs...</div>
                                                )}
                                            </div>
                                        </div>
                                    )}
                                </div>

                                {/* AI Engine Status for Local Provider - Show after local server is ready */}
                                {localAIStatus.serverReady && !engineStatus.running && (
                                    <div className="bg-blue-50 dark:bg-blue-900/20 p-4 rounded-lg border border-blue-200 dark:border-blue-800">
                                        <h3 className="font-semibold mb-3 flex items-center text-blue-800 dark:text-blue-300">
                                            <Server className="w-4 h-4 mr-2" />
                                            Start AI Engine
                                        </h3>
                                        <p className="text-sm text-blue-700 dark:text-blue-400 mb-4">
                                            Local AI Server is ready. Now start the AI Engine to connect to Asterisk.
                                        </p>
                                        <button
                                            onClick={async () => {
                                                setStartingEngine(true);
                                                setError(null);
                                                setEngineProgress({ steps: [], currentStep: 'Starting...' });
                                                try {
                                                    const res = await axios.post('/api/wizard/start-engine');
                                                    // Update progress from response
                                                    if (res.data.steps) {
                                                        setEngineProgress({ steps: res.data.steps, currentStep: '' });
                                                    }
                                                    if (res.data.success) {
                                                        setEngineStatus({ ...engineStatus, running: true, exists: true });
                                                        showToast('AI Engine started successfully!', 'success');
                                                        // Show media setup warnings if any
                                                        const mediaErrors = res.data.media_setup?.errors || [];
                                                        if (mediaErrors.length > 0) {
                                                            setError('Warning: Media path setup had issues. Audio playback may not work.\n\n' +
                                                                mediaErrors.join('\n') +
                                                                '\n\nManual fix: Run on your host:\n  sudo ln -sfn /path/to/asterisk_media/ai-generated /var/lib/asterisk/sounds/ai-generated');
                                                        }
                                                    } else {
                                                        setError(res.data.message + (res.data.stderr ? `\n\nDetails: ${res.data.stderr.slice(0, 300)}` : ''));
                                                    }
                                                } catch (err: any) {
                                                    setError(err.response?.data?.detail || err.message);
                                                } finally {
                                                    setStartingEngine(false);
                                                }
                                            }}
                                            disabled={startingEngine}
                                            className="w-full px-4 py-2 rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 flex items-center justify-center"
                                        >
                                            {startingEngine ? (
                                                <>
                                                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                                                    Building & Starting AI Engine...
                                                </>
                                            ) : (
                                                <>
                                                    <Play className="w-4 h-4 mr-2" />
                                                    Start AI Engine
                                                </>
                                            )}
                                        </button>
                                        
                                        {/* Progress Steps - show during build or after completion */}
                                        {startingEngine && engineProgress.steps.length === 0 && (
                                            <div className="mt-4 space-y-2 text-sm text-muted-foreground">
                                                <div className="flex items-center">
                                                    <Loader2 className="w-4 h-4 mr-2 animate-spin text-blue-500" />
                                                    <span>Checking Docker environment...</span>
                                                </div>
                                                <div className="flex items-center">
                                                    <Loader2 className="w-4 h-4 mr-2 animate-spin text-blue-500" />
                                                    <span>Building AI Engine image (this may take 1-2 minutes)...</span>
                                                </div>
                                            </div>
                                        )}
                                        {engineProgress.steps.length > 0 && (
                                            <div className="mt-4 space-y-2">
                                                {engineProgress.steps.map((step, idx) => (
                                                    <div key={idx} className="flex items-center text-sm">
                                                        {step.status === 'complete' && <CheckCircle className="w-4 h-4 mr-2 text-green-500" />}
                                                        {step.status === 'running' && <Loader2 className="w-4 h-4 mr-2 animate-spin text-blue-500" />}
                                                        {step.status === 'error' && <XCircle className="w-4 h-4 mr-2 text-red-500" />}
                                                        {step.status === 'warning' && <AlertCircle className="w-4 h-4 mr-2 text-yellow-500" />}
                                                        <span className={step.status === 'error' ? 'text-red-600' : step.status === 'complete' ? 'text-green-600' : ''}>
                                                            {step.message || step.name}
                                                        </span>
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                )}

                                {/* Engine Running Success for Local */}
                                {localAIStatus.serverReady && engineStatus.running && (
                                    <div className="bg-green-50 dark:bg-green-900/20 p-4 rounded-lg border border-green-200 dark:border-green-800">
                                        <div className="flex items-center justify-between">
                                            <div className="flex items-center text-green-700 dark:text-green-400">
                                                <CheckCircle className="w-5 h-5 mr-2" />
                                                <span className="font-medium">AI Engine is running</span>
                                            </div>
                                            <button
                                                onClick={async () => {
                                                    setReloadingEngine(true);
                                                    try {
                                                        const res = await axios.post('/api/system/containers/ai_engine/reload');
                                                        if (res.data.restart_required) {
                                                            const shouldRestart = await confirm({
                                                                title: 'Restart Required',
                                                                description: 'New provider detected. A full restart is needed. Restart now?',
                                                                confirmText: 'Restart',
                                                                variant: 'default'
                                                            });
                                                            if (shouldRestart) {
                                                                showToast('Restarting AI Engine...', 'success');
                                                                const restartRes = await axios.post('/api/system/containers/ai_engine/restart?force=false&recreate=true');
                                                                if (restartRes.data?.status === 'warning') {
                                                                    const confirmForce = await confirm({
                                                                        title: 'Force Restart?',
                                                                        description: `${restartRes.data.message}\n\nForce restart anyway? This may disconnect active calls.`,
                                                                        confirmText: 'Force Restart',
                                                                        variant: 'destructive'
                                                                    });
                                                                    if (confirmForce) {
                                                                        await axios.post('/api/system/containers/ai_engine/restart?force=true&recreate=true');
                                                                        showToast('AI Engine restarted!', 'success');
                                                                    } else {
                                                                        showToast('Restart skipped due to active calls.', 'warning');
                                                                    }
                                                                } else if (restartRes.data?.status === 'degraded') {
                                                                    showToast('AI Engine restarted but may not be fully healthy. Verify manually.', 'warning');
                                                                } else {
                                                                    showToast('AI Engine restarted!', 'success');
                                                                }
                                                            } else {
                                                                showToast('Config saved. Restart later to apply.', 'success');
                                                            }
                                                        } else {
                                                            showToast('AI Engine configuration reloaded!', 'success');
                                                        }
                                                    } catch (err: any) {
                                                        showToast(err.response?.data?.detail || 'Failed to reload', 'error');
                                                    } finally {
                                                        setReloadingEngine(false);
                                                    }
                                                }}
                                                disabled={reloadingEngine}
                                                className="flex items-center gap-1 px-3 py-1.5 text-sm bg-green-600 text-white rounded-md hover:bg-green-700 disabled:opacity-50"
                                            >
                                                <RefreshCw className={`w-4 h-4 ${reloadingEngine ? 'animate-spin' : ''}`} />
                                                {reloadingEngine ? 'Applying...' : 'Apply Changes'}
                                            </button>
                                        </div>
                                    </div>
                                )}

                                {/* Go to Dashboard - Only when BOTH local server AND engine are ready */}
                                {localAIStatus.serverReady && engineStatus.running && (
                                    <div className="pt-4">
                                        <button
                                            onClick={() => navigate('/')}
                                            className="w-full px-4 py-3 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 font-medium"
                                        >
                                            Go to Dashboard
                                        </button>
                                    </div>
                                )}

                                {/* Dialplan for Local */}
                                {localAIStatus.serverReady && engineStatus.running && (
                                    <div className="bg-muted p-4 rounded-lg">
                                        <h3 className="font-semibold mb-2 flex items-center">
                                            <Terminal className="w-4 h-4 mr-2" />
                                            Asterisk Dialplan for Local Provider
                                        </h3>
                                        <pre className="bg-black text-green-400 p-3 rounded-md overflow-x-auto text-xs font-mono">
                                            {`[from-ai-agent-local]
exten => s,1,NoOp(AI Agent - Local Full)
 same => n,Set(AI_CONTEXT=default)
 same => n,Set(AI_PROVIDER=local)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()`}
                                        </pre>
                                    </div>
                                )}
                            </div>
                        )}

                        {/* AI Engine Status - Show start button if not running (non-local providers) */}
                        {config.provider !== 'local' && engineStatus.checked && !engineStatus.running && (
                            <div className="bg-blue-50 dark:bg-blue-900/20 p-4 rounded-lg text-left border border-blue-200 dark:border-blue-800">
                                <h3 className="font-semibold mb-3 flex items-center text-blue-800 dark:text-blue-300">
                                    <Server className="w-4 h-4 mr-2" />
                                    Start AI Engine
                                </h3>
                                <p className="text-sm text-blue-700 dark:text-blue-400 mb-4">
                                    {engineStatus.exists
                                        ? "The AI Engine container exists but is not running. Click below to start it."
                                        : "The AI Engine container needs to be created. Run the command below, then click Start."}
                                </p>
                                {!engineStatus.exists && (
                                    <pre className="bg-black text-green-400 p-3 rounded-md text-xs font-mono mb-4 overflow-x-auto">
                                        docker compose up -d ai_engine
                                    </pre>
                                )}
                                <button
                                    onClick={async () => {
                                        setStartingEngine(true);
                                        setError(null);
                                        setEngineProgress({ steps: [], currentStep: 'Starting...' });
                                        try {
                                            const res = await axios.post('/api/wizard/start-engine');
                                            // Update progress from response
                                            if (res.data.steps) {
                                                setEngineProgress({ steps: res.data.steps, currentStep: '' });
                                            }
                                            if (res.data.success) {
                                                setEngineStatus({ ...engineStatus, running: true, exists: true });
                                                showToast('AI Engine started successfully!', 'success');
                                                // Show media setup warnings if any
                                                const mediaErrors = res.data.media_setup?.errors || [];
                                                if (mediaErrors.length > 0) {
                                                    setError('Warning: Media path setup had issues. Audio playback may not work.\n\n' +
                                                        mediaErrors.join('\n') +
                                                        '\n\nManual fix: Run on your host:\n  sudo ln -sfn /path/to/asterisk_media/ai-generated /var/lib/asterisk/sounds/ai-generated');
                                                }
                                            } else {
                                                setError(res.data.message + (res.data.stderr ? `\n\nDetails: ${res.data.stderr.slice(0, 300)}` : ''));
                                            }
                                        } catch (err: any) {
                                            setError(err.response?.data?.detail || err.message);
                                        } finally {
                                            setStartingEngine(false);
                                        }
                                    }}
                                    disabled={startingEngine}
                                    className="w-full px-4 py-2 rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 flex items-center justify-center"
                                >
                                    {startingEngine ? (
                                        <>
                                            <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                                            Building & Starting AI Engine...
                                        </>
                                    ) : (
                                        <>
                                            <Play className="w-4 h-4 mr-2" />
                                            Start AI Engine
                                        </>
                                    )}
                                </button>
                                
                                {/* Progress Steps - show during build or after completion */}
                                {startingEngine && engineProgress.steps.length === 0 && (
                                    <div className="mt-4 space-y-2 text-sm text-muted-foreground">
                                        <div className="flex items-center">
                                            <Loader2 className="w-4 h-4 mr-2 animate-spin text-blue-500" />
                                            <span>Checking Docker environment...</span>
                                        </div>
                                        <div className="flex items-center">
                                            <Loader2 className="w-4 h-4 mr-2 animate-spin text-blue-500" />
                                            <span>Building AI Engine image (this may take 1-2 minutes)...</span>
                                        </div>
                                    </div>
                                )}
                                {engineProgress.steps.length > 0 && (
                                    <div className="mt-4 space-y-2">
                                        {engineProgress.steps.map((step, idx) => (
                                            <div key={idx} className="flex items-center text-sm">
                                                {step.status === 'complete' && <CheckCircle className="w-4 h-4 mr-2 text-green-500" />}
                                                {step.status === 'running' && <Loader2 className="w-4 h-4 mr-2 animate-spin text-blue-500" />}
                                                {step.status === 'error' && <XCircle className="w-4 h-4 mr-2 text-red-500" />}
                                                {step.status === 'warning' && <AlertCircle className="w-4 h-4 mr-2 text-yellow-500" />}
                                                <span className={step.status === 'error' ? 'text-red-600' : step.status === 'complete' ? 'text-green-600' : ''}>
                                                    {step.message || step.name}
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        )}

                        {/* Engine Running - Success (non-local providers) */}
                        {config.provider !== 'local' && engineStatus.checked && engineStatus.running && (
                            <div className="bg-green-50 dark:bg-green-900/20 p-4 rounded-lg text-left border border-green-200 dark:border-green-800">
                                <div className="flex items-center justify-between">
                                    <div className="flex items-center text-green-700 dark:text-green-400">
                                        <CheckCircle className="w-5 h-5 mr-2" />
                                        <span className="font-medium">AI Engine is running</span>
                                    </div>
                                    <button
                                        onClick={async () => {
                                            setReloadingEngine(true);
                                            try {
                                                const res = await axios.post('/api/system/containers/ai_engine/reload');
                                                if (res.data.restart_required) {
                                                    // New provider needs full restart
                                                    const shouldRestart = await confirm({
                                                        title: 'Restart Required',
                                                        description: 'New provider detected. A full restart is needed to load it. Restart now?',
                                                        confirmText: 'Restart',
                                                        variant: 'default'
                                                    });
                                                    if (shouldRestart) {
                                                        showToast('Restarting AI Engine...', 'success');
                                                        const restartRes = await axios.post('/api/system/containers/ai_engine/restart?force=false&recreate=true');
                                                        if (restartRes.data?.status === 'warning') {
                                                            const confirmForce = await confirm({
                                                                title: 'Force Restart?',
                                                                description: `${restartRes.data.message}\n\nForce restart anyway? This may disconnect active calls.`,
                                                                confirmText: 'Force Restart',
                                                                variant: 'destructive'
                                                            });
                                                            if (confirmForce) {
                                                                await axios.post('/api/system/containers/ai_engine/restart?force=true&recreate=true');
                                                                showToast('AI Engine restarted! New provider is now available.', 'success');
                                                            } else {
                                                                showToast('Restart skipped due to active calls. Restart later to use new provider.', 'warning');
                                                            }
                                                        } else if (restartRes.data?.status === 'degraded') {
                                                            showToast('AI Engine restarted but may not be fully healthy. Verify manually.', 'warning');
                                                        } else {
                                                            showToast('AI Engine restarted! New provider is now available.', 'success');
                                                        }
                                                    } else {
                                                        showToast('Config saved. Restart AI Engine later to use new provider.', 'success');
                                                    }
                                                } else {
                                                    showToast('AI Engine configuration reloaded successfully!', 'success');
                                                }
                                            } catch (err: any) {
                                                showToast(err.response?.data?.detail || 'Failed to reload', 'error');
                                            } finally {
                                                setReloadingEngine(false);
                                            }
                                        }}
                                        disabled={reloadingEngine}
                                        className="flex items-center gap-1 px-3 py-1.5 text-sm bg-green-600 text-white rounded-md hover:bg-green-700 disabled:opacity-50"
                                    >
                                        <RefreshCw className={`w-4 h-4 ${reloadingEngine ? 'animate-spin' : ''}`} />
                                        {reloadingEngine ? 'Applying...' : 'Apply Changes'}
                                    </button>
                                </div>
                                <p className="text-xs text-green-600 dark:text-green-500 mt-2">
                                    Click "Apply Changes" to activate your new provider settings.
                                </p>
                            </div>
                        )}

                        {/* Dialplan Section - non-local providers */}
                        {config.provider !== 'local' && (
                            <>
                                <div className="bg-muted p-4 rounded-lg text-left">
                                    <h3 className="font-semibold mb-2 flex items-center">
                                        <Terminal className="w-4 h-4 mr-2" />
                                        Next Step: Update Asterisk Dialplan
                                    </h3>
                                    <p className="text-sm text-muted-foreground mb-3">
                                        Add this to your <code>extensions_custom.conf</code> to route calls to the agent:
                                    </p>
                                    <div className="relative group">
                                        <pre className="bg-black text-green-400 p-4 rounded-md overflow-x-auto text-sm font-mono">
                                            {nonLocalDialplanSnippet}
                                        </pre>
                                        <button
                                            onClick={() => {
                                                navigator.clipboard.writeText(nonLocalDialplanSnippet)
                                                    .then(() => showToast('Copied to clipboard!', 'success'))
                                                    .catch(() => showToast('Failed to copy to clipboard', 'error'));
                                            }}
                                            className="absolute top-2 right-2 p-1 bg-white/10 rounded hover:bg-white/20 text-white opacity-0 group-hover:opacity-100 transition-opacity"
                                            title="Copy to clipboard"
                                        >
                                            <Copy className="w-4 h-4" />
                                        </button>
                                    </div>
                                </div>

                                <div className="pt-4">
                                    <button
                                        onClick={() => navigate('/')}
                                        className="w-full px-4 py-3 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 font-medium"
                                    >
                                        Go to Dashboard
                                    </button>
                                </div>
                            </>
                        )}
                    </div>
                )}

                <div className="mt-8 flex justify-between">
                    {step > 1 && step < 5 ? (
                        <button
                            onClick={() => setStep(step - 1)}
                            className="px-4 py-2 rounded-md border border-input hover:bg-accent hover:text-accent-foreground"
                            disabled={loading}
                        >
                            Back
                        </button>
                    ) : <div></div>}

	                    {step < 5 && (
	                        <button
	                            onClick={handleNext}
	                            disabled={
	                                loading ||
	                                (step === 3 &&
	                                    ((config.provider === 'local' &&
	                                        (localAIStatus.downloading ||
	                                            (!localAIStatus.downloadCompleted && !!localAIStatus.tier))) ||
	                                        (config.provider === 'local_hybrid' &&
	                                            (localAIStatus.downloading || localHybridMissingRequired))))
	                            }
	                            className="px-4 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 flex items-center disabled:opacity-50 disabled:cursor-not-allowed"
	                        >
                            {loading && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                            {step === 4 ? 'Finish Setup' : 'Next'}
                            {step < 4 && <ArrowRight className="w-4 h-4 ml-2" />}
                        </button>
                    )}
                </div>
                {showSkipConfirm && (
                    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                        <div className="bg-card border border-border p-6 rounded-lg shadow-lg max-w-md w-full">
                            <h3 className="text-lg font-semibold mb-2">Skip Setup?</h3>
                            <p className="text-muted-foreground mb-4">
                                Are you sure you want to skip setup? You will need to manually configure the environment variables later.
                            </p>
                            <div className="flex justify-end space-x-2">
                                <button
                                    onClick={() => setShowSkipConfirm(false)}
                                    className="px-4 py-2 rounded-md border border-input hover:bg-accent hover:text-accent-foreground"
                                >
                                    Cancel
                                </button>
                                <button
                                    onClick={confirmSkip}
                                    className="px-4 py-2 rounded-md bg-destructive text-destructive-foreground hover:bg-destructive/90"
                                >
                                    Skip Setup
                                </button>
                            </div>
                        </div>
                    </div>
                )}

                {/* Toast Notification */}
	                {toast && (
	                    <div className="fixed bottom-4 right-4 z-50">
	                        <div
	                            className={`flex items-center gap-2 px-4 py-3 rounded-lg shadow-lg text-sm font-medium animate-in slide-in-from-right ${toast.type === 'success'
	                                ? 'bg-green-500 text-white'
	                                : toast.type === 'warning'
	                                    ? 'bg-yellow-500 text-white'
	                                    : 'bg-red-500 text-white'
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
	                    </div>
	                )}
            </div>
        </div >
    );
};

export default Wizard;
