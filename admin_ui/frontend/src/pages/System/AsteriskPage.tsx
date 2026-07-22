import { useState, useEffect, useCallback } from 'react';
import {
    RefreshCw, CheckCircle2, XCircle, AlertCircle, Server, Wifi, WifiOff,
    Copy, Check, Globe, Monitor, Clock, Package, AppWindow, FileText, Loader2
} from 'lucide-react';
import { ConfigSection } from '../../components/ui/ConfigSection';
import { ConfigCard } from '../../components/ui/ConfigCard';
import axios from 'axios';

interface ManifestCheck {
    ok: boolean;
    detail: string;
}

interface Manifest {
    timestamp: string;
    asterisk_found: boolean;
    asterisk_version: string;
    config_dir: string;
    freepbx: { detected: boolean; version: string };
    checks: Record<string, ManifestCheck>;
}

interface LiveStatus {
    ari_reachable: boolean;
    asterisk_version: string | null;
    uptime: string | null;
    last_reload: string | null;
    app_registered: boolean;
    app_name: string;
    modules: Record<string, string>;
}

interface AsteriskStatus {
    mode: 'local' | 'remote';
    manifest: Manifest | null;
    live: LiveStatus;
}

const MODULE_DESCRIPTIONS: Record<string, string> = {
    app_audiosocket: 'AudioSocket application for streaming audio',
    res_ari: 'Asterisk REST Interface',
    res_stasis: 'Stasis application framework',
    chan_pjsip: 'PJSIP channel driver',
    res_http_websocket: 'HTTP WebSocket support for ARI events',
};

const CONFIG_CHECK_LABELS: Record<string, { label: string; fixHint: string }> = {
    ari_enabled: {
        label: 'ARI Enabled',
        fixHint: "Ensure 'enabled=yes' under [general] in /etc/asterisk/ari.conf or ari_general_custom.conf",
    },
    ari_user: {
        label: 'ARI User Configured',
        fixHint: 'Add an ARI user block in /etc/asterisk/ari_additional_custom.conf:\n\n[AIAgent]\ntype=user\npassword=YourPassword\nread_only=no',
    },
    http_enabled: {
        label: 'HTTP Server Enabled',
        fixHint: "Ensure 'enabled=yes' under [general] in /etc/asterisk/http.conf or http_custom.conf",
    },
    dialplan_context: {
        label: 'Dialplan Context',
        fixHint: 'Add a context in /etc/asterisk/extensions_custom.conf:\n\n[from-ai-agent]\nexten => s,1,NoOp(AI Agent)\n same => n,Stasis({APP_NAME})\n same => n,Hangup()',
    },
};

const CopyButton = ({ text }: { text: string }) => {
    const [copied, setCopied] = useState(false);
    const handleCopy = () => {
        navigator.clipboard.writeText(text).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        }).catch(() => {
            // Clipboard may be unavailable in insecure contexts.
        });
    };
    return (
        <button
            onClick={handleCopy}
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
            title="Copy to clipboard"
        >
            {copied ? <Check className="w-3 h-3 text-green-500" /> : <Copy className="w-3 h-3" />}
            {copied ? 'Copied' : 'Copy'}
        </button>
    );
};

const StatusBadge = ({ ok, label }: { ok: boolean; label?: string }) => (
    <span className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full ${
        ok ? 'bg-green-500/10 text-green-500' : 'bg-red-500/10 text-red-500'
    }`}>
        {ok ? <CheckCircle2 className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
        {label || (ok ? 'OK' : 'Issue')}
    </span>
);

const formatUptime = (isoStr: string | null): string => {
    if (!isoStr) return '—';
    try {
        const start = new Date(isoStr);
        const now = new Date();
        const diffMs = now.getTime() - start.getTime();
        const days = Math.floor(diffMs / 86400000);
        const hours = Math.floor((diffMs % 86400000) / 3600000);
        if (days > 0) return `${days}d ${hours}h`;
        const mins = Math.floor((diffMs % 3600000) / 60000);
        return hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;
    } catch {
        return isoStr;
    }
};

const AsteriskPage = () => {
    const [status, setStatus] = useState<AsteriskStatus | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [expandedFixes, setExpandedFixes] = useState<Record<string, boolean>>({});

    const fetchStatus = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await axios.get('/api/system/asterisk-status');
            setStatus(res.data);
        } catch (err: any) {
            setError(err?.response?.data?.detail || err?.message || 'Failed to fetch Asterisk status');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { fetchStatus(); }, [fetchStatus]);

    const toggleFix = (key: string) => {
        setExpandedFixes(prev => ({ ...prev, [key]: !prev[key] }));
    };

    if (loading && !status) {
        return (
            <div className="flex items-center justify-center min-h-[400px]">
                <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
            </div>
        );
    }

    if (error && !status) {
        return (
            <div className="p-6">
                <div className="flex items-center gap-2 text-red-500 mb-4">
                    <AlertCircle className="w-5 h-5" />
                    <span>{error}</span>
                </div>
                <button onClick={fetchStatus} className="flex items-center gap-2 px-3 py-1.5 rounded bg-primary text-primary-foreground text-sm hover:opacity-90">
                    <RefreshCw className="w-4 h-4" /> Retry
                </button>
            </div>
        );
    }

    const live = status?.live;
    const manifest = status?.manifest;
    const mode = status?.mode;
    const appName = live?.app_name || 'asterisk-ai-voice-agent';

    return (
        <div className="p-6 max-w-5xl mx-auto space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <Server className="w-6 h-6 text-primary" />
                    <div>
                        <h1 className="text-2xl font-bold tracking-tight">Asterisk Setup</h1>
                        <p className="text-sm text-muted-foreground">
                            Configuration status and connectivity checks
                        </p>
                    </div>
                    <span className={`ml-2 inline-flex items-center gap-1 text-xs font-medium px-2.5 py-1 rounded-full ${
                        mode === 'local'
                            ? 'bg-blue-500/10 text-blue-500'
                            : mode === 'remote'
                                ? 'bg-purple-500/10 text-purple-500'
                                : 'bg-muted text-muted-foreground'
                    }`}>
                        {mode === 'local'
                            ? <Monitor className="w-3 h-3" />
                            : mode === 'remote'
                                ? <Globe className="w-3 h-3" />
                                : <AlertCircle className="w-3 h-3" />}
                        {mode === 'local' ? 'Local' : mode === 'remote' ? 'Remote' : 'Unknown'}
                    </span>
                </div>
                <button
                    onClick={fetchStatus}
                    disabled={loading}
                    className="flex items-center gap-2 px-3 py-1.5 rounded-md border border-border text-sm hover:bg-accent transition-colors disabled:opacity-50"
                >
                    <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
                    Refresh
                </button>
            </div>

            {/* Connection Card */}
            <ConfigSection title="ARI Connection" description="Live connection status to Asterisk REST Interface">
                <ConfigCard>
                    <div className="flex items-center gap-3 mb-4">
                        {live?.ari_reachable ? (
                            <Wifi className="w-5 h-5 text-green-500" />
                        ) : (
                            <WifiOff className="w-5 h-5 text-red-500" />
                        )}
                        <span className={`text-lg font-semibold ${live?.ari_reachable ? 'text-green-500' : 'text-red-500'}`}>
                            {live?.ari_reachable ? 'Connected' : 'Not Reachable'}
                        </span>
                    </div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                        <div>
                            <span className="text-muted-foreground">Version</span>
                            <p className="font-medium">{live?.asterisk_version || '—'}</p>
                        </div>
                        <div>
                            <span className="text-muted-foreground">Uptime</span>
                            <p className="font-medium">{formatUptime(live?.uptime || null)}</p>
                        </div>
                        <div>
                            <span className="text-muted-foreground">Last Reload</span>
                            <p className="font-medium">{live?.last_reload ? new Date(live.last_reload).toLocaleString() : '—'}</p>
                        </div>
                        <div>
                            <span className="text-muted-foreground">Mode</span>
                            <p className="font-medium capitalize">{mode}</p>
                        </div>
                    </div>
                    {manifest?.freepbx?.detected && (
                        <div className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
                            <FileText className="w-4 h-4" />
                            FreePBX {manifest.freepbx.version || 'detected'}
                            {manifest.config_dir && <span className="ml-2">({manifest.config_dir})</span>}
                        </div>
                    )}
                </ConfigCard>
            </ConfigSection>

            {/* Modules Checklist */}
            <ConfigSection title="Required Modules" description="Asterisk modules needed for the AI Voice Agent">
                <ConfigCard>
                    {live?.ari_reachable && Object.keys(live.modules).length > 0 ? (
                        <div className="divide-y divide-border">
                            {Object.entries(live.modules).map(([mod, moduleStatus]) => {
                                const isRunning = moduleStatus === 'Running';
                                return (
                                    <div key={mod} className="flex items-center justify-between py-2.5 first:pt-0 last:pb-0">
                                        <div className="flex items-center gap-3">
                                            <Package className="w-4 h-4 text-muted-foreground" />
                                            <div>
                                                <span className="text-sm font-medium">{mod}.so</span>
                                                <p className="text-xs text-muted-foreground">
                                                    {MODULE_DESCRIPTIONS[mod] || ''}
                                                </p>
                                            </div>
                                        </div>
                                        <StatusBadge ok={isRunning} label={moduleStatus} />
                                    </div>
                                );
                            })}
                        </div>
                    ) : !live?.ari_reachable ? (
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                            <AlertCircle className="w-4 h-4" />
                            Module checks require an active ARI connection
                        </div>
                    ) : (
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                            <AlertCircle className="w-4 h-4" />
                            No module data returned from Asterisk
                        </div>
                    )}
                </ConfigCard>
            </ConfigSection>

            {/* App Registration */}
            <ConfigSection title="Application Registration" description="Whether the ARI application is registered and listening">
                <ConfigCard>
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                            <AppWindow className="w-4 h-4 text-muted-foreground" />
                            <div>
                                <span className="text-sm font-medium">{appName}</span>
                                <p className="text-xs text-muted-foreground">Stasis application</p>
                            </div>
                        </div>
                        {live?.ari_reachable ? (
                            <StatusBadge ok={live?.app_registered || false} label={live?.app_registered ? 'Registered' : 'Not Registered'} />
                        ) : (
                            <span className="text-xs text-muted-foreground">Requires ARI connection</span>
                        )}
                    </div>
                    {live?.ari_reachable && !live?.app_registered && (
                        <div className="mt-3 p-3 rounded-md bg-amber-500/5 border border-amber-500/20 text-sm text-amber-600 dark:text-amber-400">
                            The AI Engine must be running and connected to Asterisk for the app to be registered.
                            Start the ai_engine container to register the application.
                        </div>
                    )}
                </ConfigCard>
            </ConfigSection>

            {/* Configuration Checklist (from manifest) */}
            <ConfigSection title="Configuration Checklist" description="Asterisk config file checks from the last preflight run">
                <ConfigCard>
                    {manifest && manifest.checks && Object.keys(manifest.checks).length > 0 ? (
                        <div className="divide-y divide-border">
                            {Object.entries(manifest.checks).map(([key, check]) => {
                                const meta = CONFIG_CHECK_LABELS[key];
                                const label = meta?.label || key.replace(/_/g, ' ').replace(/^module /, '');
                                const isModule = key.startsWith('module_');
                                const fixHint = (meta?.fixHint || '').replace('{APP_NAME}', appName);
                                return (
                                    <div key={key} className="py-2.5 first:pt-0 last:pb-0">
                                        <div className="flex items-center justify-between">
                                            <div className="flex items-center gap-3">
                                                {isModule ? (
                                                    <Package className="w-4 h-4 text-muted-foreground" />
                                                ) : (
                                                    <FileText className="w-4 h-4 text-muted-foreground" />
                                                )}
                                                <div>
                                                    <span className="text-sm font-medium">{label}</span>
                                                    <p className="text-xs text-muted-foreground">{check.detail}</p>
                                                </div>
                                            </div>
                                            <div className="flex items-center gap-2">
                                                <StatusBadge ok={check.ok} />
                                                {!check.ok && fixHint && (
                                                    <button
                                                        onClick={() => toggleFix(key)}
                                                        className="text-xs text-primary hover:underline"
                                                    >
                                                        {expandedFixes[key] ? 'Hide fix' : 'How to fix'}
                                                    </button>
                                                )}
                                            </div>
                                        </div>
                                        {!check.ok && fixHint && expandedFixes[key] && (
                                            <div className="mt-2 ml-7">
                                                <div className="relative">
                                                    <pre className="p-3 rounded-md bg-muted text-xs font-mono whitespace-pre-wrap overflow-x-auto">
                                                        {fixHint}
                                                    </pre>
                                                    <div className="absolute top-2 right-2">
                                                        <CopyButton text={fixHint} />
                                                    </div>
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    ) : manifest === null ? (
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                            <AlertCircle className="w-4 h-4" />
                            <div>
                                <p>No preflight manifest found.</p>
                                <p className="mt-1">Run <code className="px-1.5 py-0.5 rounded bg-muted text-xs font-mono">./preflight.sh</code> on the host to generate it.</p>
                            </div>
                        </div>
                    ) : manifest && manifest.checks && Object.keys(manifest.checks).length === 0 ? (
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                            <AlertCircle className="w-4 h-4 text-amber-500" />
                            <div>
                                <p>Preflight checks were not run (or produced no check results).</p>
                                <p className="mt-1">Run <code className="px-1.5 py-0.5 rounded bg-muted text-xs font-mono">./preflight.sh</code> on the host to generate a manifest with checks.</p>
                            </div>
                        </div>
                    ) : (
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                            <CheckCircle2 className="w-4 h-4 text-green-500" />
                            No configuration issues detected
                        </div>
                    )}
                </ConfigCard>
            </ConfigSection>

            {/* Manifest Info */}
            {manifest && (
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Clock className="w-3 h-3" />
                    Last preflight run: {manifest.timestamp ? new Date(manifest.timestamp).toLocaleString() : 'unknown'}
                    <span className="ml-2">•</span>
                    <span>Run <code className="px-1 py-0.5 rounded bg-muted font-mono">./preflight.sh</code> on the host to refresh</span>
                </div>
            )}
        </div>
    );
};

export default AsteriskPage;
