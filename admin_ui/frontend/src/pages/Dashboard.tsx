import { useState, useEffect, useRef } from 'react';
import { Activity, Cpu, HardDrive, RefreshCw, FolderCheck, Wrench, Globe, Tag, Box, CheckCircle2, XCircle, Phone, type LucideIcon } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import { toast } from 'sonner';
import { SystemTopology } from '../components/SystemTopology';
import { ApiErrorInfo, buildDockerAccessHints, describeApiError } from '../utils/apiErrors';
import DonationBanner from '../components/DonationBanner';
import { useDonationReminder } from '../hooks/useDonationReminder';
import { useLiveStatus } from '../hooks/useLiveStatus';
import {
    INITIAL_CONNECTION_STATE,
    reduceConnection,
    type ConnectionSample,
} from '../utils/connectionHysteresis';

interface SystemMetrics {
    cpu: {
        percent: number;
        count: number;
    };
    memory: {
        total: number;
        available: number;
        percent: number;
        used: number;
    };
    disk: {
        total: number;
        free: number;
        percent: number;
    };
}

interface DirectoryCheck {
    status: string;
    message: string;
    [key: string]: any;
}

interface DirectoryHealth {
    overall: 'healthy' | 'warning' | 'error';
    checks: {
        media_dir_configured: DirectoryCheck;
        host_directory: DirectoryCheck;
        asterisk_symlink: DirectoryCheck;
    };
}

interface PlatformInfo {
    project?: { version: string };
    os: { id: string; version: string };
    docker: { version: string | null };
    compose: { version: string | null };
}

interface PlatformResponse {
    platform: PlatformInfo;
    summary: { ready: boolean; passed: number };
}

interface CompactMetricProps {
    title: string;
    value: string;
    subValue?: string;
    icon: LucideIcon;
    color: string;
}

const CompactMetric = ({ title, value, subValue, icon: Icon, color }: CompactMetricProps) => (
    <div className="flex items-center gap-3 px-4 py-3">
        <Icon className={`w-5 h-5 ${color} flex-shrink-0`} />
        <div className="min-w-0">
            <div className="text-xs text-muted-foreground">{title}</div>
            <div className="text-lg font-bold">{value}</div>
            {subValue && <div className="text-[10px] text-muted-foreground truncate">{subValue}</div>}
        </div>
    </div>
);

// Consecutive explicit failures before a card degrades (I4). Matches the
// 2-strike debounce SystemTopology uses for its health indicators, so the top
// cards stop half-flickering to "Loading…" during the single-poll blips an
// AI-engine restart (which this page can trigger) causes.
const CARD_FAIL_THRESHOLD = 2;

const Dashboard = () => {
    const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
    const [directoryHealth, setDirectoryHealth] = useState<DirectoryHealth | null>(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [fixingDirectories, setFixingDirectories] = useState(false);
    const [reconnectingAri, setReconnectingAri] = useState(false);

    const [metricsError, setMetricsError] = useState<ApiErrorInfo | null>(null);
    const [platformError, setPlatformError] = useState<ApiErrorInfo | null>(null);
    const [directoriesError, setDirectoriesError] = useState<ApiErrorInfo | null>(null);
    const [asteriskError, setAsteriskError] = useState<ApiErrorInfo | null>(null);
    const [platformData, setPlatformData] = useState<PlatformResponse | null>(null);
    const [platformLoadFailed, setPlatformLoadFailed] = useState(false);
    const [ariConnected, setAriConnected] = useState<boolean | null>(null);
    const navigate = useNavigate();
    const donation = useDonationReminder();
    const liveStatus = useLiveStatus();

    // Cross-poll hysteresis/debounce streaks. Kept in refs so updating them
    // doesn't trigger renders; the resulting display values land in reactive
    // state. ariState carries the 3-strike last-good reducer (I1); the dir /
    // platform streaks debounce those cards' degrade-to-failure (I4).
    const ariState = useRef(INITIAL_CONNECTION_STATE);
    const dirFailStreak = useRef(0);
    const platformFailStreak = useRef(0);

    const fetchData = async () => {
        const results = await Promise.allSettled([
            axios.get('/api/system/metrics'),
            axios.get('/api/system/directories'),
            axios.get('/api/system/platform'),
            axios.get('/api/system/asterisk-status'),
        ]);

        const [metricsRes, dirHealthRes, platformRes, asteriskRes] = results;

        if (metricsRes.status === 'fulfilled') {
            setMetrics(metricsRes.value.data);
            setMetricsError(null);
        } else {
            const info = describeApiError(metricsRes.reason, '/api/system/metrics');
            console.error('Failed to fetch metrics:', info);
            setMetricsError(info);
        }

        if (dirHealthRes.status === 'fulfilled') {
            dirFailStreak.current = 0;
            setDirectoryHealth(dirHealthRes.value.data);
            setDirectoriesError(null);
        } else {
            // Keep last-good through one failure; only clear after the debounce.
            dirFailStreak.current += 1;
            const info = describeApiError(dirHealthRes.reason, '/api/system/directories');
            console.error('Failed to fetch directory health:', info);
            setDirectoriesError(info);
            if (dirFailStreak.current >= CARD_FAIL_THRESHOLD) {
                setDirectoryHealth(null);
            }
        }

        if (platformRes.status === 'fulfilled') {
            platformFailStreak.current = 0;
            setPlatformData(platformRes.value.data);
            setPlatformLoadFailed(false);
            setPlatformError(null);
        } else {
            platformFailStreak.current += 1;
            const info = describeApiError(platformRes.reason, '/api/system/platform');
            console.error('Failed to fetch platform info:', info);
            setPlatformError(info);
            // Keep last-good platform info through one failure; degrade after.
            if (platformFailStreak.current >= CARD_FAIL_THRESHOLD) {
                setPlatformData(null);
                setPlatformLoadFailed(true);
            }
        }

        // Asterisk pill: feed the hysteresis reducer. A rejected request or a
        // response missing `data.live` is "unknown" (hold previous), NOT false.
        let asteriskSample: ConnectionSample;
        if (asteriskRes.status === 'fulfilled') {
            const reachable = asteriskRes.value.data?.live?.ari_reachable;
            asteriskSample = typeof reachable === 'boolean' ? reachable : 'unknown';
            setAsteriskError(null);
        } else {
            asteriskSample = 'unknown';
            const info = describeApiError(asteriskRes.reason, '/api/system/asterisk-status');
            console.error('Failed to fetch asterisk status:', info);
            setAsteriskError(info);
        }
        ariState.current = reduceConnection(ariState.current, asteriskSample);
        setAriConnected(ariState.current.display);

        setLoading(false);
        setRefreshing(false);
    };

    const handleReconnectAri = async () => {
        setReconnectingAri(true);
        try {
            const res = await axios.post('/api/system/containers/ai_engine/restart?force=false&recreate=true');
            if (res.data?.status === 'warning') {
                toast.warning('Active calls detected', { description: 'Restart AI Engine manually when calls finish.' });
            } else {
                toast.success('AI Engine restarted', { description: 'ARI credentials reloaded. Connection will update shortly.' });
            }
            setTimeout(fetchData, 3000);
        } catch (err: any) {
            toast.error('Failed to restart AI Engine', { description: err?.response?.data?.detail || err?.message || 'Unknown error' });
        } finally {
            setReconnectingAri(false);
        }
    };

    const handleFixDirectories = async () => {
        setFixingDirectories(true);
        try {
            const res = await axios.post('/api/system/directories/fix');
            if (res.data.success) {
                // Refresh directory health
                const dirHealthRes = await axios.get('/api/system/directories');
                setDirectoryHealth(dirHealthRes.data);
                if (res.data.restart_required) {
                    toast.success('Fixes applied!', { description: 'Container restart may be required for changes to take effect.' });
                } else {
                    toast.success('Fixes applied!');
                }
            } else {
                const errors = Array.isArray(res.data.errors) ? res.data.errors.join(', ') : 'Unknown error';
                toast.error('Some fixes failed', { description: errors });
            }
        } catch (err: any) {
            toast.error('Failed to fix directories', { description: err?.message || 'Unknown error' });
        } finally {
            setFixingDirectories(false);
        }
    };

    useEffect(() => {
        const snapshot = liveStatus.snapshot;
        if (!snapshot) return;

        const metricsComponent = snapshot.components.metrics;
        if (metricsComponent?.metrics) {
            setMetrics(metricsComponent.metrics as unknown as SystemMetrics);
            setMetricsError(null);
        }

        const directoriesComponent = snapshot.components.directories;
        if (directoriesComponent?.details?.overall) {
            dirFailStreak.current = 0;
            setDirectoryHealth(directoriesComponent.details as DirectoryHealth);
            setDirectoriesError(null);
        }

        const platformComponent = snapshot.components.platform;
        if (platformComponent?.details?.platform && platformComponent?.details?.summary) {
            platformFailStreak.current = 0;
            setPlatformData(platformComponent.details as PlatformResponse);
            setPlatformLoadFailed(false);
            setPlatformError(null);
        }

        const reachable = snapshot.components.asterisk?.details?.live?.ari_reachable;
        const asteriskSample: ConnectionSample = typeof reachable === 'boolean' ? reachable : 'unknown';
        ariState.current = reduceConnection(ariState.current, asteriskSample);
        setAriConnected(ariState.current.display);
        setAsteriskError(null);

        setLoading(false);
        setRefreshing(false);
    }, [liveStatus.snapshot]);

    useEffect(() => {
        const components = liveStatus.snapshot?.components || {};
        const hasProbeHydration = ['metrics', 'directories', 'platform', 'asterisk'].every(
            name => components[name]
        );
        if (hasProbeHydration) return;
        if (liveStatus.loading && !liveStatus.error) return;

        fetchData();
        const interval = setInterval(fetchData, 5000); // Fallback until live-status hydrates
        return () => clearInterval(interval);
    }, [liveStatus.error, liveStatus.loading, liveStatus.snapshot]);

    const formatBytes = (bytes: number) => {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    };

    // I5 — surface every swallowed poll failure in the one shared banner, not
    // just containers/metrics. Platform / directories / asterisk-status used to
    // fail silently (console.error only).
    const dataErrors = [
        { label: 'Metrics', info: metricsError },
        { label: 'Platform', info: platformError },
        { label: 'Audio Directories', info: directoriesError },
        { label: 'Asterisk Status', info: asteriskError },
    ].filter((e): e is { label: string; info: ApiErrorInfo } => e.info != null);

    if (loading) {
        return (
            <div className="flex items-center justify-center h-full">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
            </div>
        );
    }

    return (
        <div className="space-y-8">
            <div className="flex justify-between items-center">
                <h1 className="text-3xl font-bold tracking-tight">Dashboard</h1>
                <button
                    onClick={() => { setRefreshing(true); fetchData(); }}
                    aria-label="Refresh dashboard"
                    className="p-2 rounded-md hover:bg-accent hover:text-accent-foreground transition-colors"
                    disabled={refreshing}
                >
                    <RefreshCw className={`w-5 h-5 ${refreshing ? 'animate-spin' : ''}`} />
                </button>
            </div>

            {donation.show && (
                <DonationBanner
                    callCount={donation.callCount}
                    onLater={donation.onLater}
                    onDismiss={donation.onDismiss}
                    onDonate={donation.onDonate}
                    onAlreadyDonated={donation.onAlreadyDonated}
                    onKeepReminders={donation.onKeepReminders}
                />
            )}

            {dataErrors.length > 0 && (
                <div className="rounded-lg border border-destructive/20 bg-destructive/10 p-4">
                    <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                            <div className="text-sm font-semibold text-destructive">Some system data could not be loaded</div>
                            <div className="mt-1 text-sm text-muted-foreground">
                                This usually means the Admin UI backend cannot access the Docker daemon (docker socket mount/GID mismatch), or the backend is still starting.
                            </div>
                        </div>
                        <button
                            onClick={() => { setRefreshing(true); fetchData(); }}
                            className="px-3 py-1.5 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 text-sm"
                            disabled={refreshing}
                        >
                            Retry
                        </button>
                    </div>

                    <div className="mt-3 space-y-2 text-sm">
                        {dataErrors.map(({ label, info }) => (
                            <div key={label} className="break-words">
                                <span className="font-medium">{label}:</span>{' '}
                                <span className="text-muted-foreground">
                                    {info.status ? `HTTP ${info.status}` : info.kind}{' '}
                                    {info.detail ? `- ${info.detail}` : ''}
                                </span>
                            </div>
                        ))}
                    </div>

                    <details className="mt-3">
                        <summary className="cursor-pointer text-sm text-muted-foreground hover:text-foreground">
                            Troubleshooting steps (copy/paste)
                        </summary>
                        <div className="mt-2 space-y-2 text-sm">
                            <ul className="list-disc pl-5 space-y-1">
                                {(buildDockerAccessHints(dataErrors[0].info) || []).map((h, idx) => (
                                    <li key={idx}>{h}</li>
                                ))}
                            </ul>
                            <div className="rounded-md bg-muted p-3 font-mono text-xs overflow-auto">
                                docker compose -p asterisk-ai-voice-agent ps{'\n'}
                                docker compose -p asterisk-ai-voice-agent logs --tail=200 admin_ui{'\n'}
                                ls -ln /var/run/docker.sock{'\n'}
                                grep -E '^(DOCKER_SOCK|DOCKER_GID)=' .env || true{'\n'}
                                docker compose -p asterisk-ai-voice-agent up -d --force-recreate admin_ui
                            </div>
                        </div>
                    </details>
                </div>
            )}

            {/* Compact Status Bar - Platform info + Resources */}
            <div className="rounded-lg border border-border bg-card shadow-sm">
                {/* Row 1: Platform Info + System Ready */}
                <div className="flex flex-wrap items-center justify-between px-4 py-2 border-b border-border bg-muted/30">
                    <div className="flex flex-wrap items-center gap-6">
                        {/* System Ready Status */}
                        <div className="flex items-center gap-2">
                            {platformLoadFailed ? (
                                <XCircle className="w-4 h-4 text-red-500" />
                            ) : platformData == null ? (
                                <Activity className="w-4 h-4 text-muted-foreground" />
                            ) : platformData.summary?.ready ? (
                                <CheckCircle2 className="w-4 h-4 text-green-500" />
                            ) : (
                                <XCircle className="w-4 h-4 text-red-500" />
                            )}
                            <span className={`text-sm font-medium ${
                                platformLoadFailed
                                    ? 'text-red-500'
                                    : platformData == null
                                        ? 'text-muted-foreground'
                                    : platformData.summary?.ready
                                        ? 'text-green-500'
                                        : 'text-red-500'
                            }`}>
                                {platformLoadFailed
                                    ? 'Platform info unavailable'
                                    : platformData == null
                                        ? 'Loading...'
                                        : platformData.summary?.ready
                                            ? 'System Ready'
                                            : 'Action Required'}
                            </span>
                            {platformData?.summary?.passed != null && (
                                <span className="text-xs text-muted-foreground">
                                    {platformData.summary.passed} passed
                                </span>
                            )}
                        </div>
                        
                        {/* Divider */}
                        <div className="h-4 w-px bg-border hidden sm:block" />
                        
                        {/* Platform Info */}
                        <div className="flex flex-wrap items-center gap-4 text-xs">
                            <div className="flex items-center gap-1.5">
                                <Globe className="w-3.5 h-3.5 text-muted-foreground" />
                                <span className="text-muted-foreground">OS:</span>
                                <span className="font-medium">{platformData?.platform?.os ? `${platformData.platform.os.id} ${platformData.platform.os.version}` : '--'}</span>
                            </div>
                            <div className="flex items-center gap-1.5">
                                <Tag className="w-3.5 h-3.5 text-muted-foreground" />
                                <span className="text-muted-foreground">AAVA:</span>
                                <span className="font-medium">{platformData?.platform?.project?.version || '--'}</span>
                            </div>
                            <div className="flex items-center gap-1.5">
                                <Box className="w-3.5 h-3.5 text-muted-foreground" />
                                <span className="text-muted-foreground">Docker:</span>
                                <span className="font-medium">{platformData?.platform?.docker?.version || '--'}</span>
                            </div>
                            <div className="flex items-center gap-1.5">
                                <HardDrive className="w-3.5 h-3.5 text-muted-foreground" />
                                <span className="text-muted-foreground">Compose:</span>
                                <span className="font-medium">{platformData?.platform?.compose?.version || '--'}</span>
                            </div>
                        </div>
                    </div>
                </div>
                
                {/* Row 2: Resource Metrics */}
                <div className="grid grid-cols-5 divide-x divide-border">
                    <CompactMetric
                        title="CPU"
                        value={metrics?.cpu?.percent != null ? `${metrics.cpu.percent.toFixed(1)}%` : '--'}
                        subValue={metrics?.cpu?.count != null ? `${metrics.cpu.count} Cores` : undefined}
                        icon={Cpu}
                        color="text-blue-500"
                    />
                    <CompactMetric
                        title="Memory"
                        value={metrics?.memory?.percent != null ? `${metrics.memory.percent.toFixed(1)}%` : '--'}
                        subValue={metrics?.memory ? `${formatBytes(metrics.memory.used)} / ${formatBytes(metrics.memory.total)}` : undefined}
                        icon={Activity}
                        color="text-green-500"
                    />
                    <CompactMetric
                        title="Disk"
                        value={metrics?.disk?.percent != null ? `${metrics.disk.percent.toFixed(1)}%` : '--'}
                        subValue={metrics?.disk ? `${formatBytes(metrics.disk.free)} Free` : undefined}
                        icon={HardDrive}
                        color="text-orange-500"
                    />
                    {/* Asterisk Connection */}
                    <div className="flex items-center gap-3 px-4 py-3">
                        <div
                            className="flex items-center gap-3 cursor-pointer hover:opacity-80 transition-opacity flex-1 min-w-0"
                            onClick={() => navigate('/asterisk')}
                            title="View Asterisk Setup"
                        >
                            <Phone className={`w-5 h-5 flex-shrink-0 ${
                                ariConnected === true ? 'text-green-500' :
                                ariConnected === false ? 'text-red-500' : 'text-muted-foreground'
                            }`} />
                            <div className="min-w-0">
                                <div className="text-xs text-muted-foreground">Asterisk</div>
                                <div className={`text-sm font-semibold ${
                                    ariConnected === true ? 'text-green-500' :
                                    ariConnected === false ? 'text-red-500' : 'text-muted-foreground'
                                }`}>
                                    {ariConnected === true ? 'Connected' : ariConnected === false ? 'Disconnected' : 'Loading...'}
                                </div>
                            </div>
                        </div>
                        {ariConnected === false && (
                            <button
                                onClick={handleReconnectAri}
                                disabled={reconnectingAri}
                                className="ml-auto p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground"
                                title="Restart AI Engine to reconnect ARI"
                            >
                                <Wrench className={`w-3.5 h-3.5 ${reconnectingAri ? 'animate-spin' : ''}`} />
                            </button>
                        )}
                    </div>
                    {/* Compact Directory Health */}
                    <div className="flex items-center gap-3 px-4 py-3">
                        <FolderCheck className={`w-4 h-4 flex-shrink-0 ${
                            directoryHealth?.overall === 'healthy' ? 'text-green-500' : 
                            directoryHealth?.overall === 'warning' ? 'text-yellow-500' : 'text-red-500'
                        }`} />
                        <div className="min-w-0">
                            <div className="text-xs text-muted-foreground">Audio Dirs</div>
                            <div className={`text-sm font-semibold capitalize ${
                                directoryHealth?.overall === 'healthy' ? 'text-green-500' : 
                                directoryHealth?.overall === 'warning' ? 'text-yellow-500' : 'text-red-500'
                            }`}>
                                {directoryHealth?.overall || 'Loading...'}
                            </div>
                        </div>
                        {directoryHealth?.overall !== 'healthy' && directoryHealth && (
                            <button
                                onClick={handleFixDirectories}
                                disabled={fixingDirectories}
                                className="ml-2 p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground"
                                title="Auto-Fix Issues"
                            >
                                <Wrench className={`w-3.5 h-3.5 ${fixingDirectories ? 'animate-spin' : ''}`} />
                            </button>
                        )}
                    </div>
                </div>
            </div>

            {/* Live System Topology */}
            <SystemTopology
                liveStatusEnabled={Boolean(liveStatus.snapshot) && !liveStatus.error}
                liveStatusSnapshot={liveStatus.snapshot}
            />
        </div>
    );
};

export default Dashboard;
