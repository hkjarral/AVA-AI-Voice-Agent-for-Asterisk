import { useState, useEffect } from 'react';
import { Activity, Cpu, HardDrive, RefreshCw, FolderCheck, Wrench, Globe, Tag, Box, CheckCircle2, XCircle, Phone, type LucideIcon } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import { toast } from 'sonner';
import { SystemTopology } from '../components/SystemTopology';
import { ApiErrorInfo, buildDockerAccessHints, describeApiError } from '../utils/apiErrors';

interface Container {
    id: string;
    name: string;
    status: string;
    state: string;
}

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

const Dashboard = () => {
    const [, setContainers] = useState<Container[]>([]);
    const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
    const [directoryHealth, setDirectoryHealth] = useState<DirectoryHealth | null>(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [fixingDirectories, setFixingDirectories] = useState(false);
    const [reconnectingAri, setReconnectingAri] = useState(false);

    const [containersError, setContainersError] = useState<ApiErrorInfo | null>(null);
    const [metricsError, setMetricsError] = useState<ApiErrorInfo | null>(null);
    const [platformData, setPlatformData] = useState<PlatformResponse | null>(null);
    const [platformLoadFailed, setPlatformLoadFailed] = useState(false);
    const [ariConnected, setAriConnected] = useState<boolean | null>(null);
    const navigate = useNavigate();

    const fetchData = async () => {
        setContainersError(null);
        setMetricsError(null);

        const results = await Promise.allSettled([
            axios.get('/api/system/containers'),
            axios.get('/api/system/metrics'),
            axios.get('/api/system/directories'),
            axios.get('/api/system/platform'),
            axios.get('/api/system/asterisk-status'),
        ]);

        const [containersRes, metricsRes, dirHealthRes, platformRes, asteriskRes] = results;

        if (containersRes.status === 'fulfilled') {
            setContainers(containersRes.value.data);
        } else {
            const info = describeApiError(containersRes.reason, '/api/system/containers');
            console.error('Failed to fetch containers:', info);
            setContainersError(info);
        }

        if (metricsRes.status === 'fulfilled') {
            setMetrics(metricsRes.value.data);
        } else {
            const info = describeApiError(metricsRes.reason, '/api/system/metrics');
            console.error('Failed to fetch metrics:', info);
            setMetricsError(info);
        }

        if (dirHealthRes.status === 'fulfilled') {
            setDirectoryHealth(dirHealthRes.value.data);
        } else {
            setDirectoryHealth(null);
        }

        if (platformRes.status === 'fulfilled') {
            setPlatformData(platformRes.value.data);
            setPlatformLoadFailed(false);
        } else {
            console.error('Failed to fetch platform info:', platformRes.reason);
            setPlatformData(null);
            setPlatformLoadFailed(true);
        }

        if (asteriskRes.status === 'fulfilled') {
            setAriConnected(asteriskRes.value.data?.live?.ari_reachable ?? false);
        } else {
            setAriConnected(null);
        }

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
        fetchData();
        const interval = setInterval(fetchData, 5000); // Refresh every 5s
        return () => clearInterval(interval);
    }, []);

    const formatBytes = (bytes: number) => {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    };

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

            {(containersError || metricsError) && (
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
                        {containersError && (
                            <div className="break-words">
                                <span className="font-medium">Containers:</span>{' '}
                                <span className="text-muted-foreground">
                                    {containersError.status ? `HTTP ${containersError.status}` : containersError.kind}{' '}
                                    {containersError.detail ? `- ${containersError.detail}` : ''}
                                </span>
                            </div>
                        )}
                        {metricsError && (
                            <div className="break-words">
                                <span className="font-medium">Metrics:</span>{' '}
                                <span className="text-muted-foreground">
                                    {metricsError.status ? `HTTP ${metricsError.status}` : metricsError.kind}{' '}
                                    {metricsError.detail ? `- ${metricsError.detail}` : ''}
                                </span>
                            </div>
                        )}
                    </div>

                    <details className="mt-3">
                        <summary className="cursor-pointer text-sm text-muted-foreground hover:text-foreground">
                            Troubleshooting steps (copy/paste)
                        </summary>
                        <div className="mt-2 space-y-2 text-sm">
                            <ul className="list-disc pl-5 space-y-1">
                                {(buildDockerAccessHints(containersError || metricsError!) || []).map((h, idx) => (
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
            <SystemTopology />
        </div>
    );
};

export default Dashboard;
