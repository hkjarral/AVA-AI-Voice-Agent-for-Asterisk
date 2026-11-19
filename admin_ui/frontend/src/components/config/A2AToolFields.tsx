import { FormInput, FormLabel, FormSelect } from '../ui/FormComponents';

interface A2AToolValue {
    [key: string]: unknown;
    agent_url?: string;
    description?: string;
    bearer_token_file?: string;
    bearer_token_env?: string;
    a2a_auth?: string;
    timeout_ms?: number;
    poll_interval_ms?: number;
    max_input_chars?: number;
    max_response_chars?: number;
    error_message?: string;
}

export function A2AToolFields({ value, onChange, token, onTokenChange }: {
    value: A2AToolValue;
    onChange: (value: A2AToolValue) => void;
    token: string;
    onTokenChange: (token: string) => void;
}) {
    const authentication = value.bearer_token_file ? 'token' : value.bearer_token_env ? 'environment' : value.a2a_auth || 'none';
    return <div className="space-y-4">
        <p className="text-sm text-muted-foreground">Configure this remote agent here, then grant access under Agents → Tools → In-call. AVA discovers its Agent Card when a call invokes this tool.</p>
        <FormInput label="Agent endpoint" tooltip="Base URL of an A2A 1.0 service with a public Agent Card and a JSON-RPC endpoint on the same origin." value={value.agent_url || ''} placeholder="https://agents.example.com" onChange={e => onChange({ ...value, agent_url: e.target.value })} />
        <div className="space-y-2">
            <FormLabel htmlFor="a2a-description" tooltip="Tell the voice agent when to use this tool and what request to send.">Description</FormLabel>
            <textarea id="a2a-description" className="w-full rounded-md border border-input bg-background p-3 text-sm" rows={3} value={value.description || ''} onChange={e => onChange({ ...value, description: e.target.value })} />
        </div>
        <FormSelect label="Authentication" value={authentication} options={[
            { value: 'none', label: 'None' }, { value: 'token', label: 'Bearer token' },
            ...(value.bearer_token_env ? [{ value: 'environment', label: 'Existing Engine environment reference' }] : []),
        ]} onChange={e => {
            const next: A2AToolValue = { ...value, a2a_auth: e.target.value };
            if (e.target.value !== 'environment') delete next.bearer_token_env;
            if (e.target.value !== 'token') delete next.bearer_token_file;
            onTokenChange('');
            onChange(next);
        }} />
        {authentication === 'token' && <>
            <FormInput label="Bearer token" type="password" autoComplete="new-password" value={token} placeholder={value.bearer_token_file ? 'Saved token — leave blank to retain' : 'Enter a Bearer token'} tooltip="Stored as a managed credential. Use HTTPS, or a loopback endpoint for local development." onChange={e => onTokenChange(e.target.value)} />
            {value.bearer_token_file && <p className="text-xs text-muted-foreground">A token is saved. Enter a new value to replace it.</p>}
        </>}
        {authentication === 'environment' && <p className="text-sm text-muted-foreground">Engine environment reference · {value.bearer_token_env}</p>}
        <FormInput label="Timeout (ms)" type="number" min={1000} max={120000} value={value.timeout_ms ?? 15000} tooltip="Total time allowed for discovery and the remote task." onChange={e => onChange({ ...value, timeout_ms: Number(e.target.value) })} />
        <details className="rounded border border-border p-3 space-y-3">
            <summary className="cursor-pointer text-sm font-medium">Advanced</summary>
            <FormInput label="Poll interval (ms)" type="number" min={50} max={5000} value={value.poll_interval_ms ?? 250} onChange={e => onChange({ ...value, poll_interval_ms: Number(e.target.value) })} />
            <FormInput label="Maximum request characters" type="number" min={1} max={16000} value={value.max_input_chars ?? 4000} onChange={e => onChange({ ...value, max_input_chars: Number(e.target.value) })} />
            <FormInput label="Maximum response characters" type="number" min={1} max={8000} value={value.max_response_chars ?? 1000} onChange={e => onChange({ ...value, max_response_chars: Number(e.target.value) })} />
            <FormInput label="Failure message" value={value.error_message ?? 'The remote agent could not complete that request.'} onChange={e => onChange({ ...value, error_message: e.target.value })} />
        </details>
    </div>;
}
