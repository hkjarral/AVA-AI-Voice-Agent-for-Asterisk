import { useState, useEffect } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import yaml from 'js-yaml';
import { Save, AlertCircle, RefreshCw, Loader2 } from 'lucide-react';
import { YamlErrorBanner, YamlErrorInfo } from '../../components/ui/YamlErrorBanner';
import { ConfigSection } from '../../components/ui/ConfigSection';
import { ConfigCard } from '../../components/ui/ConfigCard';
import { FormInput, FormSelect, FormSwitch } from '../../components/ui/FormComponents';
import { sanitizeConfigForSave } from '../../utils/configSanitizers';
import { getCachedConfig, loadConfigYaml } from '../../utils/configCache';
import { useRestartRequired } from '../../hooks/useRestartRequired';

const normalizeAriPingSeconds = (rawValue: string): number => {
    if (rawValue.trim() === '') return 10;
    const parsed = Number(rawValue);
    return Number.isFinite(parsed) ? Math.min(60, Math.max(5, parsed)) : 10;
};

type TransportConfig = Record<string, unknown> & {
    audio_transport?: string;
    asterisk?: {
        app_name?: string;
        ws_ping_interval_sec?: number;
        ws_ping_timeout_sec?: number;
    };
    audiosocket?: {
        host?: string;
        advertise_host?: string;
        port?: number;
        format?: string;
    };
    external_media?: {
        rtp_host?: string;
        advertise_host?: string;
        rtp_port?: number;
        port_range?: string;
        allowed_remote_hosts?: string[] | string | null;
        codec?: string;
        direction?: string;
        format?: string;
        sample_rate?: number;
        lock_remote_endpoint?: boolean;
    };
    websocket_media?: {
        connection_mode?: 'asterisk_outbound';
        connection_name?: string;
        bind_host?: string;
        advertise_host?: string;
        port?: number;
        path?: string;
        format_policy?: 'profile';
        fallback_format?: 'ulaw' | 'alaw' | 'slin' | 'slin16';
        control_format?: 'json' | 'auto' | 'plain';
        direction?: 'both';
        handshake_timeout_ms?: number;
        media_start_timeout_ms?: number;
        drain_timeout_ms?: number;
        pre_start_buffer_ms?: number;
        max_connections?: number;
        allowed_remote_hosts?: string[];
        auth?: { required?: boolean; username?: string; password_env?: string };
        tls?: { enabled?: boolean; cert_file?: string | null; key_file?: string | null };
    };
};

type WebSocketMediaStatus = {
    config: NonNullable<TransportConfig['websocket_media']>;
    secret_reference: string;
    secret_present: boolean;
    secret_source: string;
};

type AsteriskStatus = {
    live?: {
        asterisk_version?: string | null;
        modules?: Record<string, string>;
        websocket_media_supported?: boolean;
        websocket_media_reason?: string;
        websocket_effective_control_format?: 'json' | 'plain' | null;
    };
};

type SystemHealth = {
    ai_engine?: {
        status?: string;
        details?: {
            audio_transport?: string | null;
            websocket_media?: {
                listening?: boolean;
                requested_control_format?: string;
                effective_control_format?: string | null;
                modules_ready?: boolean;
                module_inventory_available?: boolean;
                missing_modules?: string[];
                non_running_modules?: string[];
                timing_modules?: string[];
                module_reason?: string | null;
            };
        };
    };
};

type WebSocketFieldErrors = Partial<
    Record<
        'connection_name' | 'path' | 'advertise_host' | 'port' | 'username' | 'password_env',
        string
    >
>;

const transportLabel = (transport: string | null | undefined): string => {
    if (transport === 'websocket') return 'WebSocket';
    if (transport === 'externalmedia') return 'External Media (RTP)';
    if (transport === 'audiosocket') return 'AudioSocket';
    return 'Not detected';
};

const validateWebSocketSnippet = (
    websocketConfig: NonNullable<TransportConfig['websocket_media']>
): WebSocketFieldErrors => {
    const errors: WebSocketFieldErrors = {};
    const connectionName = websocketConfig.connection_name ?? 'aava_media';
    const path = websocketConfig.path ?? '/media';
    const host = websocketConfig.advertise_host ?? websocketConfig.bind_host ?? '127.0.0.1';
    const port = websocketConfig.port ?? 8787;
    const username = websocketConfig.auth?.username ?? 'aava_media';
    const passwordEnv = websocketConfig.auth?.password_env ?? 'ASTERISK_MEDIA_WS_PASSWORD';

    if (!/^[A-Za-z0-9_-]{1,128}$/.test(connectionName)) {
        errors.connection_name = 'Use 1–128 letters, numbers, underscores, or hyphens.';
    }
    const pathHasUnsafeCharacter = Array.from(path).some(character => {
        const codePoint = character.charCodeAt(0);
        return (
            /\s/.test(character) || '?#'.includes(character) || codePoint < 32 || codePoint === 127
        );
    });
    if (!path.startsWith('/') || path.length > 256 || pathHasUnsafeCharacter) {
        errors.path = 'Use one absolute path without whitespace, a query, or a fragment.';
    }
    const hostnameSafe = /^[A-Za-z0-9._-]{1,255}$/.test(host);
    let ipv6Safe = false;
    if (host.includes(':') && /^[0-9A-Fa-f:.]{2,255}$/.test(host)) {
        try {
            ipv6Safe = new URL(`http://[${host}]/`).hostname.length > 0;
        } catch {
            ipv6Safe = false;
        }
    }
    if (!hostnameSafe && !ipv6Safe) {
        errors.advertise_host =
            'Use a bare hostname or IP address without brackets or URI punctuation.';
    }
    if (!Number.isInteger(port) || port < 1024 || port > 65535) {
        errors.port = 'Use an integer from 1024 through 65535.';
    }
    const usernameHasUnsafeCharacter = Array.from(username).some(character => {
        const codePoint = character.charCodeAt(0);
        return /\s/.test(character) || character === ':' || codePoint < 32 || codePoint === 127;
    });
    if (!username || username.length > 128 || usernameHasUnsafeCharacter) {
        errors.username = 'Use 1–128 characters without whitespace, colons, or control characters.';
    }
    if (!/^[A-Z_][A-Z0-9_]{0,127}$/.test(passwordEnv)) {
        errors.password_env = 'Use an uppercase environment variable name.';
    }
    return errors;
};

const TransportPage = () => {
    const { confirm } = useConfirmDialog();
    const [config, setConfig] = useState<TransportConfig>(
        () => (getCachedConfig()?.config ?? {}) as TransportConfig
    );
    const [loading, setLoading] = useState(() => getCachedConfig() == null);
    const [yamlError, setYamlError] = useState<YamlErrorInfo | null>(
        () => getCachedConfig()?.yamlError ?? null
    );
    const [saving, setSaving] = useState(false);
    const { restartRequired, refetch } = useRestartRequired();
    const [restartingEngine, setRestartingEngine] = useState(false);
    const [applyProgress, setApplyProgress] = useState<string | null>(null);
    const [applyMethod, setApplyMethod] = useState<string>('restart');
    const [showExternalMediaExpert, setShowExternalMediaExpert] = useState(false);
    const [websocketStatus, setWebsocketStatus] = useState<WebSocketMediaStatus | null>(null);
    const [asteriskStatus, setAsteriskStatus] = useState<AsteriskStatus | null>(null);
    const [systemHealth, setSystemHealth] = useState<SystemHealth | null>(null);
    const [savedTransport, setSavedTransport] = useState<string>(
        () =>
            (getCachedConfig()?.config as TransportConfig | undefined)?.audio_transport ||
            'audiosocket'
    );

    useEffect(() => {
        // A WebSocket recreate runs through the updater image, which may be
        // built on first use (minutes). Surface that phase instead of a bare
        // spinner.
        if (!restartingEngine || savedTransport !== 'websocket') {
            setApplyProgress(null);
            return;
        }
        let cancelled = false;
        const poll = async () => {
            try {
                const res = await axios.get('/api/system/updates/updater-image/status');
                const status = res.data?.status;
                if (!cancelled) {
                    setApplyProgress(
                        status?.status === 'running' && status.message ? status.message : null
                    );
                }
            } catch {
                /* progress is best-effort */
            }
        };
        void poll();
        const timer = setInterval(poll, 2000);
        return () => {
            cancelled = true;
            clearInterval(timer);
        };
    }, [restartingEngine, savedTransport]);

    useEffect(() => {
        // Cache-first: seed from the shared cache (no flash on revisit). The write
        // interceptor invalidates the cache on every save, so a background
        // revalidate is unnecessary and could clobber in-progress form edits.
        fetchConfig();
        void fetchWebSocketStatus();
    }, []);

    const fetchConfig = async (force = false) => {
        try {
            const r = await loadConfigYaml(force);
            setConfig(r.config as TransportConfig);
            setSavedTransport((r.config as TransportConfig).audio_transport || 'audiosocket');
            setYamlError(r.yamlError);
        } catch (err) {
            console.error('Failed to load config', err);
            setYamlError(null);
        } finally {
            setLoading(false);
        }
    };

    const fetchWebSocketStatus = async () => {
        const [websocket, asterisk, health] = await Promise.allSettled([
            axios.get<WebSocketMediaStatus>('/api/config/websocket-media-status'),
            axios.get<AsteriskStatus>('/api/system/asterisk-status'),
            axios.get<SystemHealth>('/api/system/health'),
        ]);
        if (websocket.status === 'fulfilled') setWebsocketStatus(websocket.value.data);
        if (asterisk.status === 'fulfilled') setAsteriskStatus(asterisk.value.data);
        if (health.status === 'fulfilled') setSystemHealth(health.value.data);
        if ([websocket, asterisk, health].some(result => result.status === 'rejected')) {
            // Keep each successful source visible; diagnostics are advisory and
            // must not make the configuration editor unusable.
            console.warn('Some WebSocket transport diagnostics are unavailable');
        }
    };

    const handleSave = async () => {
        if (config.audio_transport === 'websocket') {
            const errors = validateWebSocketSnippet(
                config.websocket_media || websocketStatus?.config || {}
            );
            if (Object.keys(errors).length > 0) {
                toast.error('Fix the invalid WebSocket fields before saving.');
                return;
            }
        }
        setSaving(true);
        try {
            const sanitized = sanitizeConfigForSave(config);
            const response = await axios.post('/api/config/yaml', {
                content: yaml.dump(sanitized),
            });
            const method = response.data?.recommended_apply_method || 'restart';
            setApplyMethod(method);
            setSavedTransport((sanitized as TransportConfig).audio_transport || 'audiosocket');
            await refetch();
            await fetchWebSocketStatus();

            // Show appropriate message based on recommended apply method
            if (method === 'hot_reload') {
                toast.success('Configuration saved. Changes can be applied via hot-reload.');
            } else if (method === 'none') {
                toast.success('Configuration saved. No runtime changes detected.');
            } else {
                toast.success(
                    config.audio_transport === 'websocket'
                        ? 'Transport configuration saved. Recreate AI Engine to load the listener and its environment.'
                        : 'Transport configuration saved. Restart AI Engine to apply changes.'
                );
            }
        } catch (err) {
            console.error('Failed to save config', err);
            const description = axios.isAxiosError(err)
                ? err.response?.data?.detail || err.message
                : err instanceof Error
                  ? err.message
                  : String(err);
            toast.error('Failed to save configuration', { description });
        } finally {
            setSaving(false);
        }
    };

    const handleApplyAIEngine = async (force: boolean = false) => {
        setRestartingEngine(true);
        try {
            if (applyMethod === 'hot_reload') {
                const response = await axios.post('/api/system/containers/ai_engine/reload');

                if (response.data?.restart_required) {
                    setApplyMethod('restart');
                    await refetch();
                    toast.warning('Hot reload applied partially', {
                        description:
                            response.data.message || 'Restart AI Engine to fully apply changes',
                    });
                    return;
                }

                if (response.data?.status === 'success') {
                    await refetch();
                    toast.success('AI Engine hot reloaded! Changes are now active.');
                    return;
                }

                toast.info(`Hot reload response: ${response.data?.message || 'unknown status'}`);
                return;
            }

            const response = await axios.post(
                `/api/system/containers/ai_engine/restart?force=${force}&recreate=${savedTransport === 'websocket'}`
            );

            if (response.data.status === 'warning') {
                const applyNoun = savedTransport === 'websocket' ? 'recreate' : 'restart';
                const confirmForce = await confirm({
                    title: savedTransport === 'websocket' ? 'Force Recreate?' : 'Force Restart?',
                    description: `${response.data.message}\n\nDo you want to force ${applyNoun} anyway? This may disconnect active calls.`,
                    confirmText:
                        savedTransport === 'websocket' ? 'Force Recreate' : 'Force Restart',
                    variant: 'destructive',
                });
                if (confirmForce) {
                    setRestartingEngine(false);
                    return handleApplyAIEngine(true);
                }
                return;
            }

            if (response.data.status === 'degraded') {
                toast.warning(
                    savedTransport === 'websocket'
                        ? 'AI Engine recreated but may not be fully healthy'
                        : 'AI Engine restarted but may not be fully healthy',
                    {
                        description: response.data.output || 'Please verify manually',
                    }
                );
                return;
            }

            if (response.data.status === 'success') {
                await refetch();
                await fetchWebSocketStatus();
                toast.success(
                    savedTransport === 'websocket'
                        ? 'AI Engine recreated. Re-check listener and call readiness below.'
                        : 'AI Engine restarted! Changes are now active.'
                );
                return;
            }
        } catch (error: unknown) {
            const actionLabel =
                applyMethod === 'hot_reload'
                    ? 'hot reload'
                    : savedTransport === 'websocket'
                      ? 'recreate'
                      : 'restart';
            const description = axios.isAxiosError(error)
                ? error.response?.data?.detail || error.message
                : error instanceof Error
                  ? error.message
                  : String(error);
            toast.error(`Failed to ${actionLabel} AI Engine`, { description });
        } finally {
            setRestartingEngine(false);
        }
    };

    const updateConfig = (field: string, value: unknown) => {
        setConfig({ ...config, [field]: value });
    };

    const updateSectionConfig = (section: string, field: string, value: unknown) => {
        const currentSection = config[section];
        const sectionConfig =
            currentSection && typeof currentSection === 'object'
                ? (currentSection as Record<string, unknown>)
                : {};
        setConfig({
            ...config,
            [section]: {
                ...sectionConfig,
                [field]: value,
            },
        });
    };

    const updateNestedSectionConfig = (
        section: string,
        nestedSection: string,
        field: string,
        value: unknown
    ) => {
        const currentSection = config[section];
        const sectionConfig =
            currentSection && typeof currentSection === 'object'
                ? (currentSection as Record<string, unknown>)
                : {};
        const currentNested = sectionConfig[nestedSection];
        const nestedConfig =
            currentNested && typeof currentNested === 'object'
                ? (currentNested as Record<string, unknown>)
                : {};
        setConfig({
            ...config,
            [section]: {
                ...sectionConfig,
                [nestedSection]: { ...nestedConfig, [field]: value },
            },
        });
    };

    useEffect(() => {
        if (config?.external_media?.lock_remote_endpoint !== undefined) {
            setShowExternalMediaExpert(true);
        }
    }, [config?.external_media?.lock_remote_endpoint]);

    if (loading)
        return (
            <div className="p-8 text-center text-muted-foreground">Loading configuration...</div>
        );

    if (yamlError)
        return (
            <div className="space-y-6">
                <YamlErrorBanner error={yamlError} />
            </div>
        );

    const transportType = config.audio_transport || 'audiosocket';
    const audiosocketConfig = config.audiosocket || {};
    const externalMediaConfig = config.external_media || {};
    const websocketMediaConfig = config.websocket_media || websocketStatus?.config || {};
    const websocketAuth = websocketMediaConfig.auth || {};
    const websocketTLS = websocketMediaConfig.tls || {};
    const websocketModules = asteriskStatus?.live?.modules || {};
    const websocketVersion = asteriskStatus?.live?.asterisk_version || null;
    const websocketModuleNames = [
        'chan_websocket',
        'res_websocket_client',
        'res_http_websocket',
        'res_ari_channels',
    ];
    const websocketModulesReady = websocketModuleNames.every(
        moduleName => websocketModules[moduleName] === 'Running'
    );
    const websocketVersionSupported = asteriskStatus?.live?.websocket_media_supported;
    const websocketVersionReason = asteriskStatus?.live?.websocket_media_reason;
    const websocketCapabilityUnknown =
        !websocketVersion ||
        /unavailable|could not be verified/i.test(websocketVersionReason || '');
    const websocketCapabilityLabel = websocketVersionSupported
        ? 'Passed'
        : websocketCapabilityUnknown || websocketVersionSupported === undefined
          ? 'Not verified'
          : 'Unsupported';
    const websocketClientHost =
        websocketMediaConfig.advertise_host ?? websocketMediaConfig.bind_host ?? '127.0.0.1';
    const websocketClientUriHost =
        websocketClientHost.includes(':') && !websocketClientHost.startsWith('[')
            ? `[${websocketClientHost}]`
            : websocketClientHost;
    const websocketFieldErrors = validateWebSocketSnippet(websocketMediaConfig);
    const websocketSnippetValid = Object.keys(websocketFieldErrors).length === 0;
    const websocketClientSnippet = websocketSnippetValid
        ? `[${websocketMediaConfig.connection_name ?? 'aava_media'}]\ntype = websocket_client\nuri = ${websocketTLS.enabled ? 'wss' : 'ws'}://${websocketClientUriHost}:${websocketMediaConfig.port ?? 8787}${websocketMediaConfig.path ?? '/media'}\nprotocols = media\nusername = ${websocketAuth.username ?? 'aava_media'}\npassword = REPLACE_WITH_SECRET\nconnection_type = per_call_config\nconnection_timeout = 500\nreconnect_interval = 500\nreconnect_attempts = 5\ntls_enabled = ${websocketTLS.enabled ? 'yes' : 'no'}`
        : null;
    const configuredSecretReference = websocketAuth.password_env ?? 'ASTERISK_MEDIA_WS_PASSWORD';
    const secretReferenceIsSaved = websocketStatus?.secret_reference === configuredSecretReference;
    const engineConnected = systemHealth?.ai_engine?.status === 'connected';
    const runningTransport = engineConnected
        ? systemHealth?.ai_engine?.details?.audio_transport || null
        : null;
    const runningWebSocketListening =
        runningTransport === 'websocket'
            ? systemHealth?.ai_engine?.details?.websocket_media?.listening
            : undefined;
    const runningWebSocketHealth = systemHealth?.ai_engine?.details?.websocket_media;
    const selectionEdited = transportType !== savedTransport;

    // Determine banner message based on apply method
    const bannerMessage =
        applyMethod === 'hot_reload'
            ? 'Changes saved. Apply Changes to hot reload AI Engine without a restart.'
            : savedTransport === 'websocket'
              ? 'Saved WebSocket transport changes require an AI Engine recreate to load YAML and the referenced .env secret.'
              : 'Changes to transport configurations require an AI Engine restart to take effect.';

    const buttonLabel =
        applyMethod === 'hot_reload'
            ? 'Apply Changes'
            : savedTransport === 'websocket'
              ? 'Recreate AI Engine'
              : 'Restart AI Engine';

    return (
        <div className="space-y-6">
            {restartRequired && (
                <div className="bg-orange-500/15 border-orange-500/30 border text-yellow-800 dark:text-yellow-500 p-4 rounded-md flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex items-center">
                        <AlertCircle className="w-5 h-5 mr-2 shrink-0" />
                        <span>
                            {bannerMessage}
                            {applyProgress && (
                                <span className="block text-xs opacity-80 mt-1">{applyProgress}</span>
                            )}
                        </span>
                    </div>
                    <button
                        onClick={() => handleApplyAIEngine(false)}
                        disabled={restartingEngine}
                        className="flex items-center text-xs px-3 py-1.5 rounded transition-colors bg-orange-500 text-white hover:bg-orange-600 font-medium disabled:opacity-50"
                    >
                        {restartingEngine ? (
                            <Loader2 className="w-3 h-3 mr-1.5 animate-spin" />
                        ) : (
                            <RefreshCw className="w-3 h-3 mr-1.5" />
                        )}
                        {restartingEngine ? 'Applying...' : buttonLabel}
                    </button>
                </div>
            )}

            <div className="flex flex-col items-start gap-3 sm:flex-row sm:justify-between sm:items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Audio Transport</h1>
                    <p className="text-muted-foreground mt-1">
                        Configure how audio is transported between Asterisk and the AI Agent.
                    </p>
                </div>
                <button
                    onClick={handleSave}
                    disabled={saving}
                    className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                >
                    <Save className="w-4 h-4 mr-2" />
                    {saving ? 'Saving...' : 'Save Changes'}
                </button>
            </div>

            <ConfigSection
                title="Asterisk Configuration"
                description="Core Asterisk integration settings."
            >
                <ConfigCard>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                        <FormInput
                            label="Stasis Application Name"
                            value={config.asterisk?.app_name || 'asterisk-ai-voice-agent'}
                            onChange={e =>
                                updateSectionConfig('asterisk', 'app_name', e.target.value)
                            }
                            tooltip="Name of the Stasis application in your dialplan. Must match the app name in your Asterisk configuration."
                        />
                        <FormInput
                            label="ARI Ping Interval (seconds)"
                            type="number"
                            min={5}
                            max={60}
                            step={1}
                            value={config.asterisk?.ws_ping_interval_sec ?? 10}
                            onChange={e =>
                                updateSectionConfig(
                                    'asterisk',
                                    'ws_ping_interval_sec',
                                    normalizeAriPingSeconds(e.target.value)
                                )
                            }
                            tooltip="How often the engine probes an otherwise-idle ARI WebSocket. Lower values detect silent network loss sooner but increase sensitivity to event-loop stalls."
                        />
                        <FormInput
                            label="ARI Ping Timeout (seconds)"
                            type="number"
                            min={5}
                            max={60}
                            step={1}
                            value={config.asterisk?.ws_ping_timeout_sec ?? 10}
                            onChange={e =>
                                updateSectionConfig(
                                    'asterisk',
                                    'ws_ping_timeout_sec',
                                    normalizeAriPingSeconds(e.target.value)
                                )
                            }
                            tooltip="How long the engine waits for the ARI pong. The default interval plus timeout bounds silent-failure detection to approximately 20 seconds."
                        />
                    </div>
                </ConfigCard>
            </ConfigSection>

            <ConfigSection title="Transport Type" description="Select the audio transport method.">
                <ConfigCard>
                    <FormSelect
                        label="Transport Method"
                        value={transportType}
                        onChange={e => updateConfig('audio_transport', e.target.value)}
                        options={[
                            { value: 'audiosocket', label: 'AudioSocket (Default)' },
                            { value: 'externalmedia', label: 'External Media (RTP)' },
                            { value: 'websocket', label: 'WebSocket (Asterisk Media WebSocket)' },
                        ]}
                        description="Choose AudioSocket, External Media RTP, or Asterisk Media WebSocket. Transport changes always require an AI Engine restart."
                    />
                </ConfigCard>
            </ConfigSection>

            {transportType === 'audiosocket' && (
                <ConfigSection
                    title="AudioSocket Settings"
                    description="Configuration for the AudioSocket server."
                >
                    <ConfigCard>
                        <div className="space-y-6">
                            <h4 className="text-sm font-medium text-muted-foreground uppercase tracking-wider">
                                Network Configuration
                            </h4>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                <FormInput
                                    label="Bind Host"
                                    value={audiosocketConfig.host || '127.0.0.1'}
                                    onChange={e =>
                                        updateSectionConfig('audiosocket', 'host', e.target.value)
                                    }
                                    tooltip="IP address the AudioSocket server listens on. Use 0.0.0.0 to listen on all interfaces."
                                />
                                <FormInput
                                    label="Advertise Host"
                                    value={
                                        audiosocketConfig.advertise_host ||
                                        audiosocketConfig.host ||
                                        '127.0.0.1'
                                    }
                                    onChange={e =>
                                        updateSectionConfig(
                                            'audiosocket',
                                            'advertise_host',
                                            e.target.value
                                        )
                                    }
                                    tooltip="IP address Asterisk connects to. For NAT/VPN deployments, set this to your routable IP (VPN IP, public IP, or LAN IP). Leave as Bind Host for same-host deployments."
                                />
                                <FormInput
                                    label="Port"
                                    type="number"
                                    value={audiosocketConfig.port || 8090}
                                    onChange={e =>
                                        updateSectionConfig(
                                            'audiosocket',
                                            'port',
                                            parseInt(e.target.value)
                                        )
                                    }
                                    tooltip="TCP port for AudioSocket connections (default: 8090)."
                                />
                                <FormInput
                                    label="Format"
                                    value={audiosocketConfig.format || 'slin'}
                                    onChange={e =>
                                        updateSectionConfig('audiosocket', 'format', e.target.value)
                                    }
                                    tooltip="Audio format (e.g., slin, ulaw)"
                                />
                            </div>
                        </div>
                    </ConfigCard>
                </ConfigSection>
            )}

            {transportType === 'externalmedia' && (
                <ConfigSection
                    title="External Media (RTP) Settings"
                    description="Configuration for RTP-based audio transport."
                >
                    <ConfigCard>
                        <div className="space-y-6">
                            <div className="flex items-start gap-3 rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900 dark:border-blue-800 dark:bg-blue-900/20 dark:text-blue-200">
                                <AlertCircle className="mt-0.5 h-5 w-5 shrink-0" />
                                <div>
                                    <p className="font-medium">Supported Audio Profiles</p>
                                    <p className="mt-1">
                                        ExternalMedia RTP is supported with the 8 kHz{' '}
                                        <code>telephony_ulaw_8k</code> and{' '}
                                        <code>telephony_enhanced_8k</code> profiles. The{' '}
                                        <code>wideband_pcm_16k</code> profile is AudioSocket-only;
                                        select AudioSocket for end-to-end 16 kHz audio.
                                    </p>
                                </div>
                            </div>

                            <h4 className="text-sm font-medium text-muted-foreground uppercase tracking-wider">
                                Network Configuration
                            </h4>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                <FormInput
                                    label="RTP Bind Host"
                                    value={externalMediaConfig.rtp_host || '127.0.0.1'}
                                    onChange={e =>
                                        updateSectionConfig(
                                            'external_media',
                                            'rtp_host',
                                            e.target.value
                                        )
                                    }
                                    tooltip="IP address the RTP server listens on. Use 0.0.0.0 to listen on all interfaces."
                                />
                                <FormInput
                                    label="Advertise Host"
                                    value={
                                        externalMediaConfig.advertise_host ||
                                        externalMediaConfig.rtp_host ||
                                        '127.0.0.1'
                                    }
                                    onChange={e =>
                                        updateSectionConfig(
                                            'external_media',
                                            'advertise_host',
                                            e.target.value
                                        )
                                    }
                                    tooltip="IP address Asterisk sends RTP to. For NAT/VPN deployments, set this to your routable IP (VPN IP, public IP, or LAN IP). Leave as Bind Host for same-host deployments."
                                />
                                <FormInput
                                    label="RTP Port"
                                    type="number"
                                    value={externalMediaConfig.rtp_port || 18080}
                                    onChange={e =>
                                        updateSectionConfig(
                                            'external_media',
                                            'rtp_port',
                                            parseInt(e.target.value)
                                        )
                                    }
                                    tooltip="Base UDP port for RTP streams (default: 18080)."
                                />
                                <FormInput
                                    label="Port Range"
                                    value={externalMediaConfig.port_range || '18080:18099'}
                                    onChange={e =>
                                        updateSectionConfig(
                                            'external_media',
                                            'port_range',
                                            e.target.value
                                        )
                                    }
                                    placeholder="18080:18099"
                                    tooltip="Range of UDP ports for concurrent calls (format: start:end, e.g., 18080:18099)."
                                />
                                <FormInput
                                    label="Allowed Remote Hosts"
                                    value={
                                        Array.isArray(externalMediaConfig.allowed_remote_hosts)
                                            ? externalMediaConfig.allowed_remote_hosts.join(', ')
                                            : externalMediaConfig.allowed_remote_hosts || ''
                                    }
                                    onChange={e => {
                                        const value = e.target.value.trim();
                                        const hosts = value
                                            ? value
                                                  .split(',')
                                                  .map(h => h.trim())
                                                  .filter(h => h)
                                            : [];
                                        updateSectionConfig(
                                            'external_media',
                                            'allowed_remote_hosts',
                                            hosts.length > 0 ? hosts : null
                                        );
                                    }}
                                    placeholder="e.g., 192.168.1.100, 10.0.0.5"
                                    tooltip="IP addresses allowed to send RTP packets. Required when ASTERISK_HOST is a hostname. Comma-separated for multiple IPs."
                                />
                            </div>

                            <div className="border-t border-border my-4"></div>

                            <h4 className="text-sm font-medium text-muted-foreground uppercase tracking-wider">
                                Asterisk-side Configuration
                            </h4>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                <FormSelect
                                    label="Codec"
                                    value={externalMediaConfig.codec || 'ulaw'}
                                    onChange={e =>
                                        updateSectionConfig(
                                            'external_media',
                                            'codec',
                                            e.target.value
                                        )
                                    }
                                    options={[
                                        { value: 'ulaw', label: 'μ-law (8kHz)' },
                                        { value: 'alaw', label: 'A-law (8kHz)' },
                                        { value: 'slin', label: 'SLIN (8kHz)' },
                                        { value: 'slin16', label: 'SLIN16 (16kHz)' },
                                    ]}
                                    description="Codec Asterisk sends/receives."
                                />
                                <FormSelect
                                    label="Direction"
                                    value={externalMediaConfig.direction || 'both'}
                                    onChange={e =>
                                        updateSectionConfig(
                                            'external_media',
                                            'direction',
                                            e.target.value
                                        )
                                    }
                                    options={[
                                        { value: 'both', label: 'Both' },
                                        { value: 'sendonly', label: 'Send Only' },
                                        { value: 'recvonly', label: 'Receive Only' },
                                    ]}
                                />
                            </div>

                            <div className="border-t border-border my-4"></div>

                            <h4 className="text-sm font-medium text-muted-foreground uppercase tracking-wider">
                                Engine-side Configuration
                            </h4>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                <FormSelect
                                    label="Internal Format"
                                    value={externalMediaConfig.format || 'slin16'}
                                    onChange={e =>
                                        updateSectionConfig(
                                            'external_media',
                                            'format',
                                            e.target.value
                                        )
                                    }
                                    options={[
                                        { value: 'slin', label: 'SLIN (8kHz)' },
                                        { value: 'slin16', label: 'SLIN16 (16kHz)' },
                                        { value: 'ulaw', label: 'μ-law (8kHz)' },
                                    ]}
                                    description="Engine internal format. Pipelines typically expect 16kHz PCM16 (slin16)."
                                />
                                <FormInput
                                    label="Sample Rate (Hz)"
                                    type="number"
                                    value={externalMediaConfig.sample_rate || 16000}
                                    onChange={e =>
                                        updateSectionConfig(
                                            'external_media',
                                            'sample_rate',
                                            parseInt(e.target.value)
                                        )
                                    }
                                    tooltip="Auto-inferred from format if not set."
                                />
                            </div>

                            <div className="border border-amber-300/40 rounded-lg p-4 bg-amber-500/5">
                                <FormSwitch
                                    label="External Media Expert Settings"
                                    description="Expose RTP source endpoint hardening controls."
                                    checked={showExternalMediaExpert}
                                    onChange={e => setShowExternalMediaExpert(e.target.checked)}
                                    className="mb-0 border-0 p-0 bg-transparent"
                                />
                                <p
                                    className={`text-xs mt-2 ${showExternalMediaExpert ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'}`}
                                >
                                    {showExternalMediaExpert
                                        ? 'Warning: incorrect settings can drop RTP packets or break media connectivity.'
                                        : 'Expert values are visible and read-only until enabled.'}
                                </p>
                                <div className="mt-3">
                                    <FormSwitch
                                        label="Lock Remote Endpoint"
                                        description="Drop RTP packets if source host/port changes mid-call."
                                        checked={externalMediaConfig.lock_remote_endpoint ?? true}
                                        onChange={e =>
                                            updateSectionConfig(
                                                'external_media',
                                                'lock_remote_endpoint',
                                                e.target.checked
                                            )
                                        }
                                        disabled={!showExternalMediaExpert}
                                    />
                                    <p className="text-xs text-muted-foreground mt-2">
                                        Security hardening: keep enabled unless your network path
                                        legitimately rewrites RTP source mid-call.
                                    </p>
                                </div>
                            </div>
                        </div>
                    </ConfigCard>
                </ConfigSection>
            )}

            {transportType === 'websocket' && (
                <ConfigSection
                    title="Asterisk Media WebSocket Settings"
                    description="Asterisk opens one authenticated outbound media WebSocket to the AI Engine for each call."
                >
                    <ConfigCard>
                        <div className="space-y-6">
                            <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900 dark:border-blue-800 dark:bg-blue-900/20 dark:text-blue-200">
                                <p className="font-medium">Asterisk-outbound topology</p>
                                <p className="mt-1">
                                    Calls continue through the existing Stasis dialplan. Configure
                                    the named
                                    <code className="mx-1">websocket_client.conf</code> client
                                    below, then recreate the AI Engine. Direct{' '}
                                    <code>Dial(WebSocket/...)</code> and application-outbound
                                    connections are not part of this transport.
                                </p>
                            </div>

                            <div
                                className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4 text-sm"
                                role="status"
                                aria-live="polite"
                                aria-label="WebSocket transport readiness"
                            >
                                <div className="rounded border p-3">
                                    <p className="text-muted-foreground">Saved vs running</p>
                                    <p
                                        className={`font-medium mt-1 ${selectionEdited || (runningTransport && runningTransport !== savedTransport) ? 'text-amber-600' : ''}`}
                                    >
                                        Saved: {transportLabel(savedTransport)}
                                    </p>
                                    <p className="text-xs text-muted-foreground mt-1">
                                        {selectionEdited
                                            ? `Editor: ${transportLabel(transportType)}; `
                                            : ''}
                                        Running: {transportLabel(runningTransport)}
                                        {runningTransport === 'websocket'
                                            ? runningWebSocketListening === true
                                                ? '; listener is bound'
                                                : runningWebSocketListening === false
                                                  ? '; listener is not bound'
                                                  : '; listener state is unknown'
                                            : ''}
                                    </p>
                                    {runningTransport === 'websocket' && (
                                        <p className="text-xs text-muted-foreground mt-1">
                                            Running controls:{' '}
                                            {systemHealth?.ai_engine?.details?.websocket_media
                                                ?.requested_control_format || 'unknown'}
                                            {' → '}
                                            {systemHealth?.ai_engine?.details?.websocket_media
                                                ?.effective_control_format || 'unresolved'}
                                            . Editor controls:{' '}
                                            {websocketMediaConfig.control_format || 'json'}.
                                        </p>
                                    )}
                                    {(selectionEdited ||
                                        (runningTransport &&
                                            runningTransport !== savedTransport)) && (
                                        <p className="text-xs text-amber-700 dark:text-amber-400 mt-1">
                                            The editor or saved file does not match the running
                                            engine yet.
                                        </p>
                                    )}
                                </div>
                                <div className="rounded border p-3">
                                    <p className="text-muted-foreground">Asterisk version gate</p>
                                    <p
                                        className={`font-medium mt-1 ${websocketCapabilityLabel === 'Passed' ? 'text-green-600' : 'text-amber-600'}`}
                                    >
                                        {websocketCapabilityLabel}
                                    </p>
                                    <p className="text-xs text-muted-foreground mt-1">
                                        {websocketVersion ? `${websocketVersion}. ` : ''}
                                        {websocketVersionReason ||
                                            'The saved WebSocket selection must be probed before this gate can pass.'}
                                    </p>
                                </div>
                                <div className="rounded border p-3">
                                    <p className="text-muted-foreground">
                                        Saved-selection PBX probe
                                    </p>
                                    <p
                                        className={`font-medium mt-1 ${websocketModulesReady ? 'text-green-600' : 'text-amber-600'}`}
                                    >
                                        {Object.keys(websocketModules).length
                                            ? websocketModulesReady
                                                ? 'Probe passed'
                                                : 'Check Asterisk setup'
                                            : 'Not probed'}
                                    </p>
                                    <p className="text-xs text-muted-foreground mt-1">
                                        chan_websocket, res_websocket_client, res_http_websocket,
                                        res_ari_channels
                                    </p>
                                    <p className="text-xs text-muted-foreground mt-1">
                                        This saved-selection probe is guidance only. Running-engine
                                        admission uses its own module inventory and timing check.
                                    </p>
                                </div>
                                <div className="rounded border p-3">
                                    <p className="text-muted-foreground">
                                        Saved secret configuration
                                    </p>
                                    <p
                                        className={`font-medium mt-1 ${websocketStatus?.secret_present && secretReferenceIsSaved ? 'text-green-600' : 'text-amber-600'}`}
                                    >
                                        {!websocketStatus
                                            ? 'Not checked'
                                            : !secretReferenceIsSaved
                                              ? 'Save to check'
                                              : websocketStatus.secret_present
                                                ? 'Present in saved .env'
                                                : 'Missing from saved .env'}
                                    </p>
                                    <p className="text-xs text-muted-foreground mt-1">
                                        This checks {configuredSecretReference} in the saved{' '}
                                        <code>.env</code>; it does not prove the running engine
                                        loaded it. The value is never returned or stored in YAML.
                                    </p>
                                </div>
                            </div>

                            {runningTransport === 'websocket' &&
                                runningWebSocketHealth?.modules_ready === false && (
                                    <div
                                        role="alert"
                                        className="rounded border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive"
                                    >
                                        <p className="font-medium">
                                            Running WebSocket prerequisites failed
                                        </p>
                                        <p className="mt-1">
                                            {runningWebSocketHealth.module_reason ||
                                                'The running engine could not verify the required WebSocket and timing modules.'}
                                        </p>
                                    </div>
                                )}

                            <div className="rounded-lg border border-amber-300/40 bg-amber-500/5 p-4 text-sm">
                                <p className="font-medium">Qualification boundary</p>
                                <p className="mt-1 text-muted-foreground">
                                    Live evidence currently covers one development OpenAI Realtime
                                    flow with <code>ulaw</code> at 8 kHz only. Other providers and
                                    the <code>alaw</code>, <code>slin</code>, and{' '}
                                    <code>slin16</code> paths remain unqualified; a version or
                                    module check is not call certification.
                                </p>
                                <p className="mt-2 text-muted-foreground">
                                    Profile policy can select <code>ulaw</code>, <code>alaw</code>,{' '}
                                    <code>slin</code> (8 kHz), or <code>slin16</code> (16 kHz).
                                    Built-in telephony μ-law profiles select <code>ulaw</code>;
                                    linear 8 kHz profiles select <code>slin</code>; the wideband PCM
                                    profile selects <code>slin16</code>.
                                </p>
                            </div>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                <FormInput
                                    label="Connection Name"
                                    value={websocketMediaConfig.connection_name ?? 'aava_media'}
                                    onChange={e =>
                                        updateSectionConfig(
                                            'websocket_media',
                                            'connection_name',
                                            e.target.value
                                        )
                                    }
                                    tooltip="The matching websocket_client.conf section name Asterisk uses as external_host."
                                    error={websocketFieldErrors.connection_name}
                                />
                                <FormInput
                                    label="Listener Path"
                                    value={websocketMediaConfig.path ?? '/media'}
                                    onChange={e =>
                                        updateSectionConfig(
                                            'websocket_media',
                                            'path',
                                            e.target.value
                                        )
                                    }
                                    tooltip="Absolute Media WebSocket listener path."
                                    error={websocketFieldErrors.path}
                                />
                                <FormInput
                                    label="Bind Host"
                                    value={websocketMediaConfig.bind_host || '127.0.0.1'}
                                    onChange={e =>
                                        updateSectionConfig(
                                            'websocket_media',
                                            'bind_host',
                                            e.target.value
                                        )
                                    }
                                    tooltip="IP address the AI Engine listener binds to."
                                />
                                <FormInput
                                    label="Advertise Host"
                                    value={
                                        websocketMediaConfig.advertise_host ??
                                        websocketMediaConfig.bind_host ??
                                        '127.0.0.1'
                                    }
                                    onChange={e =>
                                        updateSectionConfig(
                                            'websocket_media',
                                            'advertise_host',
                                            e.target.value
                                        )
                                    }
                                    tooltip="Host placed in the generated Asterisk client URI; it must route from Asterisk to AI Engine."
                                    error={websocketFieldErrors.advertise_host}
                                />
                                <FormInput
                                    label="Listener Port"
                                    type="number"
                                    min={1024}
                                    max={65535}
                                    value={websocketMediaConfig.port ?? 8787}
                                    onChange={e =>
                                        updateSectionConfig(
                                            'websocket_media',
                                            'port',
                                            parseInt(e.target.value, 10)
                                        )
                                    }
                                    error={websocketFieldErrors.port}
                                />
                                <FormInput
                                    label="Allowed Asterisk Hosts"
                                    value={(
                                        websocketMediaConfig.allowed_remote_hosts || ['127.0.0.1']
                                    ).join(', ')}
                                    onChange={e =>
                                        updateSectionConfig(
                                            'websocket_media',
                                            'allowed_remote_hosts',
                                            e.target.value
                                                .split(',')
                                                .map(host => host.trim())
                                                .filter(Boolean)
                                        )
                                    }
                                    tooltip="IP addresses permitted to open media connections. Comma-separated."
                                />
                            </div>

                            <div className="border-t border-border pt-6">
                                <h4 className="text-sm font-medium text-muted-foreground uppercase tracking-wider mb-4">
                                    Protocol and limits
                                </h4>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                    <FormSelect
                                        label="Fallback Wire Format"
                                        value={websocketMediaConfig.fallback_format || 'ulaw'}
                                        onChange={e =>
                                            updateSectionConfig(
                                                'websocket_media',
                                                'fallback_format',
                                                e.target.value
                                            )
                                        }
                                        options={[
                                            { value: 'ulaw', label: 'μ-law (8 kHz)' },
                                            { value: 'alaw', label: 'A-law (8 kHz)' },
                                            { value: 'slin', label: 'SLIN (8 kHz)' },
                                            { value: 'slin16', label: 'SLIN16 (16 kHz)' },
                                        ]}
                                        tooltip="Used only when a per-call audio profile has no explicit wire format; availability here is not live provider/codec certification."
                                    />
                                    <FormSelect
                                        label="Control Format"
                                        value={websocketMediaConfig.control_format || 'json'}
                                        onChange={e =>
                                            updateSectionConfig(
                                                'websocket_media',
                                                'control_format',
                                                e.target.value
                                            )
                                        }
                                        options={[
                                            { value: 'json', label: 'JSON (default)' },
                                            {
                                                value: 'auto',
                                                label: 'Auto (experimental legacy opt-in)',
                                            },
                                            {
                                                value: 'plain',
                                                label: 'Plain (experimental: 20.17.0 only)',
                                            },
                                        ]}
                                    />
                                    <p className="text-xs text-muted-foreground md:col-span-2">
                                        Auto selects JSON on 20.18+/22.8+/23.2+, or plain on exactly
                                        20.17.0; other versions fail closed. No fallback after
                                        connection errors. Saved changes require engine recreation;
                                        active calls retain their protocol. Plain provider/pipeline
                                        combinations require live qualification.
                                    </p>
                                    <FormInput
                                        label="Direction"
                                        value="Both (fixed for v1)"
                                        disabled
                                        tooltip="Duplex media only; dynamic direction is not exposed."
                                    />
                                    <FormInput
                                        label="Maximum Connections"
                                        type="number"
                                        min={1}
                                        max={10000}
                                        value={websocketMediaConfig.max_connections ?? 100}
                                        onChange={e =>
                                            updateSectionConfig(
                                                'websocket_media',
                                                'max_connections',
                                                parseInt(e.target.value, 10)
                                            )
                                        }
                                    />
                                    <FormInput
                                        label="Handshake Timeout (ms)"
                                        type="number"
                                        min={100}
                                        max={60000}
                                        value={websocketMediaConfig.handshake_timeout_ms ?? 5000}
                                        onChange={e =>
                                            updateSectionConfig(
                                                'websocket_media',
                                                'handshake_timeout_ms',
                                                parseInt(e.target.value, 10)
                                            )
                                        }
                                    />
                                    <FormInput
                                        label="MEDIA_START Timeout (ms)"
                                        type="number"
                                        min={100}
                                        max={60000}
                                        value={websocketMediaConfig.media_start_timeout_ms ?? 5000}
                                        onChange={e =>
                                            updateSectionConfig(
                                                'websocket_media',
                                                'media_start_timeout_ms',
                                                parseInt(e.target.value, 10)
                                            )
                                        }
                                    />
                                    <FormInput
                                        label="Drain Timeout (ms)"
                                        type="number"
                                        min={1000}
                                        max={120000}
                                        value={websocketMediaConfig.drain_timeout_ms ?? 30000}
                                        onChange={e =>
                                            updateSectionConfig(
                                                'websocket_media',
                                                'drain_timeout_ms',
                                                parseInt(e.target.value, 10)
                                            )
                                        }
                                    />
                                    <FormInput
                                        label="Pre-start Buffer (ms)"
                                        type="number"
                                        min={0}
                                        max={5000}
                                        value={websocketMediaConfig.pre_start_buffer_ms ?? 200}
                                        onChange={e =>
                                            updateSectionConfig(
                                                'websocket_media',
                                                'pre_start_buffer_ms',
                                                parseInt(e.target.value, 10)
                                            )
                                        }
                                        tooltip="Bounded allowance for binary audio that arrives before MEDIA_START."
                                    />
                                </div>
                            </div>

                            <div className="border-t border-border pt-6">
                                <h4 className="text-sm font-medium text-muted-foreground uppercase tracking-wider mb-4">
                                    Authentication and TLS
                                </h4>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                    <FormInput
                                        label="WebSocket Username"
                                        value={websocketAuth.username ?? 'aava_media'}
                                        onChange={e =>
                                            updateNestedSectionConfig(
                                                'websocket_media',
                                                'auth',
                                                'username',
                                                e.target.value
                                            )
                                        }
                                        error={websocketFieldErrors.username}
                                    />
                                    <FormInput
                                        label="Password Environment Variable"
                                        value={
                                            websocketAuth.password_env ??
                                            'ASTERISK_MEDIA_WS_PASSWORD'
                                        }
                                        onChange={e =>
                                            updateNestedSectionConfig(
                                                'websocket_media',
                                                'auth',
                                                'password_env',
                                                e.target.value
                                            )
                                        }
                                        tooltip="Name only. Set the password in .env; it is not stored in YAML or returned by this UI."
                                        error={websocketFieldErrors.password_env}
                                    />
                                </div>
                                <p className="text-sm text-muted-foreground">
                                    Add or rotate the value on the{' '}
                                    <a
                                        className="text-primary underline underline-offset-2"
                                        href="/env"
                                    >
                                        Environment
                                    </a>{' '}
                                    page, then return here. Recreating the AI Engine from this page
                                    loads the saved <code>.env</code>; a plain container restart
                                    does not.
                                </p>
                                <div className="mt-5 rounded border border-amber-300/40 p-4 bg-amber-500/5">
                                    <FormSwitch
                                        label="Enable TLS (wss)"
                                        description="Required for routed or untrusted networks. Loopback deployments may use ws with an authenticated listener."
                                        checked={websocketTLS.enabled ?? false}
                                        onChange={e =>
                                            updateNestedSectionConfig(
                                                'websocket_media',
                                                'tls',
                                                'enabled',
                                                e.target.checked
                                            )
                                        }
                                        className="mb-0 border-0 p-0 bg-transparent"
                                    />
                                    {websocketTLS.enabled && (
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-5">
                                            <FormInput
                                                label="TLS Certificate File"
                                                value={websocketTLS.cert_file || ''}
                                                onChange={e =>
                                                    updateNestedSectionConfig(
                                                        'websocket_media',
                                                        'tls',
                                                        'cert_file',
                                                        e.target.value || null
                                                    )
                                                }
                                            />
                                            <FormInput
                                                label="TLS Key File"
                                                value={websocketTLS.key_file || ''}
                                                onChange={e =>
                                                    updateNestedSectionConfig(
                                                        'websocket_media',
                                                        'tls',
                                                        'key_file',
                                                        e.target.value || null
                                                    )
                                                }
                                            />
                                        </div>
                                    )}
                                </div>
                            </div>

                            <div className="border-t border-border pt-6">
                                <h4 className="text-sm font-medium text-muted-foreground uppercase tracking-wider mb-3">
                                    Asterisk websocket_client.conf template
                                </h4>
                                <p className="text-sm text-muted-foreground mb-3">
                                    Replace <code>REPLACE_WITH_SECRET</code> with the exact URL-safe
                                    secret stored as <code>{configuredSecretReference}</code>, then
                                    add this per-call client stanza on Asterisk. Do not add a{' '}
                                    <code>d(...)</code> dial-string option. Reload the applicable
                                    Asterisk module or restart Asterisk under your operating policy
                                    before testing.
                                </p>
                                {websocketClientSnippet ? (
                                    <pre
                                        className="overflow-x-auto rounded bg-muted p-4 text-xs leading-5"
                                        aria-label="Validated websocket_client.conf template"
                                    >
                                        {websocketClientSnippet}
                                    </pre>
                                ) : (
                                    <div
                                        role="alert"
                                        className="rounded border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive"
                                    >
                                        Fix the highlighted fields before using the generated
                                        Asterisk template. Invalid values are never interpolated
                                        into configuration text.
                                    </div>
                                )}
                                <p className="mt-3 text-sm text-muted-foreground">
                                    To roll back, select the previous transport, save, restart the
                                    AI Engine, and verify a call before removing this inactive
                                    client stanza or secret.
                                </p>
                            </div>
                        </div>
                    </ConfigCard>
                </ConfigSection>
            )}
        </div>
    );
};

export default TransportPage;
