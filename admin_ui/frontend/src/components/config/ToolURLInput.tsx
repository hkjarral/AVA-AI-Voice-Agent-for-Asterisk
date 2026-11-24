import { FormInput, FormLabel } from '../ui/FormComponents';

export type ToolURL = string | { origin: string; set: boolean };

function storedURL(value?: ToolURL) {
    if (typeof value === 'object' && value !== null) return value;
    if (typeof value === 'string' && value.includes('«secret:')) {
        return { origin: value.split('«secret:', 1)[0].replace(/\/$/, ''), set: true };
    }
    return undefined;
}

export function displayToolURL(value?: ToolURL): string {
    const stored = storedURL(value);
    return stored ? stored.origin || (stored.set ? 'Stored URL' : 'No URL') : String(value || 'No URL');
}

export function ToolURLInput({ value, onChange, label, tooltip, placeholder }: {
    value?: ToolURL; onChange: (url: string) => void; label: string; tooltip: string; placeholder: string;
}) {
    const stored = storedURL(value);
    if (stored) return <div className="space-y-1">
        <FormLabel tooltip={tooltip}>{label}</FormLabel>
        <div className="flex items-center gap-3"><span>{displayToolURL(value)}</span>
            <button type="button" className="text-primary hover:underline" onClick={() => onChange('')}>Replace URL</button>
        </div>
        <p className="text-xs text-muted-foreground">Enter the complete URL to change this destination.</p>
    </div>;
    return <FormInput label={label} tooltip={tooltip} placeholder={placeholder}
        value={typeof value === 'string' ? value : ''} onChange={e => onChange(e.target.value)} />;
}
