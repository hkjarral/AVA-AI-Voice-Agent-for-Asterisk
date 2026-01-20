import React, { useState, useEffect } from 'react';
import { Activity, Cpu, HardDrive, RefreshCw, FolderCheck, AlertTriangle, CheckCircle, XCircle, Wrench } from 'lucide-react';
import axios from 'axios';
import { HealthWidget } from '../components/HealthWidget';
import { SystemStatus } from '../components/SystemStatus';
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

const Dashboard = () => {
    const [containers, setContainers] = useState<Container[]>([]);
    const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
    const [directoryHealth, setDirectoryHealth] = useState<DirectoryHealth | null>(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [fixingDirectories, setFixingDirectories] = useState(false);

    const [containersError, setContainersError] = useState<ApiErrorInfo | null>(null);
    const [metricsError, setMetricsError] = useState<ApiErrorInfo | null>(null);

    const fetchData = async () => {
        setContainersError(null);
        setMetricsError(null);

        const results = await Promise.allSettled([
            axios.get('/api/system/containers'),
            axios.get('/api/system/metrics'),
            axios.get('/api/system/directories'),
        ]);

        const [containersRes, metricsRes, dirHealthRes] = results;

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

        setLoading(false);
        setRefreshing(false);
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
                    alert('Fixes applied! Container restart may be required for changes to take effect.');
                }
            } else {
                const errors = Array.isArray(res.data.errors) ? res.data.errors.join('\n') : 'Unknown error';
                const manualSteps = Array.isArray(res.data.manual_steps) ? res.data.manual_steps.join('\n') : '';
                alert(`Some fixes failed:\n${errors}${manualSteps ? `\n\nManual steps:\n${manualSteps}` : ''}`);
            }
        } catch (err: any) {
            alert(`Failed to fix directories: ${err?.message || 'Unknown error'}`);
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

    const MetricCard = ({ title, value, subValue, icon: Icon, color }: any) => (
        <div className="p-6 rounded-lg border border-border bg-card text-card-foreground shadow-sm">
            <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-medium text-muted-foreground">{title}</h3>
                <Icon className={`w-4 h-4 ${color}`} />
            </div>
            <div className="text-2xl font-bold">{value}</div>
            {subValue && <p className="text-xs text-muted-foreground mt-1">{subValue}</p>}
        </div>
    );

    const StatusIcon = ({ status }: { status: string }) => {
        if (status === 'ok') return <CheckCircle className="w-4 h-4 text-green-500" />;
        if (status === 'warning') return <AlertTriangle className="w-4 h-4 text-yellow-500" />;
        return <XCircle className="w-4 h-4 text-red-500" />;
    };

    const DirectoryHealthCard = () => {
        if (!directoryHealth) {
            return (
                <div className="p-6 rounded-lg border border-border bg-card text-card-foreground shadow-sm">
                    <div className="flex items-center justify-between mb-4">
                        <h3 className="text-sm font-medium text-muted-foreground">Audio Directories</h3>
                        <FolderCheck className="w-4 h-4 text-muted-foreground" />
                    </div>
                    <div className="text-sm text-muted-foreground">Loading...</div>
                </div>
            );
        }

        const overallColor = directoryHealth.overall === 'healthy' 
            ? 'text-green-500' 
            : directoryHealth.overall === 'warning' 
                ? 'text-yellow-500' 
                : 'text-red-500';

        const checks = directoryHealth.checks;
        const hasIssues = directoryHealth.overall !== 'healthy';

        return (
            <div className="p-6 rounded-lg border border-border bg-card text-card-foreground shadow-sm">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="text-sm font-medium text-muted-foreground">Audio Directories</h3>
                    <FolderCheck className={`w-4 h-4 ${overallColor}`} />
                </div>
                <div className={`text-2xl font-bold ${overallColor} capitalize`}>
                    {directoryHealth.overall}
                </div>
                
                <div className="mt-3 space-y-3">
                    <div className="text-xs">
                        <div className="flex items-center gap-2">
                            <StatusIcon status={checks.media_dir_configured.status} />
                            <span className="text-muted-foreground font-medium">Media Dir Config</span>
                        </div>
                        <div className="ml-6 text-[10px] text-muted-foreground/70 truncate" title={checks.media_dir_configured.configured_path || checks.media_dir_configured.expected_path}>
                            {checks.media_dir_configured.configured_path || checks.media_dir_configured.expected_path || 'Not set'}
                        </div>
                    </div>
                    <div className="text-xs">
                        <div className="flex items-center gap-2">
                            <StatusIcon status={checks.host_directory.status} />
                            <span className="text-muted-foreground font-medium">Host Directory</span>
                        </div>
                        <div
                            className="ml-6 text-[10px] text-muted-foreground/70 truncate"
                            title={checks.host_directory.message || checks.host_directory.path}
                        >
                            {checks.host_directory.path || 'Unknown'}
                        </div>
                    </div>
                    <div className="text-xs">
                        <div className="flex items-center gap-2">
                            <StatusIcon status={checks.asterisk_symlink.status} />
                            <span className="text-muted-foreground font-medium">Asterisk Symlink</span>
                        </div>
                        <div className="ml-6 text-[10px] text-muted-foreground/70 truncate" title={checks.asterisk_symlink.message}>
                            {checks.asterisk_symlink.target || checks.asterisk_symlink.path || checks.asterisk_symlink.message}
                        </div>
                    </div>
                </div>

                {hasIssues && (
                    <button
                        onClick={handleFixDirectories}
                        disabled={fixingDirectories}
                        className="mt-4 w-full flex items-center justify-center gap-2 px-3 py-2 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                    >
                        <Wrench className="w-3 h-3" />
                        {fixingDirectories ? 'Fixing...' : 'Auto-Fix Issues'}
                    </button>
                )}
            </div>
        );
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

            {/* Health Widget */}
            <HealthWidget />

            {/* System Status - Platform & Cross-Platform Checks (AAVA-126) */}
            <SystemStatus />

            {/* System Metrics */}
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                <MetricCard
                    title="CPU Usage"
                    value={metrics?.cpu?.percent != null ? `${metrics.cpu.percent.toFixed(1)}%` : '--'}
                    subValue={metrics?.cpu?.count != null ? `${metrics.cpu.count} Cores` : '--'}
                    icon={Cpu}
                    color="text-blue-500"
                />
                <MetricCard
                    title="Memory Usage"
                    value={metrics?.memory?.percent != null ? `${metrics.memory.percent.toFixed(1)}%` : '--'}
                    subValue={`${formatBytes(metrics?.memory?.used ?? 0)} / ${formatBytes(metrics?.memory?.total ?? 0)}`}
                    icon={Activity}
                    color="text-green-500"
                />
                <MetricCard
                    title="Disk Usage"
                    value={metrics?.disk?.percent != null ? `${metrics.disk.percent.toFixed(1)}%` : '--'}
                    subValue={`${formatBytes(metrics?.disk?.free ?? 0)} Free`}
                    icon={HardDrive}
                    color="text-orange-500"
                />
                <DirectoryHealthCard />
            </div>
        </div>
    );
};

export default Dashboard;
