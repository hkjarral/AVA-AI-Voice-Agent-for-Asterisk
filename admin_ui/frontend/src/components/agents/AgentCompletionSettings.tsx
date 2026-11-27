import { FormSelect } from '../ui/FormComponents';
import type { AgentToolState } from './agentToolConfig';
import type { TransferDestination } from './agentToolInventory';
interface Props { state: AgentToolState; destinations: TransferDestination[]; globalDefault: string; onChange: (state: AgentToolState) => void; }
export default function AgentCompletionSettings({state, destinations, globalDefault, onChange}: Props) {
    const allowed = destinations.filter(item => item.completionAllowed && ["extension", "queue", "ringgroup", "dialplan"].includes(item.type) && (state.transferDestinationPolicy === 'inherit' || (state.transferDestinationPolicy === 'selected' && state.transferDestinationKeys.includes(item.key))));
    const selected = state.completionDestinationKeys || [];
    const effective = allowed.filter(item => !state.completionSelected || selected.includes(item.key));
    const defaults = effective.filter(item => !item.requiresOptIn);
    const saved = state.completionDefault || '';
    return <section className="space-y-3 border border-border rounded-lg p-4" aria-label="Agent conversation completion">
        <h3 className="text-sm font-medium">After conversation</h3>
        <p className="text-xs text-muted-foreground">The farewell finishes before the selected handoff or hangup. Global tool disables and destination access always win. Changes apply to new conversations.</p>
        <FormSelect label="Agent completion destinations" value={state.completionSelected ? 'selected' : 'inherit'}
            options={[{value:'inherit',label:'Inherit allowed global destinations'},{value:'selected',label:'Select destinations (empty disables handoff)'}]}
            onChange={event => onChange({...state,completionSelected:event.target.value === 'selected'})} />
        {state.completionSelected && allowed.map(item => <label key={item.key} className="flex gap-2 text-sm"><input type="checkbox" checked={selected.includes(item.key)} onChange={event => onChange({...state,completionDestinationKeys:event.target.checked ? [...selected,item.key] : selected.filter(key => key !== item.key)})} />{item.key}{item.requiresOptIn ? ' · caller opt-in required' : ''}</label>)}
        {selected.filter(key => !allowed.some(item => item.key === key)).map(key => <p key={key} className="text-xs text-amber-700">Unavailable or disallowed saved destination: {key}. It cannot receive calls.</p>)}
        <FormSelect label="Agent default after conversation" value={saved}
            options={[{value:'',label:`Inherit global default (${globalDefault})`},{value:'hangup',label:'End call'},...defaults.map(item => ({value:item.key,label:`Hand off to ${item.key}`})),
                ...(saved && saved !== 'hangup' && !defaults.some(item => item.key === saved) ? [{value:saved,label:`Unavailable: ${saved} (falls back to hangup)`}] : [])]}
            onChange={event => onChange({...state,completionDefault:event.target.value})}
            tooltip="Only used when hangup_call omits next_step. Opt-in destinations require explicit caller acceptance and tool selection." />
        <p className="text-xs text-muted-foreground">Survey prompt example: Offer the survey after resolving the request. If accepted, call hangup_call with farewell_message and next_step set to the approved survey key. If declined, use next_step=hangup. For a return to the IVR during conversation, call blind_transfer with the IVR destination key.</p>
    </section>;
}
