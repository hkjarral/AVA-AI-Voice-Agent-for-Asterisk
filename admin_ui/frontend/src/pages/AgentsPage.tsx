import { useState, useEffect } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { Link } from 'react-router-dom';
import { useConfirmDialog } from '../hooks/useConfirmDialog';
import {
    Plus, Users, AlertCircle, Phone, Search, LayoutGrid, List, RefreshCw,
    TrendingUp, ArrowLeftRight,
} from 'lucide-react';
import { Modal } from '../components/ui/Modal';
import AgentOverview from '../components/agents/AgentOverview';
import { agentEngine, configuredTools } from '../components/agents/agentSummary';
import type { ToolDef } from '../components/agents/agentToolConfig';
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
    <div className="bg-card border border-border rounded-lg shadow-sm p-4 sm:p-5 flex flex-wrap items-center gap-3">
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
    const [creatingStarters, setCreatingStarters] = useState(false);
    const [catalog, setCatalog] = useState<ToolDef[]>([]);
    const [catalogAvailable, setCatalogAvailable] = useState(false);
    const [query, setQuery] = useState('');
    const [status, setStatus] = useState('all');
    const [engine, setEngine] = useState('all');
    const [layout, setLayout] = useState<'cards' | 'list'>(() => {
        try { return localStorage.getItem('agents-layout') === 'list' ? 'list' : 'cards'; } catch { return 'cards'; }
    });
    const changeLayout = (value: 'cards' | 'list') => {
        setLayout(value); try { localStorage.setItem('agents-layout', value); } catch { /* Optional preference. */ }
    };
    const [manualDialplan, setManualDialplan] = useState<{ slug: string; dialplan: string } | null>(null);

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
                catalogRes,
            ] = await Promise.all([
                axios.get<Agent[]>('/api/agents'),
                axios.get<MigrationStatus>('/api/agents-migration/status').catch(() => null),
                axios.get<AgentStat[]>('/api/agents/stats-batch').catch(() => null),
                axios.get<Summary>('/api/agents/summary').catch(() => null),
                axios.get<DistributionEntry[]>('/api/agents/distribution').catch(() => null),
                axios.get<RoutingMethods>('/api/agents/routing-methods').catch(() => null),
                axios.get<{ tools: ToolDef[] }>('/api/tools/catalog').catch(() => null),
            ]);

            const agentList = Array.isArray(agentsRes.data) ? agentsRes.data : [];
            setAgents(agentList);
            setCatalog(Array.isArray(catalogRes?.data?.tools) ? catalogRes.data.tools : []);
            setCatalogAvailable(!!catalogRes);

            if (migRes) {
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

    const handleCreateStarters = async () => {
        setCreatingStarters(true);
        try {
            const response = await axios.post('/api/agents/starter-set', {});
            const created = response.data?.created || [];
            if (created.length) toast.success('Receptionist, Sales, and Support created');
            else toast.info('Agents are already configured');
            await loadAll();
        } catch (e: unknown) {
            const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
            toast.error(detail ?? 'Failed to create starter agents');
        } finally {
            setCreatingStarters(false);
        }
    };

    const engines = [...new Set(agents.map(agentEngine))].sort();
    const visibleAgents = agents.filter(agent => {
        const text = [agent.display_name, agent.slug, agent.role_label, agent.extension, agent.voice, agentEngine(agent), ...configuredTools(agent, catalog)].join(' ').toLowerCase();
        return text.includes(query.trim().toLowerCase())
            && (status === 'all' || (status === 'enabled' ? agent.is_active === 1 : agent.is_active === 0))
            && (engine === 'all' || agentEngine(agent) === engine);
    });

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

    if (loading) return <div role="status" className="p-8 text-center text-muted-foreground">Loading agents…</div>;

    return (
        <div className="mx-auto max-w-screen-2xl space-y-6 pb-6">
            {/* Error banner */}
            {error && (
                <div className="bg-red-500/15 border border-red-500/30 text-red-700 dark:text-red-400 p-4 rounded-md flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <AlertCircle className="w-5 h-5" />
                        {error}
                    </div>
                    <button
                        onClick={loadAll}
                        className="flex items-center text-xs px-3 py-1.5 rounded transition-colors bg-red-500 text-white hover:bg-red-600 font-medium"
                    >
                        Reload
                    </button>
                </div>
            )}

            <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                    <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Voice workspace</p>
                    <h1 className="mt-2 text-3xl font-semibold tracking-tight">Agents</h1>
                    <p className="mt-2 text-sm text-muted-foreground">Manage the voices, instructions and tools behind your calls.</p>
                </div>
                <div className="flex gap-2">
                    <button type="button" onClick={loadAll} aria-label="Refresh agents" className="inline-flex h-11 w-11 items-center justify-center rounded-lg border border-border hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"><RefreshCw className="h-4 w-4" /></button>
                    <Link to="/agents/new" className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:ring-2 focus-visible:ring-ring"><Plus className="h-4 w-4" />New agent</Link>
                </div>
            </div>
            {systemOk === false && <p role="status" className="text-sm text-muted-foreground">Call statistics are currently unavailable.</p>}

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

            <section aria-label="Manage agents" className="space-y-4">
                <div className="flex flex-wrap items-center gap-3">
                    <div className="relative min-w-0 flex-1 basis-64">
                        <Search className="pointer-events-none absolute left-3 top-3.5 h-4 w-4 text-muted-foreground" />
                        <input aria-label="Search agents" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search agents, voices or tools…"
                            className="h-11 w-full rounded-lg border border-input bg-card pl-10 pr-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
                    </div>
                    <select aria-label="Filter by status" value={status} onChange={event => setStatus(event.target.value)} className="h-11 rounded-lg border border-input bg-card px-3 text-sm focus-visible:ring-2 focus-visible:ring-ring">
                        <option value="all">All statuses</option><option value="enabled">Enabled</option><option value="disabled">Disabled</option>
                    </select>
                    <select aria-label="Filter by engine" value={engine} onChange={event => setEngine(event.target.value)} className="h-11 max-w-full rounded-lg border border-input bg-card px-3 text-sm focus-visible:ring-2 focus-visible:ring-ring">
                        <option value="all">All engines</option>{engines.map(value => <option key={value}>{value}</option>)}
                    </select>
                    <div className="flex rounded-lg border border-border bg-card p-1" role="group" aria-label="Agent view">
                        <button aria-label="Cards view" aria-pressed={layout === 'cards'} onClick={() => changeLayout('cards')} className={`inline-flex h-11 w-11 items-center justify-center rounded-md focus-visible:ring-2 focus-visible:ring-ring ${layout === 'cards' ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:bg-accent'}`}><LayoutGrid className="h-4 w-4" /></button>
                        <button aria-label="List view" aria-pressed={layout === 'list'} onClick={() => changeLayout('list')} className={`inline-flex h-11 w-11 items-center justify-center rounded-md focus-visible:ring-2 focus-visible:ring-ring ${layout === 'list' ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:bg-accent'}`}><List className="h-4 w-4" /></button>
                    </div>
                </div>
                <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
                    <p aria-live="polite">{visibleAgents.length} of {agents.length} agents</p>
                    <p>The default agent handles calls without a specific target.</p>
                </div>
                <div className={layout === 'cards' ? 'grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3' : 'space-y-3'}>
                    {visibleAgents.map(agent => <AgentOverview key={agent.slug} agent={agent} stats={statsMap[agent.slug]} tools={configuredTools(agent, catalog)}
                        catalogAvailable={catalogAvailable} layout={layout} onDefault={() => handleMakeDefault(agent.slug)} onCopy={() => handleCopyDialplan(agent.slug)} onDelete={() => handleDelete(agent)} />)}
                </div>
                {!error && agents.length === 0 && <div className="rounded-xl border border-dashed border-border p-8 text-center">
                    <Users className="mx-auto mb-3 h-10 w-10 text-muted-foreground" /><h2 className="font-semibold">Create your first agent</h2>
                    <p className="mt-2 text-sm text-muted-foreground">Start with Receptionist, Sales and Support, or create an agent of your own.</p>
                    <div className="mt-4 flex flex-wrap justify-center gap-2">
                        <button onClick={handleCreateStarters} disabled={creatingStarters} className="min-h-11 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50">{creatingStarters ? 'Creating…' : 'Create starter agents'}</button>
                        <Link to="/agents/new" className="inline-flex min-h-11 items-center rounded-lg border border-border px-4 text-sm font-medium hover:bg-accent">Create one agent</Link>
                    </div>
                </div>}
                {agents.length > 0 && !visibleAgents.length && <div className="rounded-xl border border-dashed border-border p-8 text-center">
                    <h2 className="font-semibold">No matching agents</h2><p className="mt-2 text-sm text-muted-foreground">Try another name, voice, tool or filter.</p>
                    <button onClick={() => { setQuery(''); setStatus('all'); setEngine('all'); }} className="mt-3 min-h-11 rounded-lg border border-border px-4 text-sm hover:bg-accent">Clear filters</button>
                </div>}
            </section>

            <div className="rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted-foreground">
                <span className="font-medium text-foreground">Advanced:</span>{' '}
                upgrading from a Context-based configuration? Review and import legacy YAML from the{' '}
                <Link to="/agents/migration" className="font-medium text-primary hover:underline">
                    legacy Context import report
                </Link>.
            </div>

            <details className="rounded-xl border border-border bg-card p-4">
                <summary className="cursor-pointer py-2 text-sm font-medium focus-visible:ring-2 focus-visible:ring-ring">Routing and distribution</summary>
                <div className="mt-4 flex flex-col gap-4 sm:flex-row">
                    <BarPanel title="Routing source" rows={routingRows} />
                    <BarPanel title="Distribution by agent" rows={distributionRows} />
                </div>
            </details>

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
