import { Wrench } from 'lucide-react';

export type ToolPhase = 'pre_call' | 'in_call' | 'post_call';

export type ToolExecutionStatus = 'pending' | 'ok' | 'error' | 'timeout' | 'skipped';

export interface PhaseToolCall {
    name: string;
    kind?: string | null;
    phase: ToolPhase;
    status: ToolExecutionStatus;
    started_at?: string | null;
    finished_at?: string | null;
    duration_ms?: number | null;
    http_status?: number | null;
    response_summary?: string | null;
    output_variables?: Record<string, string> | null;
    error_message?: string | null;
    attempt?: number | null;
}

export interface InCallToolCall {
    name: string;
    params: unknown;
    result: string;
    message?: string;
    timestamp: string;
    duration_ms: number;
}

const PHASE_LABELS: Record<ToolPhase, string> = {
    pre_call: 'Pre-call',
    in_call: 'In-call',
    post_call: 'Post-call',
};

const StatusPill = ({ status }: { status: ToolExecutionStatus }) => {
    const styles: Record<ToolExecutionStatus, string> = {
        ok: 'bg-green-500/15 text-green-500',
        error: 'bg-red-500/15 text-red-500',
        timeout: 'bg-orange-500/15 text-orange-500',
        pending: 'bg-yellow-500/15 text-yellow-500',
        skipped: 'bg-muted text-muted-foreground',
    };

    return (
        <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium ${styles[status] || styles.skipped}`}>
            {status === 'pending' && <span className="w-1.5 h-1.5 rounded-full bg-current mr-1 animate-pulse" />}
            {status}
        </span>
    );
};

export const PhaseToolGroup = ({ phase, entries }: { phase: Exclude<ToolPhase, 'in_call'>; entries: PhaseToolCall[] }) => (
    <div>
        <div className="text-sm font-medium text-muted-foreground mb-1">
            {PHASE_LABELS[phase]} ({entries.length})
        </div>
        <div className="space-y-2">
            {entries.map((entry, i) => {
                const ms = typeof entry.duration_ms === 'number' ? `${Math.round(entry.duration_ms)}ms` : null;
                return (
                    <div key={`${phase}-${entry.name}-${entry.started_at ?? i}`} className="bg-muted/30 rounded-lg p-3 text-sm">
                        <div className="flex items-center justify-between gap-2 flex-wrap">
                            <div className="flex items-center gap-2 min-w-0">
                                <Wrench className="w-4 h-4 shrink-0" />
                                <span className="font-medium truncate">{entry.name}</span>
                                {entry.kind && <span className="text-xs text-muted-foreground truncate">{entry.kind}</span>}
                            </div>
                            <div className="flex items-center gap-2 text-muted-foreground text-xs">
                                {entry.http_status != null && <span>HTTP {entry.http_status}</span>}
                                {ms && <span>{ms}</span>}
                                <StatusPill status={entry.status} />
                            </div>
                        </div>
                        {entry.error_message && (
                            <div className="mt-2 text-xs text-red-500/90 break-words">{entry.error_message}</div>
                        )}
                        {entry.response_summary && (
                            <pre className="mt-2 text-xs bg-background/50 rounded p-2 overflow-x-auto whitespace-pre-wrap break-words">
                                {entry.response_summary}
                            </pre>
                        )}
                        {entry.output_variables && Object.keys(entry.output_variables).length > 0 && (
                            <div className="mt-2 text-xs">
                                <div className="text-muted-foreground mb-1">Output variables</div>
                                <div className="bg-background/50 rounded p-2 space-y-1">
                                    {Object.entries(entry.output_variables).map(([key, value]) => (
                                        <div key={key} className="grid grid-cols-[minmax(0,10rem)_1fr] gap-2">
                                            <span className="font-mono text-blue-400 break-all">{key}</span>
                                            <span className="break-words">{value || <em className="text-muted-foreground">empty</em>}</span>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>
                );
            })}
        </div>
    </div>
);

export const InCallToolGroup = ({ entries }: { entries: InCallToolCall[] }) => (
    <div>
        <div className="text-sm font-medium text-muted-foreground mb-1">
            {PHASE_LABELS.in_call} ({entries.length})
        </div>
        <div className="space-y-2">
            {entries.map((tool, i) => {
                const status: ToolExecutionStatus = tool.result === 'success' ? 'ok' : 'error';
                const hasParams = tool.params && typeof tool.params === 'object' && Object.keys(tool.params).length > 0;
                return (
                    <div key={`in-${tool.name}-${i}`} className="bg-muted/30 rounded-lg p-3 text-sm">
                        <div className="flex items-center justify-between gap-2 flex-wrap">
                            <div className="flex items-center gap-2 min-w-0">
                                <Wrench className="w-4 h-4 shrink-0" />
                                <span className="font-medium truncate">{tool.name}</span>
                            </div>
                            <div className="flex items-center gap-2 text-muted-foreground text-xs">
                                <span>{Math.round(tool.duration_ms)}ms</span>
                                <StatusPill status={status} />
                            </div>
                        </div>
                        {tool.message && <div className="mt-2 text-xs text-muted-foreground break-words">{tool.message}</div>}
                        {hasParams && (
                            <pre className="mt-2 text-xs bg-background/50 rounded p-2 overflow-x-auto">
                                {JSON.stringify(tool.params, null, 2)}
                            </pre>
                        )}
                    </div>
                );
            })}
        </div>
    </div>
);
