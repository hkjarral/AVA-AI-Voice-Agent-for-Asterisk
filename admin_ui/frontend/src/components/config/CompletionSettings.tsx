import { FormSelect } from '../ui/FormComponents';

export interface CompletionPolicy { default_next_step?: string; destination_keys?: string[]; }
interface Route { type?: string; target?: string; description?: string; requires_opt_in?: boolean; }
interface Props { value?: CompletionPolicy; destinations: Record<string, Route>; disabled?: boolean; onChange: (value: CompletionPolicy) => void; }
export default function CompletionSettings({ value = {}, destinations, disabled, onChange }: Props) {
    const keys = value.destination_keys || [];
    const available = Object.entries(destinations).filter(([key, route]) => key !== 'hangup' && route && ['extension', 'queue', 'ringgroup', 'dialplan'].includes(route.type || ''));
    const defaults = available.filter(([key, route]) => keys.includes(key) && !route.requires_opt_in);
    return <section className="space-y-3 rounded-lg border border-border p-3" aria-label="Conversation completion">
        <h4 className="font-medium">Conversation completion</h4>
        <p className="text-xs text-muted-foreground">After the farewell finishes, end the call or continue to an approved destination. These settings apply to new inbound conversations after Save &amp; Apply. Outbound and externally owned calls keep their existing ending behavior.</p>
        {disabled && <p className="text-sm text-amber-700">Transfer routing is disabled globally. Completion destinations are unavailable.</p>}
        <fieldset disabled={disabled} className="space-y-2">
            <legend className="text-sm">Allowed next destinations</legend>
            {available.map(([key, route]) => <label key={key} className="flex gap-2 text-sm">
                <input type="checkbox" checked={keys.includes(key)} onChange={event => {
                    const nextKeys = event.target.checked ? [...keys, key] : keys.filter(item => item !== key);
                    onChange({ ...value, destination_keys: nextKeys, default_next_step: nextKeys.includes(value.default_next_step || '') ? value.default_next_step : 'hangup' });
                }} />
                <span>{key} · {route.description || route.type}{route.requires_opt_in ? ' · caller opt-in required' : ''}</span>
            </label>)}
            {!available.length && <p className="text-xs text-muted-foreground">Add a Transfer Destination first. IVRs, surveys and announcements use the Dialplan type.</p>}
        </fieldset>
        {keys.filter(key => !available.some(([known]) => known === key)).map(key => <p key={key} className="text-sm text-amber-700">Unavailable saved destination: {key}. Calls fall back to hangup. <button type="button" onClick={() => onChange({ ...value, destination_keys: keys.filter(item => item !== key), default_next_step: value.default_next_step === key ? 'hangup' : value.default_next_step })}>Remove selection</button></p>)}
        <FormSelect label="Default after conversation" value={value.default_next_step || 'hangup'} disabled={disabled}
            options={[{ value: 'hangup', label: 'End call (default)' }, ...defaults.map(([key]) => ({ value: key, label: `Hand off to ${key}` })),
                ...(value.default_next_step && value.default_next_step !== 'hangup' && !defaults.some(([key]) => key === value.default_next_step) ? [{value: value.default_next_step, label: `Unavailable: ${value.default_next_step}`}]: [])]}
            onChange={event => onChange({ ...value, default_next_step: event.target.value })}
            tooltip="Used when hangup_call omits next_step. Explicit next_step=hangup always disconnects. Opt-in surveys cannot be automatic defaults." />
        <p className="text-xs text-muted-foreground">For a survey, instruct the agent to offer it first, then call hangup_call with farewell_message and next_step set to the survey key after acceptance, or hangup after a decline. For a mid-call return to an IVR, use blind_transfer with its destination key.</p>
    </section>;
}
