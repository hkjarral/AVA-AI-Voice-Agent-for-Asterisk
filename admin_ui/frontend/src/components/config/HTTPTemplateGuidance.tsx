import { useEffect, useRef, useState, type TextareaHTMLAttributes } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { FormLabel } from '../ui/FormComponents';

type Phase = 'pre_call' | 'in_call' | 'post_call';
type HTTPConfig = Record<string, unknown>;
interface Reference { field: string; name: string; start: number; end: number; status: string; message: string }
interface Issue { field: string; variable?: string; severity: string; error_code: string; message: string }
export interface TemplateReview {
    valid: boolean;
    issues: Issue[];
    references: Reference[];
    variables: { name: string; type: string; source: string; description: string; example: unknown }[];
    example_body: string;
    example_title?: string;
    preview: Record<string, unknown> | null;
}

const COLORS: Record<string, string> = {
    available: 'text-emerald-700 dark:text-emerald-400',
    warning: 'text-amber-700 dark:text-amber-400',
    error: 'text-red-700 dark:text-red-400',
};

export function useHTTPTemplateReview(config: HTTPConfig | null, phase: Phase, token: string | null) {
    const [result, setResult] = useState<{key: string; phase: Phase; review: TemplateReview} | null>(null);
    const [error, setError] = useState('');
    const [revision, setRevision] = useState(0);
    const serialized = config ? JSON.stringify(config) : '';
    useEffect(() => {
        setResult(null);
        setError('');
        if (!serialized) return;
        const configEndpoint = JSON.parse(serialized).kind === 'pushover' ? '/api/pushover/templates/validate' : '/api/tools/http-templates/validate';
        const controller = new AbortController();
        const timer = setTimeout(() => {
            void (async () => {
                try {
                    const response = await axios.post(configEndpoint,
                        { phase, config: JSON.parse(serialized) },
                        { headers: { Authorization: `Bearer ${token}` }, signal: controller.signal });
                    if (!controller.signal.aborted && typeof response?.data?.valid === 'boolean') setResult({key: serialized, phase, review: response.data});
                } catch {
                    if (!controller.signal.aborted) setError('Template validation is unavailable. Retry before saving an enabled tool.');
                }
            })();
        }, 250);
        return () => { clearTimeout(timer); controller.abort(); };
    }, [serialized, phase, token, revision]);
    return { review: result?.key === serialized && result.phase === phase ? result.review : null, error, refresh: () => setRevision(value => value + 1) };
}

/** Same mirror/textarea pattern as PromptToolHighlight; offsets come from the runtime grammar. */
export function HTTPTemplateEditor({ references = [], ...props }: TextareaHTMLAttributes<HTMLTextAreaElement> & { references?: Reference[] }) {
    const mirror = useRef<HTMLDivElement>(null);
    const value = String(props.value || '');
    let cursor = 0;
    const segments = references.flatMap((reference, index) => {
        const before = value.slice(cursor, reference.start);
        cursor = reference.end;
        return [<span key={`before-${index}`}>{before}</span>,
            <span key={`ref-${index}`} className={`${COLORS[reference.status] || ''} font-medium`}>{value.slice(reference.start, reference.end)}</span>];
    });
    const box = 'w-full p-3 text-sm font-mono leading-6 whitespace-pre-wrap break-words';
    return <div className="relative rounded-md border border-input bg-background overflow-hidden">
        <div ref={mirror} aria-hidden="true" className={`${box} absolute inset-0 overflow-auto pointer-events-none text-foreground`}>
            {segments}{value.slice(cursor)}{value.endsWith('\n') ? '\u200b' : ''}
        </div>
        <textarea {...props} spellCheck={false}
            style={{ ...props.style, backgroundColor: 'transparent', color: 'transparent' }}
            className={`${props.className || ''} ${box} relative bg-transparent text-transparent caret-foreground resize-y`}
            onScroll={event => { if (mirror.current) mirror.current.scrollTop = event.currentTarget.scrollTop; props.onScroll?.(event); }} />
    </div>;
}

export function HTTPTemplateGuidance({ phase, review, error, refresh, onUseExample, kind = 'http' }: {
    phase: Phase; review: TemplateReview | null; error: string; refresh: () => void;
    kind?: 'http' | 'pushover';
    onUseExample: (body: string, title?: string) => void;
}) {
    const notification = kind === 'pushover';
    const [previewOpen, setPreviewOpen] = useState(false);
    return <section className="rounded-lg border border-border p-3 space-y-3" aria-label={notification ? 'Pushover template guidance' : 'HTTP template guidance'}>
        <FormLabel tooltip="Template review uses the runtime rendering contract. Conditional values depend on the phase, summary settings and trusted enrichment.">Template guidance · {phase.replace('_', '-')}</FormLabel>
        <p className="text-xs text-muted-foreground">
            Validation and preview use synthetic data. They do not send a request or generate an AI summary.
            {phase === 'in_call' && ' Summaries describe the AVA conversation recorded at invocation. Invoke before transfer for a pre-transfer briefing.'}
            {phase === 'post_call' && ' Summaries describe the final recorded AVA conversation, excluding later human conversation.'}
            {notification ? ' Pushover titles and messages are plain text; use {{ and }} for literal braces.' : ' Event names in examples are illustrative; use the contract expected by your receiver.'}
        </p>
        <div className="flex flex-wrap gap-2">
            <button type="button" className="rounded border px-3 py-1 text-sm hover:bg-accent" onClick={() => { refresh(); setPreviewOpen(true); }}>Validate &amp; Preview</button>
            <button type="button" disabled={!review} className="rounded border px-3 py-1 text-sm hover:bg-accent disabled:opacity-50" onClick={() => review && (notification ? onUseExample(review.example_body, review.example_title) : onUseExample(review.example_body))}>Use example</button>
            <button type="button" disabled={!review} className="rounded border px-3 py-1 text-sm hover:bg-accent disabled:opacity-50" onClick={() => {
                if (!review) return;
                if (!navigator.clipboard) { toast.error('Clipboard unavailable; open the example below and select its text to copy it.'); return; }
                void navigator.clipboard.writeText(notification ? `${review.example_title}\n\n${review.example_body}` : review.example_body).then(() => toast.success('Example copied')).catch(() => toast.error('Clipboard unavailable; select the example text to copy it.'));
            }}>Copy example</button>
        </div>
        {error && <p role="status" className="text-xs text-amber-700 dark:text-amber-400">{error}</p>}
        {review && <>
            <div role="status" className={`text-sm ${review.valid ? COLORS.available : COLORS.error}`}>
                {review.valid ? 'No definite template errors detected.' : notification ? 'Fix template errors before saving this notification.' : 'Fix template errors before enabling or applying this tool.'}
            </div>
            <ul className="space-y-1 text-xs">
                {review.issues.map((issue, index) => <li key={index} className={COLORS[issue.severity]}>
                    <strong>{issue.severity === 'error' ? 'Error' : 'Warning'} · {issue.field}{issue.variable ? ` · ${issue.variable}` : ''}:</strong> {issue.message}
                </li>)}
            </ul>
            {review.references.length > 0 && <div className="flex flex-wrap gap-2 text-xs" aria-label="Referenced variables">
                {review.references.map((reference, index) => <span key={index} title={`${reference.field}: ${reference.message}`} className={COLORS[reference.status]}>
                    {reference.status === 'available' ? '✓' : reference.status === 'error' ? '✗' : '⚠'} {reference.name}
                </span>)}
            </div>}
            <details>
                <summary className="cursor-pointer text-sm">Available variables and example</summary>
                <div className="overflow-x-auto mt-2"><table className="w-full text-xs text-left">
                    <thead><tr><th>Variable</th><th>Type / source</th><th>Meaning / example</th></tr></thead>
                    <tbody>{review.variables.map(variable => <tr key={variable.name} className="border-t border-border">
                        <td className="py-2 pr-2"><code>{`{${variable.name}}`}</code></td>
                        <td className="pr-2">{variable.type}<br />{variable.source}</td>
                        <td>{variable.description}<br /><code>{String(variable.example)}</code></td>
                    </tr>)}</tbody>
                </table></div>
                {!notification && <p className="my-2 text-xs text-muted-foreground">JSON-encoded placeholders such as {'{transcript_json}'} and {'{summary_json}'} go outside quotes. Custom enrichment depends on the Agent and completed lookups. Server credential values are never loaded for preview.</p>}
                {notification && <p className="my-2 text-xs text-muted-foreground">Pushover supports the catalog above, including {'{summary}'}, but not JSON transcript placeholders. Missing optional values render as unknown; a disabled or failed summary renders as unavailable. Titles are limited to 250 and messages to 1024 rendered characters; long values are truncated.</p>}
                <pre className="overflow-auto text-xs rounded bg-muted p-2">{notification && `${review.example_title}\n\n`}{review.example_body}</pre>
            </details>
            {previewOpen && <div aria-label={notification ? 'Synthetic notification preview' : 'Synthetic request preview'}>
                <div className="text-sm font-medium">Synthetic preview</div>
                <pre className="mt-2 overflow-auto rounded bg-muted p-2 text-xs">{review.preview ? JSON.stringify(review.preview, null, 2) : 'Preview unavailable until template errors are fixed.'}</pre>
                <p className="mt-1 text-xs text-muted-foreground">Warnings are not proof that a runtime value exists. This preview does not verify receiver acceptance or credentials.</p>
            </div>}
        </>}
    </section>;
}
