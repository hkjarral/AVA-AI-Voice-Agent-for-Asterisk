import { useEffect, useRef, useState } from 'react';
import { Upload } from 'lucide-react';

export interface AgentAppearance {
    avatar_preset?: string | null;
    avatar_image?: string | null;
}

export function AgentAvatar({ appearance, size = 'large' }: {
    appearance: AgentAppearance; size?: 'large' | 'small';
}) {
    const fallback = `/ava-${appearance.avatar_preset === 'female' ? 'female' : 'male'}-icon.png`;
    return <img
        src={appearance.avatar_image || fallback}
        onError={event => { if (event.currentTarget.getAttribute('src') !== fallback) event.currentTarget.src = fallback; }}
        alt={`${appearance.avatar_preset === 'female' ? 'Female' : 'Male'} agent avatar${appearance.avatar_image ? ' (custom image)' : ''}`}
        width={size === 'large' ? 96 : 44} height={size === 'large' ? 96 : 44}
        className={`${size === 'large' ? 'h-24 w-24' : 'h-11 w-11'} shrink-0 rounded-full object-contain bg-secondary`}
    />;
}

export function AgentAvatarPicker({ value, onChange, onBusyChange }: {
    value: AgentAppearance; onChange: (value: AgentAppearance) => void;
    onBusyChange: (busy: boolean) => void;
}) {
    const input = useRef<HTMLInputElement>(null);
    const reader = useRef<FileReader | null>(null);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    useEffect(() => () => { reader.current?.abort(); }, []);
    const upload = (file?: File) => {
        if (!file) return;
        setError('');
        if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 2 * 1024 * 1024) {
            setError('Choose a PNG, JPEG, or WebP image up to 2 MB.'); return;
        }
        reader.current?.abort();
        const next = new FileReader(); reader.current = next;
        setBusy(true); onBusyChange(true);
        next.onload = () => onChange({ ...value, avatar_image: String(next.result) });
        next.onerror = () => setError('Could not read this image. Please try again.');
        next.onloadend = () => { setBusy(false); onBusyChange(false); };
        next.readAsDataURL(file);
    };
    return <div className="rounded-xl border border-border bg-secondary/20 p-5">
        <div className="flex flex-col items-start gap-5 sm:flex-row sm:items-center">
            <button type="button" onClick={() => input.current?.click()} disabled={busy}
                className="rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label="Upload agent image">
                <AgentAvatar appearance={value} />
            </button>
            <div className="w-full space-y-3 flex-1 min-w-0">
                <div><h3 className="font-semibold">Agent appearance</h3>
                    <p className="mt-1 text-sm text-muted-foreground">Choose a visual voice indicator. This does not change the actual voice.</p></div>
                <div className="flex flex-wrap gap-2" role="group" aria-label="Default avatar">
                    {(['male', 'female'] as const).map(preset => <button key={preset} type="button" disabled={busy}
                        aria-pressed={(value.avatar_preset || 'male') === preset}
                        onClick={() => onChange({ ...value, avatar_preset: preset })}
                        className={`min-h-11 rounded-lg border px-4 text-sm font-medium focus-visible:ring-2 focus-visible:ring-ring ${(value.avatar_preset || 'male') === preset ? 'border-primary bg-primary/10 text-foreground' : 'border-border hover:bg-accent'}`}>
                        {preset === 'male' ? 'Male' : 'Female'}
                    </button>)}
                    <button type="button" disabled={busy} onClick={() => input.current?.click()}
                        className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-border px-4 text-sm hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring">
                        <Upload className="h-4 w-4" />{busy ? 'Reading…' : 'Upload image'}
                    </button>
                    {value.avatar_image && <button type="button" disabled={busy} onClick={() => onChange({ ...value, avatar_image: null })}
                        className="min-h-11 px-2 text-sm underline underline-offset-4">Use default image</button>}
                </div>
                <p className="text-xs text-muted-foreground">PNG, JPEG or WebP · up to 2 MB. Changes apply when you save.</p>
                {value.avatar_image && <p className="text-xs text-muted-foreground">Your custom image is shown. The selected default is used when you remove it.</p>}
                {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
            </div>
        </div>
        <input ref={input} type="file" className="hidden" aria-label="Agent image file" accept="image/png,image/jpeg,image/webp"
            onChange={event => { upload(event.target.files?.[0]); event.target.value = ''; }} />
    </div>;
}
