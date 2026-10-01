import { useId } from 'react';

interface Props {
    config: Record<string, unknown>;
    onChange: (config: Record<string, unknown>) => void;
}

/** Display defaults without writing them during mount or unrelated edits. */
export default function ConnectionRecoveryFields({ config, onChange }: Props) {
    const id = useId();
    const update = (key: string, raw: string) => {
        const next = { ...config };
        // ProvidersPage merges updates; an explicit tombstone removes saved values.
        if (raw === '') next[key] = undefined;
        else next[key] = Number(raw);
        onChange(next);
    };
    const fields = [
        { key: 'connect_timeout_sec', label: 'Connection timeout per attempt (sec)', fallback: 10, max: 60, min: 0.1, step: 0.1 },
        { key: 'connect_max_retries', label: 'Initial connection retries', fallback: 0, max: 3, min: 0, step: 1 },
        { key: 'connect_total_timeout_sec', label: 'Maximum connection wait (sec)', fallback: '', max: 180, min: 0.1, step: 0.1 },
    ];
    return (
        <details className="space-y-3 rounded-lg border p-3">
            <summary className="cursor-pointer font-semibold">Startup connection recovery (Expert)</summary>
            <p className="text-sm text-muted-foreground">
                Recovery is opt-in. Default: 10 seconds per connection attempt and no retries.
                One retry means two attempts. More attempts delay failure routing.
                These controls apply before session setup, independently of keepalive.
            </p>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {fields.map(field => (
                    <div className="space-y-2" key={field.key}>
                        <label className="text-sm font-medium" htmlFor={`${id}-${field.key}`}>{field.label}</label>
                        <input
                            id={`${id}-${field.key}`}
                            type="number"
                            min={field.min} max={field.max} step={field.step}
                            value={(config[field.key] ?? field.fallback) as number | string}
                            onChange={e => update(field.key, e.target.value)}
                            className="w-full rounded-md border bg-background px-3 py-2"
                            placeholder={field.key === 'connect_total_timeout_sec' ? 'Unset (existing deadlines)' : undefined}
                        />
                    </div>
                ))}
            </div>
            <p className="text-xs text-muted-foreground">
                Optional maximum wait covers connection attempts, backoff and pre-connect authentication
                (including ElevenLabs signed URLs). It excludes session setup. Leave blank to retain
                existing operation deadlines. Save provider settings, then restart AI Engine to apply.
            </p>
        </details>
    );
}
