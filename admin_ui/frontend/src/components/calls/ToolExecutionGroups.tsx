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
    type?: 'tool_result';
    call_id?: string;
    tool_call_id?: string;
    name: string;
    action?: string;
    status?: 'success' | 'failure';
    target_id?: string | null;
    params: unknown;
    result: string;
    message?: string;
    redaction_mode?: 'strict' | 'show_routing' | 'off';
    redacted_fields?: string[];
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

export const InCallToolGroup = ({ entries }: { entries: InCallToolCall[] }) => {
    const recordedModes = Array.from(new Set(entries.map(entry => entry.redaction_mode ?? 'legacy')));
    const recordedMode = recordedModes.length === 1 ? recordedModes[0] : recordedModes.length > 1 ? 'mixed' : 'legacy';
    const modeLabels: Record<string, string> = {
        strict: 'Strict redaction',
        show_routing: 'Routing details visible',
        off: 'Redaction off',
        mixed: 'Mixed redaction policies',
        legacy: 'Legacy record — policy not recorded',
    };

    return (
    <div>
        <div className="text-sm font-medium text-muted-foreground mb-1">
            {PHASE_LABELS.in_call} ({entries.length})
        </div>
        <div className="mb-2 rounded border border-blue-500/20 bg-blue-500/5 px-3 py-2 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">{modeLabels[recordedMode]}</span>
            {' '}This reflects how the call was stored; changing the policy affects future tool executions only.
            {' '}<a className="text-blue-500 hover:underline" href="/env?section=call-history#system">Configure future calls</a>.
        </div>
        <div className="space-y-2">
            {entries.map((tool, i) => {
                const succeeded = tool.status ? tool.status === 'success' : tool.result === 'success';
                const status: ToolExecutionStatus = succeeded ? 'ok' : 'error';
                const hasParams = tool.params && typeof tool.params === 'object' && Object.keys(tool.params).length > 0;
                const invocationId = tool.tool_call_id || tool.call_id;
                return (
                    <div key={`in-${invocationId || tool.name}-${i}`} className="bg-muted/30 rounded-lg p-3 text-sm">
                        <div className="flex items-center justify-between gap-2 flex-wrap">
                            <div className="flex items-center gap-2 min-w-0">
                                <Wrench className="w-4 h-4 shrink-0" />
                                <span className="font-medium truncate">{tool.name}</span>
                                {tool.action && tool.action !== tool.name && (
                                    <span className="text-xs text-muted-foreground truncate">{tool.action}</span>
                                )}
                            </div>
                            <div className="flex items-center gap-2 text-muted-foreground text-xs">
                                <span>{Math.round(tool.duration_ms)}ms</span>
                                <StatusPill status={status} />
                            </div>
                        </div>
                        {tool.target_id && (
                            <div className="mt-2 text-xs text-muted-foreground break-all">
                                Target: <span className="font-mono">{tool.target_id}</span>
                            </div>
                        )}
                        {invocationId && (
                            <div className="mt-2 text-xs text-muted-foreground break-all">
                                Invocation: <span className="font-mono">{invocationId}</span>
                            </div>
                        )}
                        {tool.message && <div className="mt-2 text-xs text-muted-foreground break-words">{tool.message}</div>}
                        {hasParams && (
                            <pre className="mt-2 text-xs bg-background/50 rounded p-2 overflow-x-auto">
                                {JSON.stringify(tool.params, null, 2)}
                            </pre>
                        )}
                        {tool.redacted_fields && tool.redacted_fields.length > 0 && (
                            <div className="mt-2 text-xs text-muted-foreground break-words">
                                Sanitized fields: <span className="font-mono">{tool.redacted_fields.join(', ')}</span>. Original values cannot be recovered.
                            </div>
                        )}
                    </div>
                );
            })}
        </div>
    </div>
    );
};
