import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';
import { copyTextToClipboard } from '../utils/clipboard';

export interface SetupReadiness {
    stage: string;
    agent_slug: string | null;
    agent_provider?: string | null;
    agent_pipeline?: string | null;
    app_name: string;
    dialplan: string | null;
    destination: string;
    message?: string;
    audio_transport: string;
    ready_for_test: boolean;
    checks: Record<string, boolean>;
}

export default function SetupHandoff() {
    const [result, setResult] = useState<SetupReadiness | null>(null);
    const [error, setError] = useState('');
    const [copyMessage, setCopyMessage] = useState('');
    const [pbx, setPbx] = useState('freepbx');
    const [confirmed, setConfirmed] = useState<Record<string, boolean>>({});
    const [saving, setSaving] = useState(false);
    const [refresh, setRefresh] = useState(0);
    useEffect(() => {
        setConfirmed({});
    }, [result?.agent_slug, result?.app_name, result?.agent_provider, result?.agent_pipeline]);
    useEffect(() => {
        let cancelled = false;
        let timer: ReturnType<typeof setTimeout>;
        const poll = async () => {
            try {
                const response = await axios.get('/api/wizard/readiness', { timeout: 6000 });
                if (!cancelled) { setResult(response.data); setError(''); }
            } catch {
                if (!cancelled) {
                    setError('Readiness could not be checked. Check Admin UI/engine logs and retry.');
                    setResult(null); // Never retain stale green checks after a failed probe.
                }
            }
            if (!cancelled) timer = setTimeout(poll, 5000);
        };
        void poll();
        return () => { cancelled = true; clearTimeout(timer); };
    }, [refresh]);
    const checks = [
        ['reachable', 'Engine responding'], ['ari_connected', 'Asterisk ARI connected'],
        ['transport_ok', 'Engine media listener ready'], ['target_ready', 'Engine default provider/pipeline ready'],
        ['agent_resolved', 'Active default Agent found'],
    ];
    const confirmations = [
        ['greeting_heard', 'I heard the greeting'], ['two_way_audio', 'I spoke and heard a relevant reply'],
        ['clean_hangup', 'The call ended cleanly'], ['history_checked', 'I checked the call in Call History'],
    ];
    return <section className="space-y-5 text-left" aria-label="Connect Asterisk and verify your first call">
        <h2 className="text-xl font-semibold">Connect Asterisk and verify your first call</h2>
        <p className="text-sm text-muted-foreground">Settings are saved. Engine checks do not prove that audio reaches your phone; finish routing and make a test call.</p>
        {error && <p role="alert">{error}</p>}
        <div className="rounded-lg border p-4 space-y-2">
            <h3 className="font-semibold">1. Check readiness</h3>
            {checks.map(([key, label]) => {
                const passed = key === 'target_ready' ? result?.checks.provider_ok || result?.checks.pipeline_ok : result?.checks[key];
                return <p key={key} className="text-sm">{passed ? '✓' : '○'} {label}: {passed ? 'Passed' : 'Pending'}</p>;
            })}
            <p className="text-sm">If a check stays pending, start/apply the engine above, then check ARI credentials, media settings and provider configuration.</p>
            <button type="button" className="px-3 py-2 border rounded" onClick={() => setRefresh(v => v + 1)}>Recheck readiness</button>
        </div>
        <div className="rounded-lg border p-4 space-y-3">
            <h3 className="font-semibold">2. Route a test call to {result?.agent_slug || 'your Agent'}</h3>
            {result?.message && <p role="alert">{result.message} <Link to="/agents" className="underline">Open Agents</Link></p>}
            <label className="block">PBX type
                <select aria-label="PBX type" value={pbx} onChange={e => setPbx(e.target.value)} className="ml-2 border rounded p-2 bg-background">
                    <option value="freepbx">FreePBX</option><option value="asterisk">Plain Asterisk</option>
                </select>
            </label>
            {result?.dialplan && <>
                <p className="text-sm">{pbx === 'freepbx' ? 'Add this to /etc/asterisk/extensions_custom.conf.' : 'Add this to extensions.conf or a custom file included by it.'} Preserve existing contexts and choose an unused test route.</p>
                <textarea aria-label="Generated dialplan" readOnly value={result.dialplan} rows={8} className="w-full p-3 font-mono text-xs bg-muted rounded" />
                <button type="button" className="px-3 py-2 border rounded" onClick={async () => setCopyMessage(await copyTextToClipboard(result.dialplan!) ? 'Copied.' : 'Select the snippet above and copy it manually.')}>Copy dialplan</button>
                <p role="status" className="text-sm">{copyMessage}</p>
                {pbx === 'freepbx' ? <ol className="list-decimal pl-5 space-y-2 text-sm">
                    <li>Save the file, then Apply Config in FreePBX.</li>
                    <li>Under Admin → Custom Destinations, add target <code>{result.destination}</code> and name it AVA test.</li>
                    <li>Select that destination on a test inbound route, IVR option or Misc Application with an unused dialable feature code. Apply Config again.</li>
                    <li>Call the number, IVR option or feature code you configured. The context&apos;s <code>s</code> is not a phone number.</li>
                </ol> : <ol className="list-decimal pl-5 space-y-2 text-sm">
                    <li>In the context used by your test phone, choose an unused extension and route it with <code>Goto(from-ai-agent,s,1)</code>.</li>
                    <li>Run <code>asterisk -rx &quot;dialplan reload&quot;</code>, then <code>asterisk -rx &quot;dialplan show from-ai-agent&quot;</code>.</li>
                    <li>Dial the test extension from that phone.</li>
                </ol>}
                <p className="text-sm">Stasis application: <code>{result.app_name}</code>. The Agent owns its provider/pipeline; no per-call override is needed.</p>
            </>}
            <p className="text-sm">For a remote PBX, permit only the PBX to reach the selected AVA media port(s). AudioSocket uses TCP (default 8090); RTP uses the configured UDP range (default 18080–18099). File playback also needs a shared sounds directory on the PBX. WebSocket deployments require the separate authenticated transport setup.</p>
        </div>
        <div className="rounded-lg border p-4 space-y-3">
            <h3 className="font-semibold">3. Verify the conversation</h3>
            <p className="text-sm">Listen for the greeting, ask a simple question, then hang up. Open <Link className="underline" to="/history">Call History</Link> to inspect the result.</p>
            {confirmations.map(([key, label]) => <label key={key} className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={!!confirmed[key]} onChange={e => setConfirmed(v => ({ ...v, [key]: e.target.checked }))} />{label}
            </label>)}
            <button type="button" disabled={saving || !result?.ready_for_test || !confirmations.every(([key]) => confirmed[key])} className="px-4 py-2 rounded bg-primary text-primary-foreground disabled:opacity-50" onClick={async () => {
                setSaving(true);
                try {
                    await axios.post('/api/wizard/complete', confirmed);
                    setResult(prev => prev && { ...prev, stage: 'verified' });
                } catch (err) { setError(axios.isAxiosError<{ detail: string }>(err) ? err.response?.data?.detail || 'Could not record verification.' : 'Could not record verification. Recheck readiness and retry.'); }
                finally { setSaving(false); }
            }}>Confirm successful test call</button>
            {result?.stage === 'verified' && <p role="status">First call verified by you. Onboarding complete.</p>}
            <p className="text-sm">No greeting: check the route, Stasis name and active Agent. One-way audio: check media bind/advertised addresses and firewall rules. Failed provider: inspect the call error and validate the selected provider credentials.</p>
        </div>
        <p className="text-sm">After your first call, customize <Link to="/agents" className="underline">Agents</Link> and optionally configure <Link to="/tools" className="underline">Tools</Link> for transfers, calendars and notifications.</p>
        <Link to="/" className="inline-block px-4 py-2 border rounded">{result?.stage === 'verified' ? 'Go to Dashboard' : 'Go to Dashboard — resume setup later'}</Link>
    </section>;
}
