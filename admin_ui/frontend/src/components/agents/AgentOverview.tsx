import { Link } from 'react-router-dom';
import { Copy, Star, Trash2, ArrowUpRight } from 'lucide-react';
import type { Agent } from './AgentForm';
import { AgentAvatar } from './AgentAvatar';
import { agentEngine, toolLabel } from './agentSummary';

export interface AgentStat {
    slug: string; calls: number; transfers: number; avg_duration_seconds: number; last_call: string | null;
}
const duration = (seconds: number) => {
    const rounded = Math.round(seconds);
    return rounded < 60 ? `${rounded}s` : `${Math.floor(rounded / 60)}m ${rounded % 60}s`;
};

export default function AgentOverview({ agent, stats, tools, catalogAvailable, layout, onDefault, onCopy, onDelete }: {
    agent: Agent; stats?: AgentStat; tools: string[]; catalogAvailable: boolean; layout: 'cards' | 'list';
    onDefault: () => void; onCopy: () => void; onDelete: () => void;
}) {
    const list = layout === 'list';
    const href = `/agents/${encodeURIComponent(agent.slug)}/edit`;
    const iconButton = 'inline-flex h-11 w-11 items-center justify-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';
    return <article aria-label={agent.display_name} className="min-w-0 rounded-xl border border-border bg-card p-5 shadow-sm transition-colors hover:border-primary/40">
        <div className={list ? 'flex flex-wrap items-center gap-4' : 'flex h-full flex-col gap-4'}>
            <div className={`flex min-w-0 gap-4 ${list ? 'flex-1 items-center' : 'items-start justify-between'}`}>
                {list && <AgentAvatar appearance={agent} size="small" />}
                <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                        <Link to={href} className="break-words text-base font-semibold tracking-tight hover:underline focus-visible:ring-2 focus-visible:ring-ring">{agent.display_name}</Link>
                        {agent.is_default === 1 && <span className="rounded-md bg-primary/10 px-2 py-1 text-xs font-medium text-primary">Default</span>}
                    </div>
                    <p className="mt-2 flex items-center gap-2 text-xs text-muted-foreground"><span className={`h-2 w-2 rounded-full ${agent.is_active ? 'bg-emerald-500' : 'bg-muted-foreground'}`} />{agent.is_active ? 'Enabled' : 'Disabled'}</p>
                    <p className="mt-3 break-words text-sm text-muted-foreground">{agent.role_label || agentEngine(agent)}</p>
                    {agent.role_label && <p className="mt-1 break-words text-xs text-muted-foreground">{agentEngine(agent)}</p>}
                    {agent.extension && <p className="mt-1 text-xs text-muted-foreground">Extension {agent.extension}</p>}
                    {agent.voice && <p className="mt-1 break-words text-xs text-muted-foreground">Voice: {agent.voice}</p>}
                </div>
                {!list && <AgentAvatar appearance={agent} />}
            </div>
            <div className={list ? 'min-w-0 sm:w-44' : 'space-y-2'}>
                <p className="mb-2 text-xs font-medium text-muted-foreground">Configured tools</p>
                <Link to={`${href}?section=tools`} title={tools.map(toolLabel).join(', ') || 'Review tool configuration'}
                    className="inline-flex min-h-11 max-w-full flex-wrap items-center gap-1.5 rounded-lg text-xs focus-visible:ring-2 focus-visible:ring-ring">
                    {list ? <span className="rounded-md border border-border px-2 py-1">{tools.length} tools <ArrowUpRight className="ml-1 inline h-3 w-3" /></span> : <>
                        {tools.slice(0, 2).map(tool => <span key={tool} className="max-w-full break-words rounded-md border border-border bg-secondary/40 px-2 py-1">{toolLabel(tool)}</span>)}
                        {tools.length > 2 && <span className="rounded-md border border-border px-2 py-1">+{tools.length - 2}</span>}
                        {!tools.length && <span className="text-muted-foreground">{catalogAvailable ? 'No tools configured' : 'Review tools'}</span>}
                    </>}
                </Link>
                {!catalogAvailable && <p className="text-xs text-muted-foreground">Global tool summary unavailable</p>}
            </div>
            <div className={`${list ? 'sm:w-48' : 'mt-auto border-t border-border pt-4'} text-sm text-muted-foreground`}>
                {stats ? <><span className="font-medium text-foreground">{stats.calls}</span> calls · {stats.transfers} transfers<br /><span className="text-xs">{duration(stats.avg_duration_seconds)} average duration</span></> : <span>Statistics unavailable</span>}
            </div>
            <div className={`flex gap-2 ${list ? 'flex-wrap items-center' : 'flex-col border-t border-border pt-3'}`}>
                {!list && <span className="text-xs text-muted-foreground">{agent.is_operator_managed ? 'Managed in Admin UI' : 'Imported from YAML'}</span>}
                <div className="flex flex-wrap items-center justify-end gap-1">
                    {agent.is_active === 1 && agent.is_default !== 1 && <button onClick={onDefault} className={iconButton} aria-label={`Make ${agent.display_name} default`} title="Make default"><Star className="h-4 w-4" /></button>}
                    <button onClick={onCopy} className={iconButton} aria-label={`Copy dialplan for ${agent.display_name}`} title="Copy dialplan"><Copy className="h-4 w-4" /></button>
                    <button onClick={onDelete} className={`${iconButton} hover:text-destructive`} aria-label={`Delete ${agent.display_name}`} title="Delete agent"><Trash2 className="h-4 w-4" /></button>
                    <Link to={href} aria-label={`Edit ${agent.display_name}`} className="inline-flex min-h-11 items-center rounded-lg border border-border px-4 text-sm font-medium hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring">Edit</Link>
                </div>
            </div>
        </div>
    </article>;
}
