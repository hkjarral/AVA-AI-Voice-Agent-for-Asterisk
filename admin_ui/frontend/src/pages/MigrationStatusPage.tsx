import { useState, useEffect } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useConfirmDialog } from '../hooks/useConfirmDialog';
import { AlertCircle } from 'lucide-react';
import { ConfigSection } from '../components/ui/ConfigSection';

interface MigrationInfo {
    imported?: number;
    skipped?: string[];
    [key: string]: unknown;
}

interface DriftInfo {
    stored_hash?: string;
    current_hash?: string;
    [key: string]: unknown;
}

interface MigrationStatusResponse {
    migration?: MigrationInfo | null;
    drift?: DriftInfo | false | null;
    last_default_promotion?: string | null;
}

const MigrationStatusPage = () => {
    const { confirm } = useConfirmDialog();
    const [status, setStatus] = useState<MigrationStatusResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        loadStatus();
    }, []);

    const loadStatus = async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await axios.get<MigrationStatusResponse>('/api/agents-migration/status');
            setStatus(res.data);
        } catch (e: unknown) {
            const httpStatus = (e as { response?: { status?: number } })?.response?.status;
            if (httpStatus === 401) {
                setError('Not authenticated. Please refresh and log in again.');
            } else {
                setError('Failed to load migration status. Check backend logs and try again.');
            }
        } finally {
            setLoading(false);
        }
    };

    const handleReconcile = async () => {
        const confirmed = await confirm({
            title: 'Import YAML Changes?',
            description: 'Import YAML context changes into the Agents database? This will update agent records to reflect current YAML context definitions.',
            confirmText: 'Import',
        });
        if (!confirmed) return;
        try {
            const res = await axios.post<{ changed: unknown[] }>('/api/agents-migration/reconcile');
            toast.success(`Applied: ${res.data.changed.length} change(s)`);
            loadStatus();
        } catch (e: unknown) {
            const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
            toast.error(detail ?? 'Failed to reconcile');
        }
    };

    const handleAcknowledge = async () => {
        try {
            await axios.post('/api/agents-migration/acknowledge');
            toast.success('Drift acknowledged — database kept as-is');
            loadStatus();
        } catch (e: unknown) {
            const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
            toast.error(detail ?? 'Failed to acknowledge');
        }
    };

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading migration status…</div>;

    const hasDrift = status?.drift && status.drift !== false;

    return (
        <div className="space-y-6">
            {error && (
                <div className="bg-red-500/15 border border-red-500/30 text-red-700 dark:text-red-400 p-4 rounded-md flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <AlertCircle className="w-5 h-5" />
                        {error}
                    </div>
                    <button
                        onClick={() => window.location.reload()}
                        className="flex items-center text-xs px-3 py-1.5 rounded transition-colors bg-red-500 text-white hover:bg-red-600 font-medium"
                    >
                        Reload
                    </button>
                </div>
            )}

            <div>
                <h1 className="text-3xl font-bold tracking-tight">Migration Status</h1>
                <p className="text-muted-foreground mt-1">
                    YAML-to-agents.db migration state and drift detection.
                </p>
            </div>

            <ConfigSection title="Initial Migration" description="Result of the one-time YAML context import into agents.db.">
                <div className="space-y-3 text-sm">
                    <div className="flex gap-3">
                        <span className="font-medium text-muted-foreground w-24 flex-shrink-0">Imported</span>
                        <span className="font-mono">
                            {status?.migration?.imported ?? '—'}
                        </span>
                    </div>
                    <div className="flex gap-3">
                        <span className="font-medium text-muted-foreground w-24 flex-shrink-0">Skipped</span>
                        <span className="font-mono break-all">
                            {status?.migration?.skipped && status.migration.skipped.length > 0
                                ? JSON.stringify(status.migration.skipped)
                                : '—'}
                        </span>
                    </div>
                    {status?.last_default_promotion && (
                        <div className="flex gap-3">
                            <span className="font-medium text-muted-foreground w-24 flex-shrink-0">Last default promoted</span>
                            <span className="font-mono">{status.last_default_promotion}</span>
                        </div>
                    )}
                </div>
            </ConfigSection>

            <ConfigSection title="YAML Drift" description="Detects changes to ai-agent.yaml context definitions since the last migration.">
                {hasDrift ? (
                    <div className="space-y-4">
                        <div className="bg-orange-500/15 border border-orange-500/30 text-yellow-700 dark:text-yellow-400 p-4 rounded-md flex items-center gap-2">
                            <AlertCircle className="w-5 h-5 flex-shrink-0" />
                            <span>
                                YAML context definitions have changed since the last migration. Choose an action below.
                            </span>
                        </div>

                        <div className="space-y-2 text-sm">
                            <div className="flex gap-3">
                                <span className="font-medium text-muted-foreground w-32 flex-shrink-0">Stored hash</span>
                                <span className="font-mono text-xs">
                                    {typeof status?.drift === 'object' && status.drift !== null && status.drift !== false
                                        ? (status.drift.stored_hash ?? '').slice(0, 12) || '—'
                                        : '—'}
                                </span>
                            </div>
                            <div className="flex gap-3">
                                <span className="font-medium text-muted-foreground w-32 flex-shrink-0">Current hash</span>
                                <span className="font-mono text-xs">
                                    {typeof status?.drift === 'object' && status.drift !== null && status.drift !== false
                                        ? (status.drift.current_hash ?? '').slice(0, 12) || '—'
                                        : '—'}
                                </span>
                            </div>
                        </div>

                        <div className="flex gap-3 pt-2">
                            <button
                                onClick={handleReconcile}
                                className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                            >
                                Import YAML changes
                            </button>
                            <button
                                onClick={handleAcknowledge}
                                className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2"
                            >
                                Acknowledge — keep DB as-is
                            </button>
                        </div>
                    </div>
                ) : (
                    <p className="text-sm text-muted-foreground">none — agents.db is in sync with YAML.</p>
                )}
            </ConfigSection>
        </div>
    );
};

export default MigrationStatusPage;
