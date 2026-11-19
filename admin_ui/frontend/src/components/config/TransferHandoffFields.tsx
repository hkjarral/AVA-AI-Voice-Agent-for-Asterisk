import { useEffect, useId, useState } from 'react';

type Handoff = { enabled?: boolean; mode?: string; message?: string; start_timeout_sec?: number; finish_timeout_sec?: number };
type Props = { value?: Handoff; global?: Handoff; override?: boolean; disabled?: boolean; onChange: (value: Handoff) => void };

function TimingInput({ label, value, fallback, max, onChange }: { label: string; value?: number; fallback: number; max: number; onChange: (value: number) => void }) {
    const [draft, setDraft] = useState(String(value ?? fallback));
    useEffect(() => setDraft(String(value ?? fallback)), [value, fallback]);
    return <input aria-label={label} type="number" min={0.1} max={max} step={0.1} value={draft}
        onChange={e => setDraft(e.target.value)} onBlur={() => {
            const parsed = draft.trim() ? Number(draft) : fallback;
            const next = Number.isFinite(parsed) ? Math.max(0.1, Math.min(max, parsed)) : fallback;
            setDraft(String(next));
            onChange(next);
        }} className="ml-2 w-20 rounded border bg-background p-1" />;
}

export default function TransferHandoffFields({ value = {}, global, override = false, disabled = false, onChange }: Props) {
    const id = useId();
    const mode = value.mode || 'inherit';
    const custom = override ? mode === 'custom' : Boolean(value.enabled);
    const effective = override && mode === 'inherit' ? (global?.enabled ? global.message : '') : custom ? value.message : '';
    return <fieldset disabled={disabled} className="space-y-3 border border-border rounded-lg p-3">
        <legend className="px-1 text-sm font-medium">Transfer announcement</legend>
        {override ? <label className="block text-sm">Announcement setting
            <select aria-label="Announcement setting" value={mode} onChange={e => onChange({ ...value, mode: e.target.value })} className="ml-2 rounded border bg-background p-2">
                <option value="inherit">Use global</option><option value="disabled">Disabled</option><option value="custom">Custom message</option>
            </select>
        </label> : <label className="flex gap-2 text-sm">
            <input type="checkbox" checked={Boolean(value.enabled)} onChange={e => onChange({ ...value, enabled: e.target.checked })} />
            Wait for transfer announcement
        </label>}
        {custom && <div>
            <label htmlFor={`${id}-message`} className="block text-sm">{override ? 'Custom announcement' : 'Global announcement'}</label>
            <textarea id={`${id}-message`} value={value.message || ''} maxLength={1000} required onChange={e => onChange({ ...value, message: e.target.value })} className="w-full rounded border bg-background p-2" rows={3} />
            {!value.message?.trim() && <p role="alert" className="text-xs text-destructive">Enter an announcement before saving.</p>}
        </div>}
        {override && <p className="text-xs text-muted-foreground">Effective announcement: {effective || 'Disabled'}</p>}
        {!override && custom && <details><summary className="text-sm cursor-pointer">Announcement timing</summary>
            <div className="mt-2 flex flex-wrap gap-4">
                {([['start_timeout_sec', 'Time to start speaking (seconds)', 4, 10], ['finish_timeout_sec', 'Time to finish speaking (seconds)', 20, 60]] as const).map(([key, label, fallback, max]) => <label key={key} className="text-sm">{label}
                    <TimingInput label={label} value={value[key]} fallback={fallback} max={max} onChange={next => onChange({ ...value, [key]: next })} />
                </label>)}
            </div>
        </details>}
        <p className="text-xs text-muted-foreground">The agent is asked to say this before connecting. If the wait expires, the transfer continues after queued audio drains. Exact wording is best effort.</p>
        {disabled && <p className="text-xs text-muted-foreground">Enable deferred transfer to use announcement waiting.</p>}
    </fieldset>;
}
