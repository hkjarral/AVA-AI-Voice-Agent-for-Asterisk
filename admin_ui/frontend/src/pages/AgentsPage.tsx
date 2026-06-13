import { useState, useEffect } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { Link } from 'react-router-dom';
import { useConfirmDialog } from '../hooks/useConfirmDialog';
import { Plus, Pencil, Trash2, Copy, Star, Users, AlertCircle, Phone } from 'lucide-react';
import { ConfigSection } from '../components/ui/ConfigSection';
import { ConfigCard } from '../components/ui/ConfigCard';
import AgentForm from '../components/agents/AgentForm';
import type { Agent } from '../components/agents/AgentForm';

interface Stats {
    calls_30d: number;
    last_call: string | null;
}

interface MigrationStatus {
    drift: boolean;
    last_default_promotion?: string | null;
}

const AgentsPage = () => {
    const { confirm } = useConfirmDialog();
    const [agents, setAgents] = useState<Agent[]>([]);
    const [statsMap, setStatsMap] = useState<Record<string, Stats>>({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [driftBanner, setDriftBanner] = useState(false);
    const [editingAgent, setEditingAgent] = useState<Agent | null | undefined>(undefined);
    // undefined = form closed, null = new agent, Agent = edit

    useEffect(() => {
        loadAll();
    }, []);

    const loadAll = async () => {
        setLoading(true);
        setError(null);
        try {
            const [agentsRes, migRes] = await Promise.all([
                axios.get<Agent[]>('/api/agents'),
                axios.get<MigrationStatus>('/api/agents-migration/status').catch(() => null),
            ]);

            const agentList = Array.isArray(agentsRes.data) ? agentsRes.data : [];
            setAgents(agentList);

            if (migRes) {
                if (migRes.data.drift) setDriftBanner(true);
                if (migRes.data.last_default_promotion) {
                    toast.info(`Default agent was auto-promoted: ${migRes.data.last_default_promotion}`);
                }
            }

            // Load stats in parallel, best-effort
            const statsEntries = await Promise.all(
                agentList.map(async (a) => {
                    try {
                        const res = await axios.get<Stats>(`/api/agents/${a.slug}/stats`);
                        return [a.slug, res.data] as [string, Stats];
                    } catch {
                        return [a.slug, { calls_30d: 0, last_call: null }] as [string, Stats];
                    }
                })
            );
            setStatsMap(Object.fromEntries(statsEntries));
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
            await navigator.clipboard.writeText(res.data.dialplan);
            toast.success('Dialplan snippet copied to clipboard');
        } catch {
            toast.error('Failed to copy dialplan');
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

    const activeAgents = agents.filter((a) => a.is_active === 1);
    const totalCalls30d = Object.values(statsMap).reduce((sum, s) => sum + (s.calls_30d ?? 0), 0);

    const formatLastCall = (ts: string | null): string => {
        if (!ts) return 'No calls yet';
        try {
            const d = new Date(ts);
            return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
        } catch {
            return ts;
        }
    };

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
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Agents</h1>
                    <p className="text-muted-foreground mt-1">
                        {activeAgents.length === 0
                            ? 'No active agents — create one to start routing calls.'
                            : `${activeAgents.length} active agent${activeAgents.length !== 1 ? 's' : ''} · ${totalCalls30d} call${totalCalls30d !== 1 ? 's' : ''} in the last 30 days`}
                    </p>
                </div>
                <button
                    onClick={() => setEditingAgent(null)}
                    className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                >
                    <Plus className="w-4 h-4 mr-2" />
                    New Agent
                </button>
            </div>

            <ConfigSection title="Agents" description="Configure AI voice agents. The default agent handles calls when no specific agent is targeted.">
                <div className="grid grid-cols-1 gap-4">
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
                                    className={`group relative hover:border-primary/50 transition-colors ${isInactive ? 'opacity-60' : ''}`}
                                >
                                    <div className="flex justify-between items-start">
                                        <div className="flex items-center gap-3 mb-3">
                                            <div className="p-2 bg-secondary rounded-md flex-shrink-0">
                                                <Users className="w-5 h-5 text-primary" />
                                            </div>
                                            <div>
                                                <div className="flex items-center gap-2">
                                                    <h4 className="font-semibold text-lg leading-tight">
                                                        {agent.display_name}
                                                    </h4>
                                                    {agent.is_default === 1 && (
                                                        <Star className="w-4 h-4 text-yellow-500 fill-yellow-500 flex-shrink-0" title="Default agent" />
                                                    )}
                                                </div>
                                                <div className="flex flex-wrap gap-1.5 mt-1">
                                                    {agent.extension && (
                                                        <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold text-muted-foreground bg-secondary/50">
                                                            Ext {agent.extension}
                                                        </span>
                                                    )}
                                                    {agent.role_label && (
                                                        <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold text-muted-foreground bg-secondary/50">
                                                            {agent.role_label}
                                                        </span>
                                                    )}
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
                                        </div>

                                        {/* Card actions */}
                                        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">
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

                                    {/* Stats row */}
                                    {stats && (
                                        <div className="flex items-center gap-4 text-sm text-muted-foreground mt-1">
                                            <span className="flex items-center gap-1">
                                                <Phone className="w-3.5 h-3.5" />
                                                {stats.calls_30d} call{stats.calls_30d !== 1 ? 's' : ''} (30d)
                                            </span>
                                            <span className="text-xs">
                                                Last call: {formatLastCall(stats.last_call)}
                                            </span>
                                        </div>
                                    )}

                                    {/* Greeting preview */}
                                    {agent.greeting && (
                                        <div className="mt-3 bg-secondary/30 p-3 rounded-md">
                                            <span className="font-medium text-xs uppercase tracking-wider text-muted-foreground block mb-1">Greeting</span>
                                            <p className="text-foreground/90 italic text-sm">&ldquo;{agent.greeting}&rdquo;</p>
                                        </div>
                                    )}
                                </ConfigCard>
                            );
                        })
                    )}
                </div>
            </ConfigSection>

            <AgentForm
                isOpen={editingAgent !== undefined}
                onClose={() => setEditingAgent(undefined)}
                onSaved={loadAll}
                agent={editingAgent ?? null}
            />
        </div>
    );
};

export default AgentsPage;
