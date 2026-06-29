import { useState, useEffect } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { Link } from 'react-router-dom';
import { useConfirmDialog } from '../hooks/useConfirmDialog';
import {
    Plus, Pencil, Trash2, Copy, Star, Users, AlertCircle, Phone,
    TrendingUp, ArrowLeftRight,
} from 'lucide-react';
import { ConfigSection } from '../components/ui/ConfigSection';
import { ConfigCard } from '../components/ui/ConfigCard';
import { Modal } from '../components/ui/Modal';
import AgentForm from '../components/agents/AgentForm';
import type { Agent } from '../components/agents/AgentForm';
import { copyTextToClipboard } from '../utils/clipboard';

// ── Types ────────────────────────────────────────────────────────────────────

interface Summary {
    active_agents: number;
    active_calls: number;
    total_routed: number;
    total_transfers: number;
}

interface AgentStat {
    slug: string;
    calls: number;
    transfers: number;
    avg_duration_seconds: number;
    last_call: string | null;
}

interface DistributionEntry {
    context_name: string;
    count: number;
}

interface RoutingMethods {
    ai_agent: number;
    ai_context: number;
    default: number;
    unknown: number;
}

interface MigrationStatus {
    drift: boolean;
    last_default_promotion?: string | null;
}

// ── KPI card ─────────────────────────────────────────────────────────────────

interface KpiCardProps {
    label: string;
    value: number | string;
    icon: React.ElementType;
    iconColor: string;
}

const KpiCard = ({ label, value, icon: Icon, iconColor }: KpiCardProps) => (
    <div className="bg-card border border-border rounded-lg shadow-sm p-5 flex items-center gap-4">
        <div className={`p-2.5 rounded-md bg-secondary flex-shrink-0`}>
            <Icon className={`w-5 h-5 ${iconColor}`} />
        </div>
        <div className="min-w-0">
            <p className="text-xs text-muted-foreground uppercase tracking-wider font-medium">{label}</p>
            <p className="text-2xl font-bold leading-tight">{value}</p>
        </div>
    </div>
);

// ── Bar panel ─────────────────────────────────────────────────────────────────

interface BarPanelProps {
    title: string;
    rows: { label: string; count: number }[];
}

const BarPanel = ({ title, rows }: BarPanelProps) => {
    const max = Math.max(...rows.map((r) => r.count), 1);
    return (
        <div className="bg-card border border-border rounded-lg shadow-sm p-5 flex-1 min-w-0">
            <h3 className="text-sm font-semibold tracking-tight mb-4">{title}</h3>
            {rows.length === 0 ? (
                <p className="text-sm text-muted-foreground">No data yet.</p>
            ) : (
                <div className="space-y-3">
                    {rows.map((row) => (
                        <div key={row.label}>
                            <div className="flex justify-between items-center mb-1">
                                <span className="text-sm text-foreground/80 truncate pr-2">{row.label}</span>
                                <span className="text-sm font-medium flex-shrink-0">{row.count}</span>
                            </div>
                            <div className="h-2 bg-secondary rounded-full overflow-hidden">
                                <div
                                    className="h-full bg-primary rounded-full transition-all duration-300"
                                    style={{ width: `${(row.count / max) * 100}%` }}
                                />
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};

// ── Helpers ───────────────────────────────────────────────────────────────────

const formatDuration = (seconds: number): string => {
    if (!seconds) return '0s';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    return s > 0 ? `${m}m ${s}s` : `${m}m`;
};

// ── Main page ─────────────────────────────────────────────────────────────────

const AgentsPage = () => {
    const { confirm } = useConfirmDialog();
    const [agents, setAgents] = useState<Agent[]>([]);
    const [statsMap, setStatsMap] = useState<Record<string, AgentStat>>({});
    const [summary, setSummary] = useState<Summary | null>(null);
    const [distribution, setDistribution] = useState<DistributionEntry[]>([]);
    const [routingMethods, setRoutingMethods] = useState<RoutingMethods | null>(null);
    const [systemOk, setSystemOk] = useState<boolean | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [driftBanner, setDriftBanner] = useState(false);
    const [editingAgent, setEditingAgent] = useState<Agent | null | undefined>(undefined);
    const [manualDialplan, setManualDialplan] = useState<{ slug: string; dialplan: string } | null>(null);
    // undefined = form closed, null = new agent, Agent = edit

    useEffect(() => {
        loadAll();
    }, []);

    const loadAll = async () => {
        setLoading(true);
        setError(null);
        try {
            const [
                agentsRes,
                migRes,
                statsBatchRes,
                summaryRes,
                distributionRes,
                routingRes,
            ] = await Promise.all([
                axios.get<Agent[]>('/api/agents'),
                axios.get<MigrationStatus>('/api/agents-migration/status').catch(() => null),
                axios.get<AgentStat[]>('/api/agents/stats-batch').catch(() => null),
                axios.get<Summary>('/api/agents/summary').catch(() => null),
                axios.get<DistributionEntry[]>('/api/agents/distribution').catch(() => null),
                axios.get<RoutingMethods>('/api/agents/routing-methods').catch(() => null),
            ]);

            const agentList = Array.isArray(agentsRes.data) ? agentsRes.data : [];
            setAgents(agentList);

            if (migRes) {
                if (migRes.data.drift) setDriftBanner(true);
                if (migRes.data.last_default_promotion) {
                    toast.info(`Default agent was auto-promoted: ${migRes.data.last_default_promotion}`);
                }
            }

            // stats-batch: map by slug; reset to {} on failure to avoid stale values
            if (statsBatchRes && Array.isArray(statsBatchRes.data)) {
                const map: Record<string, AgentStat> = {};
                for (const s of statsBatchRes.data) {
                    map[s.slug] = s;
                }
                setStatsMap(map);
            } else {
                setStatsMap({});
            }

            if (summaryRes) {
                setSummary(summaryRes.data);
                setSystemOk(true);
            } else {
                setSummary(null);
                setSystemOk(false);
            }

            if (distributionRes && Array.isArray(distributionRes.data)) {
                setDistribution(distributionRes.data);
            } else {
                setDistribution([]);
            }

            if (routingRes) {
                setRoutingMethods(routingRes.data);
            } else {
                setRoutingMethods(null);
            }
        } catch (e: unknown) {
            const status = (e as { response?: { status?: number } })?.response?.status;
            if (status === 401) {
                setError('Not authenticated. Please refresh and log in again.');
            } else {
                setError('Failed to load agents. Check backend logs and try again.');
            }
        } finally {
            setLoading(false);
        }
    };

    const handleCopyDialplan = async (slug: string) => {
        try {
            const res = await axios.get<{ dialplan: string }>(`/api/agents/${slug}/dialplan`);
            const dialplan = res.data?.dialplan;
            if (typeof dialplan !== 'string' || dialplan.length === 0) {
                toast.error('Dialplan snippet was empty');
                return;
            }

            const copied = await copyTextToClipboard(dialplan);
            if (copied) {
                toast.success('Dialplan snippet copied to clipboard');
            } else {
                setManualDialplan({ slug, dialplan });
                toast.error('Clipboard unavailable. Copy the snippet manually.');
            }
        } catch {
            toast.error('Failed to load dialplan snippet');
        }
    };

    const handleMakeDefault = async (slug: string) => {
        try {
            await axios.post(`/api/agents/${slug}/default`);
            toast.success('Default agent updated');
            loadAll();
        } catch (e: unknown) {
            const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
            toast.error(detail ?? 'Failed to set default');
        }
    };

    const handleDelete = async (agent: Agent) => {
        const confirmed = await confirm({
            title: 'Delete Agent?',
            description: `Are you sure you want to delete "${agent.display_name}"? This cannot be undone.`,
            confirmText: 'Delete',
            variant: 'destructive',
        });
        if (!confirmed) return;
        try {
            await axios.delete(`/api/agents/${agent.slug}`);
            toast.success('Agent deleted');
            loadAll();
        } catch (e: unknown) {
            const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
            toast.error(detail ?? 'Failed to delete agent');
        }
    };

    // ── Routing-source bar rows (omit "unknown" if zero) ──────────────────────
    const routingRows = routingMethods
        ? [
              { label: 'AI_AGENT', count: routingMethods.ai_agent },
              { label: 'AI_CONTEXT', count: routingMethods.ai_context },
              { label: 'Default', count: routingMethods.default },
              ...(routingMethods.unknown > 0
                  ? [{ label: 'Unknown', count: routingMethods.unknown }]
                  : []),
          ]
        : [];

    // ── Distribution rows: map context_name → display_name where possible ────
    const agentNameMap: Record<string, string> = {};
    for (const a of agents) {
        agentNameMap[a.slug] = a.display_name;
    }
    const distributionRows = distribution.map((d) => ({
        label: agentNameMap[d.context_name] ?? d.context_name,
        count: d.count,
    }));

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading agents…</div>;

    return (
        <div className="space-y-6">
            {/* Drift banner */}
            {driftBanner && (
                <div className="bg-orange-500/15 border border-orange-500/30 text-yellow-700 dark:text-yellow-400 p-4 rounded-md flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <AlertCircle className="w-5 h-5 flex-shrink-0" />
                        <span>
                            Your <code className="text-xs bg-orange-500/20 px-1 rounded">ai-agent.yaml</code> context entries have changed
                            since the last migration. YAML contexts no longer take effect — agents.db is active.{' '}
                            <Link to="/agents/migration" className="underline font-medium">
                                View migration status
                            </Link>
                        </span>
                    </div>
                </div>
            )}

            {/* Error banner */}
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

            {/* Page header */}
            <div className="flex justify-between items-start">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Multi-Agent System</h1>
                    <p className="text-muted-foreground mt-1">Monitor and manage your agents in real time</p>
                </div>
                {/* System status indicator (read-only) */}
                <div className="flex items-center gap-2 mt-1">
                    {systemOk === null ? null : systemOk ? (
                        <>
                            <span className="w-2.5 h-2.5 rounded-full bg-green-500 flex-shrink-0" />
                            <span className="text-sm text-muted-foreground">System operational</span>
                        </>
                    ) : (
                        <>
                            <span className="w-2.5 h-2.5 rounded-full bg-gray-400 flex-shrink-0" />
                            <span className="text-sm text-muted-foreground">Status unavailable</span>
                        </>
                    )}
                </div>
            </div>

            {/* KPI row */}
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <KpiCard
                    label="Active Agents"
                    value={summary?.active_agents ?? '—'}
                    icon={Users}
                    iconColor="text-primary"
                />
                <KpiCard
                    label="Active Calls"
                    value={summary?.active_calls ?? '—'}
                    icon={Phone}
                    iconColor="text-green-500"
                />
                <KpiCard
                    label="Total Routed"
                    value={summary?.total_routed ?? '—'}
                    icon={TrendingUp}
                    iconColor="text-blue-500"
                />
                <KpiCard
                    label="Transfers"
                    value={summary?.total_transfers ?? '—'}
                    icon={ArrowLeftRight}
                    iconColor="text-orange-500"
                />
            </div>

            {/* Manage Agents panel */}
            <ConfigSection
                title="Manage Agents"
                description="Configure AI voice agents. The default agent handles calls when no specific agent is targeted."
            >
                {/* New Agent button */}
                <div className="flex justify-end -mt-2 mb-2">
                    <button
                        onClick={() => setEditingAgent(null)}
                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                    >
                        <Plus className="w-4 h-4 mr-2" />
                        New Agent
                    </button>
                </div>

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                    {agents.length === 0 ? (
                        <div className="col-span-full p-8 border border-dashed rounded-lg text-center text-muted-foreground">
                            <Users className="w-10 h-10 mx-auto mb-3 opacity-30" />
                            <p>No agents configured. Click &ldquo;New Agent&rdquo; to create one.</p>
                        </div>
                    ) : (
                        agents.map((agent) => {
                            const stats = statsMap[agent.slug];
                            const isInactive = agent.is_active === 0;
                            return (
                                <ConfigCard
                                    key={agent.slug}
                                    className={`group relative hover:border-primary/50 transition-colors flex flex-col ${isInactive ? 'opacity-60' : ''}`}
                                >
                                    {/* Card header */}
                                    <div className="flex justify-between items-start mb-3">
                                        <div className="min-w-0">
                                            <div className="flex items-center gap-2 flex-wrap">
                                                <h4 className="font-semibold text-base leading-tight">
                                                    {agent.display_name}
                                                </h4>
                                                {agent.is_default === 1 && (
                                                    <Star className="w-4 h-4 text-yellow-500 fill-yellow-500 flex-shrink-0" title="Default agent" />
                                                )}
                                            </div>
                                            <div className="flex flex-wrap gap-1.5 mt-1.5">
                                                {agent.role_label && (
                                                    <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold text-muted-foreground bg-secondary/50">
                                                        {agent.role_label}
                                                    </span>
                                                )}
                                                {agent.extension && (
                                                    <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold text-muted-foreground bg-secondary/50">
                                                        Ext. {agent.extension}
                                                    </span>
                                                )}
                                                {agent.is_operator_managed === 0 && (
                                                    <span className="inline-flex items-center rounded-full border border-blue-500/30 px-2.5 py-0.5 text-xs font-semibold text-blue-600 dark:text-blue-400 bg-blue-500/10">
                                                        Imported from YAML
                                                    </span>
                                                )}
                                                {isInactive && (
                                                    <span className="inline-flex items-center rounded-full border border-muted px-2.5 py-0.5 text-xs font-semibold text-muted-foreground bg-muted/40">
                                                        Inactive
                                                    </span>
                                                )}
                                            </div>
                                        </div>

                                        {/* Card actions */}
                                        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity flex-shrink-0 ml-2">
                                            {agent.is_default !== 1 && agent.is_active === 1 && (
                                                <button
                                                    onClick={() => handleMakeDefault(agent.slug)}
                                                    className="p-2 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground"
                                                    title="Make default"
                                                    aria-label="Make default agent"
                                                >
                                                    <Star className="w-4 h-4" />
                                                </button>
                                            )}
                                            <button
                                                onClick={() => handleCopyDialplan(agent.slug)}
                                                className="p-2 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground"
                                                title="Copy dialplan snippet"
                                                aria-label="Copy dialplan"
                                            >
                                                <Copy className="w-4 h-4" />
                                            </button>
                                            <button
                                                onClick={() => setEditingAgent(agent)}
                                                className="p-2 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground"
                                                title="Edit agent"
                                                aria-label="Edit agent"
                                            >
                                                <Pencil className="w-4 h-4" />
                                            </button>
                                            <button
                                                onClick={() => handleDelete(agent)}
                                                className="p-2 hover:bg-destructive/10 rounded-md text-destructive"
                                                title="Delete agent"
                                                aria-label="Delete agent"
                                            >
                                                <Trash2 className="w-4 h-4" />
                                            </button>
                                        </div>
                                    </div>

                                    {/* Stats rows */}
                                    <div className="grid grid-cols-3 gap-2 text-sm border-t border-border pt-3 mt-auto">
                                        <div className="text-center">
                                            <p className="text-xs text-muted-foreground mb-0.5">Calls</p>
                                            <p className="font-semibold">{stats?.calls ?? 0}</p>
                                        </div>
                                        <div className="text-center border-x border-border">
                                            <p className="text-xs text-muted-foreground mb-0.5">Transfers</p>
                                            <p className="font-semibold">{stats?.transfers ?? 0}</p>
                                        </div>
                                        <div className="text-center">
                                            <p className="text-xs text-muted-foreground mb-0.5">Avg Duration</p>
                                            <p className="font-semibold">{formatDuration(stats?.avg_duration_seconds ?? 0)}</p>
                                        </div>
                                    </div>

                                    {/* Footer: Provider + Voice */}
                                    {(agent.provider || agent.voice) && (
                                        <div className="flex flex-wrap gap-1.5 mt-3 pt-3 border-t border-border">
                                            {agent.provider && (
                                                <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold text-muted-foreground bg-secondary/50">
                                                    {agent.provider}
                                                </span>
                                            )}
                                            {agent.voice && (
                                                <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold text-muted-foreground bg-secondary/50">
                                                    {agent.voice}
                                                </span>
                                            )}
                                        </div>
                                    )}
                                </ConfigCard>
                            );
                        })
                    )}
                </div>
            </ConfigSection>

            {/* Analytics panels */}
            <div className="flex flex-col gap-4 sm:flex-row">
                <BarPanel title="Routing Source" rows={routingRows} />
                <BarPanel title="Distribution by Agent" rows={distributionRows} />
            </div>

            <AgentForm
                isOpen={editingAgent !== undefined}
                onClose={() => setEditingAgent(undefined)}
                onSaved={loadAll}
                agent={editingAgent ?? null}
            />

            <Modal
                isOpen={manualDialplan !== null}
                onClose={() => setManualDialplan(null)}
                title="Copy Dialplan Manually"
                size="lg"
                footer={
                    <button
                        type="button"
                        onClick={() => setManualDialplan(null)}
                        className="px-4 py-2 rounded-md border border-border bg-card hover:bg-accent text-sm font-medium transition-colors"
                    >
                        Close
                    </button>
                }
            >
                <div className="space-y-3">
                    <p className="text-sm text-muted-foreground">
                        Clipboard access is unavailable in this browser context. Select the snippet below and copy it manually.
                    </p>
                    <label htmlFor="manual-dialplan-snippet" className="text-sm font-medium">
                        Dialplan for {manualDialplan?.slug}
                    </label>
                    <textarea
                        id="manual-dialplan-snippet"
                        readOnly
                        value={manualDialplan?.dialplan ?? ''}
                        onFocus={(event) => event.currentTarget.select()}
                        className="w-full min-h-64 rounded-md border border-border bg-background p-3 font-mono text-sm leading-6 text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                    />
                </div>
            </Modal>
        </div>
    );
};

export default AgentsPage;
