import { useEffect, useId, useRef, useState } from 'react';
import { FormLabel } from './FormComponents';
import { envString, isSecretMarker, type EnvValue } from '../../utils/envValue';

type Props = {
    name: string;
    value: EnvValue;
    onChange: (replacement: string) => void;
    label?: string;
    placeholder?: string;
    tooltip?: string;
    hideLabel?: boolean;
};

export function SecretInput({ name, value, onChange, label = name, placeholder, tooltip, hideLabel }: Props) {
    const id = useId();
    const [editing, setEditing] = useState(false);
    const input = useRef<HTMLInputElement>(null);
    const marked = isSecretMarker(value);
    useEffect(() => {
        if (marked) setEditing(false);
    }, [marked, name]);
    useEffect(() => {
        if (editing) input.current?.focus();
    }, [editing]);
    return (
        <div className="min-w-0 flex-1">
            {!hideLabel && <FormLabel htmlFor={id} tooltip={tooltip}>{label}</FormLabel>}
            {marked && !editing ? (
                <div className="flex h-9 items-center gap-3">
                    <span className="text-sm text-muted-foreground">{value.set ? 'Set' : 'Not set'}</span>
                    <button type="button" id={id} aria-label={`Replace ${label}`}
                        className="rounded border border-input px-3 py-1 text-sm" onClick={() => setEditing(true)}>Replace</button>
                </div>
            ) : (
                <input id={id} ref={input} type="password" aria-label={label} name={name} autoComplete="new-password"
                    className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                    placeholder={placeholder} value={envString(value)} onChange={event => onChange(event.target.value)} />
            )}
        </div>
    );
}
