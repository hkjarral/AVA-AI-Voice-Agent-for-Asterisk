import React, { useMemo, useRef } from 'react';
import { CheckCircle2, Globe, AlertTriangle } from 'lucide-react';
import { parseToolReferences, type ToolStatus } from '../../utils/promptTools';

interface Props {
    value: string;
    onChange: (value: string) => void;
    /** Every configured tool name (all phases) — the detection set. */
    knownNames: string[];
    /** name -> in-call status for the current agent/context. */
    statusMap: Record<string, ToolStatus>;
    id?: string;
    rows?: number;
    placeholder?: string;
}

const STATUS_META: Record<ToolStatus, { text: string; Icon: typeof CheckCircle2; label: string }> = {
    context: { text: 'text-emerald-700 dark:text-emerald-400', Icon: CheckCircle2, label: 'Enabled here (in-call)' },
    global: { text: 'text-amber-700 dark:text-amber-400', Icon: Globe, label: 'Global tool — runs for all contexts' },
    unavailable: { text: 'text-red-700 dark:text-red-400', Icon: AlertTriangle, label: "Not enabled for in-call here" },
};

// Shared box-model so the highlight mirror and the textarea align character-for-character.
const BOX = 'w-full p-3 text-sm leading-relaxed whitespace-pre-wrap break-words';

interface Segment { text: string; status: ToolStatus | null; }

export const PromptToolHighlight: React.FC<Props> = ({ value, onChange, knownNames, statusMap, id, rows = 8, placeholder }) => {
    const taRef = useRef<HTMLTextAreaElement>(null);
    const mirrorRef = useRef<HTMLDivElement>(null);

    const refs = useMemo(() => parseToolReferences(value, knownNames), [value, knownNames]);

    const segments = useMemo<Segment[]>(() => {
        const segs: Segment[] = [];
        let i = 0;
        for (const r of refs) {
            if (r.start > i) segs.push({ text: value.slice(i, r.start), status: null });
            segs.push({ text: value.slice(r.start, r.end), status: statusMap[r.name] ?? 'unavailable' });
            i = r.end;
        }
        if (i < value.length) segs.push({ text: value.slice(i), status: null });
        return segs;
    }, [value, refs, statusMap]);

    // Unique referenced tools for the validation list, in first-seen order.
    const referenced = useMemo<Array<{ name: string; status: ToolStatus }>>(() => {
        const seen = new Set<string>();
        const out: Array<{ name: string; status: ToolStatus }> = [];
        for (const r of refs) {
            if (seen.has(r.name)) continue;
            seen.add(r.name);
            out.push({ name: r.name, status: statusMap[r.name] ?? 'unavailable' });
        }
        return out;
    }, [refs, statusMap]);

    const hasUnavailable = referenced.some((r) => r.status === 'unavailable');

    const syncScroll = () => {
        if (taRef.current && mirrorRef.current) {
            mirrorRef.current.scrollTop = taRef.current.scrollTop;
            mirrorRef.current.scrollLeft = taRef.current.scrollLeft;
        }
    };

    return (
        <div>
            <div className="relative rounded-md border border-border bg-background overflow-hidden">
                <div
                    ref={mirrorRef}
                    aria-hidden="true"
                    className={`${BOX} absolute inset-0 overflow-auto pointer-events-none text-foreground`}
                >
                    {segments.map((s, idx) =>
                        s.status ? (
                            <span key={idx} className={`${STATUS_META[s.status].text} font-medium`}>{s.text}</span>
                        ) : (
                            <span key={idx}>{s.text}</span>
                        )
                    )}
                    {/* trailing newline keeps the mirror height in step with the textarea */}
                    {value.endsWith('\n') ? '​' : ''}
                </div>
                <textarea
                    ref={taRef}
                    id={id}
                    rows={rows}
                    placeholder={placeholder}
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                    onScroll={syncScroll}
                    spellCheck={false}
                    className={`${BOX} relative bg-transparent text-transparent caret-foreground resize-y focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset block`}
                />
            </div>

            {referenced.length > 0 && (
                <div className="mt-2 space-y-1 text-xs">
                    <div className="text-muted-foreground">Tools referenced in this prompt:</div>
                    {referenced.map(({ name, status }) => {
                        const meta = STATUS_META[status];
                        return (
                            <div key={name} className="flex items-center gap-1.5">
                                <meta.Icon className={`w-3.5 h-3.5 flex-shrink-0 ${meta.text}`} aria-hidden="true" />
                                <span className={`font-medium ${meta.text}`}>{name}</span>
                                <span className="text-muted-foreground">— {meta.label}</span>
                            </div>
                        );
                    })}
                    {hasUnavailable && (
                        <div className="text-red-700 dark:text-red-400">
                            ⚠ Some referenced tools aren&apos;t enabled for in-call here — add them in the in-call Tools section, or they won&apos;t be available to the agent.
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};
