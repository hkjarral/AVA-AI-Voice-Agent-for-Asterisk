import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { useAuth } from '../../auth/AuthContext';

type Binding = { uri: string; connection_id: string; one_to_one_confirmed: boolean; name: string; duration: number; qualified_locations?: string[] };
type Config = { enabled?: boolean; appointments?: Record<string, Binding> };
type Entry = { uri: string; name: string; host: string; duration: number; supported: boolean; reason: string; location_summary: string };
type Conn = { revision: number; id?: string; name?: string; timezone?: string; enabled: boolean; qualified?: boolean };
type Question = { position: number; question: string; type: string; choices: string[] };
type Proposal = { operation: string; slots: { slot_reference: string; start_datetime: string }[]; questions: Question[]; appointment_name: string; duration_minutes: number; timezone: string; location_summary: string };
type Result = { status: string; message: string; reservation_status?: string; attempt_reference?: string; start_datetime?: string; cancel_url?: string; reschedule_url?: string };
type Attempt = { id: string; state: string; appointment_name?: string; updated: number };

const input = 'w-full rounded-md border border-input bg-background p-2 text-sm';
const button = 'rounded-md border border-input px-3 py-2 text-sm hover:bg-accent disabled:opacity-50';

export default function CalendlyToolForm({ config, onChange, onSave }: { config: Config; onChange: (v: Config) => void; onSave: (v: Config) => Promise<void> }) {
    const { token } = useAuth();
    const [connection, setConnection] = useState<Conn>({ revision: 0, enabled: false });
    const [candidate, setCandidate] = useState('');
    const [verified, setVerified] = useState('');
    const [entries, setEntries] = useState<Entry[]>([]);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [notice, setNotice] = useState('');
    const [oneToOne, setOneToOne] = useState(false);
    const [testKey, setTestKey] = useState('');
    const [tz, setTz] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone);
    const [proposal, setProposal] = useState<Proposal | null>(null);
    const [slot, setSlot] = useState('');
    const [name, setName] = useState('');
    const [recipient, setRecipient] = useState('');
    const [answers, setAnswers] = useState<Record<number, string>>({});
    const [approved, setApproved] = useState(false);
    const [result, setResult] = useState<Result | null>(null);
    const [setupAttempt, setSetupAttempt] = useState('');
    const [attempts, setAttempts] = useState<Attempt[]>([]);
    const [resolutionNote, setResolutionNote] = useState('');
    const [invitationChecked, setInvitationChecked] = useState(false);
    const [calendarChecked, setCalendarChecked] = useState(false);
    const [cleanupChecked, setCleanupChecked] = useState(false);
    const revision = useRef(0);
    const headers = { Authorization: `Bearer ${token}` };
    const api = async (method: string, path: string, data?: unknown) => (await axios.request({ method, url: `/api/calendly${path}`, headers, data, timeout: 25000 })).data;
    const refresh = async () => {
        setConnection(await api('GET', '/connection'));
        const rows = await api('GET', '/attempts');
        if (!Array.isArray(rows)) throw new Error('invalid_attempts');
        setAttempts(rows);
    };
    useEffect(() => {
        let alive = true;
        axios.get('/api/calendly/connection', { headers: { Authorization: `Bearer ${token}` } })
            .then(r => { if (alive) setConnection(r.data); }).catch(() => { if (alive) setError('Unable to load Calendly connection.'); });
        axios.request({ method: 'GET', url: '/api/calendly/attempts', headers: { Authorization: `Bearer ${token}` }, timeout: 25000 })
            .then(r => {
                if (!Array.isArray(r.data)) throw new Error('invalid_attempts');
                if (alive) setAttempts(r.data);
            }).catch(() => { if (alive) setError('Unable to load pending Calendly requests. Refresh pending requests before checking an existing booking.'); });
        return () => { alive = false; };
    }, [token]);
    useEffect(() => { setApproved(false); }, [slot, name, recipient, answers, tz, testKey]);
    useEffect(() => { setInvitationChecked(false); setCalendarChecked(false); setCleanupChecked(false); }, [result?.attempt_reference]);
    const run = async (work: () => Promise<void>, fallback = 'This action could not be confirmed. Refresh status before repeating a booking.') => {
        if (busy) return;
        setBusy(true); setError(''); setNotice('');
        try { await work(); } catch (e: unknown) {
            const detail = axios.isAxiosError(e) ? e.response?.data?.detail : undefined;
            setError(typeof detail === 'string' ? detail : detail?.message || fallback);
        } finally { setBusy(false); }
    };
    const selected = config.appointments || {};
    const changeCandidate = (value: string) => { revision.current++; setCandidate(value); setVerified(''); };
    const verify = async () => {
        const current = revision.current;
        const response = await api('POST', '/connection/verify', { token: candidate || undefined });
        if (current === revision.current) setVerified(`Connection verified for ${response.name}. Booking and delivery are not yet verified.`);
    };
    const editSelection = (entry: Entry, checked: boolean) => {
        const next = { ...selected };
        const key = entry.uri.split('/').pop()!;
        if (checked && connection.id) next[key] = { uri: entry.uri, connection_id: connection.id, one_to_one_confirmed: true, name: entry.name, duration: entry.duration };
        else delete next[key];
        onChange({ ...config, appointments: next });
        setProposal(null); setApproved(false);
    };
    return <div className="max-w-3xl space-y-7">
        <div className="space-y-2 text-sm text-muted-foreground">
            <p>This is an optional adapter for operators who already use Calendly. Direct API booking requires Calendly Standard or higher; Calendly is not required for appointment booking.</p>
            <p>For a self-hosted calendar, expose its availability and event tools through Ava&apos;s Streamable HTTP MCP support instead. Provider-specific booking and Pushover operator notifications remain independently configurable.</p>
        </div>
        {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
        {notice && <p role="status" className="text-sm">{notice}</p>}
        {verified && <p role="status" className="rounded-md border border-green-500/30 bg-green-500/10 p-3 text-sm text-green-700 dark:text-green-400">{verified}</p>}
        <fieldset disabled={busy} className="space-y-4 min-w-0">
            <legend className="text-lg font-medium">1. Connect Calendly</legend>
            <p>{connection.id ? `${connection.name} · ${connection.timezone} · ${connection.enabled ? 'Scheduling enabled' : 'Scheduling paused'}` : 'Not connected'}</p>
            <a href="https://calendly.com/integrations/api_webhooks" target="_blank" rel="noopener noreferrer" className="text-primary underline">Open Calendly to create a personal access token</a>
            <p className="text-xs text-muted-foreground">Scopes: users:read, event_types:read, availability:read, scheduled_events:write. Saved tokens are never displayed.</p>
            <label className="block text-sm">{connection.id ? 'Replace saved token' : 'Personal access token'}<input className={input} type="password" autoComplete="off" value={candidate} onChange={e => changeCandidate(e.target.value)} /></label>
            {!connection.id && <p className="text-xs text-muted-foreground">Enter a token to verify your connection. Booking stays paused when you save it.</p>}
            <div className="flex flex-wrap gap-2">
                <button type="button" className={button} disabled={!candidate.trim() && !connection.id} onClick={() => run(verify, 'Connection verification did not finish. No booking was submitted. Try Verify connection again.')}>Verify connection</button>
                <button type="button" className={button} disabled={!candidate || !verified} onClick={() => run(async () => {
                    const saved = await api('PUT', '/connection', { token: candidate, revision: connection.revision });
                    setConnection(saved); changeCandidate(''); setEntries([]); setProposal(null); setResult(null);
                    if (saved.id !== connection.id) onChange({ enabled: false, appointments: {} });
                    setNotice('Connection saved. Choose appointments, save configuration, and explicitly enable booking below.');
                })}>Save connection</button>
                {connection.id && <>
                    <button type="button" className={button} onClick={() => run(async () => { await api('POST', `/connection/${connection.enabled ? 'disable' : 'enable'}`, { revision: connection.revision }); await refresh(); })}>{connection.enabled ? 'Disable booking now' : 'Enable booking'}</button>
                    <button type="button" className={button} onClick={() => run(async () => { await api('DELETE', '/connection', { revision: connection.revision }); await refresh(); setProposal(null); setNotice('Disconnected locally. Existing appointments remain in Calendly; admitted requests may still settle.'); })}>Disconnect</button>
                </>}
            </div>
        </fieldset>
        {connection.id && <fieldset disabled={busy} className="space-y-4 min-w-0">
            <legend className="text-lg font-medium">2. Choose appointments</legend>
            <div className="flex flex-wrap gap-3"><button type="button" className={button} onClick={() => run(async () => { setEntries(await api('GET', '/appointment-types')); })}>Refresh from Calendly</button><a href="https://calendly.com/app/availability" target="_blank" rel="noopener noreferrer" className="text-primary underline">Open Calendly settings</a></div>
            <label className="flex gap-2 text-sm"><input type="checkbox" checked={oneToOne} onChange={e => setOneToOne(e.target.checked)} />I checked that selected appointments are one host and one invitee in Calendly.</label>
            {entries.map(entry => { const key = entry.uri.split('/').pop()!; return <div key={entry.uri} className="border-b border-border py-3"><label className="flex gap-3"><input type="checkbox" checked={!!selected[key]} disabled={!entry.supported || !oneToOne} onChange={e => editSelection(entry, e.target.checked)} /><span>{entry.name} · {entry.host} · {entry.duration} minutes<span className="block text-xs text-muted-foreground">{entry.location_summary}{!entry.supported && ` · ${entry.reason}`}</span></span></label></div>; })}
            {!!Object.keys(selected).length && <p className="text-sm">Selected: {Object.values(selected).map(v => v.name).join(', ')}</p>}
            <label className="flex gap-2 text-sm"><input type="checkbox" checked={config.enabled || false} onChange={e => onChange({ ...config, enabled: e.target.checked })} />Enable Calendly tool for assigned agents</label>
            <button type="button" className={button} onClick={() => run(async () => { await onSave(config); setNotice('Selection saved. Use Save & Apply on Tools to publish it, then assign permitted appointments in Agents.'); })}>Save appointment selection</button>
            <p className="text-xs text-muted-foreground">Ordinary settings and agent changes affect new calls after apply. Disable booking now also blocks new submissions from active calls. A previously admitted request can still finish.</p>
        </fieldset>}
        {connection.id && <fieldset disabled={busy} className="space-y-4 min-w-0">
            <legend className="text-lg font-medium">3. Test setup</legend>
            <p className="text-sm">This creates a real appointment and sends Calendly’s configured invitation. Use your own disposable test recipient. Save and apply selection first; verify conflict calendars and destination calendar in Calendly.</p>
            <label className="block text-sm">Appointment<select className={input} value={testKey} onChange={e => { setTestKey(e.target.value); setProposal(null); setResult(null); setSetupAttempt(''); }}><option value="">Choose an appointment</option>{Object.entries(selected).map(([key, v]) => <option key={key} value={key}>{v.name}</option>)}</select></label>
            <label className="block text-sm">Test recipient timezone<input className={input} value={tz} onChange={e => { setTz(e.target.value); setProposal(null); }} /></label>
            <button type="button" className={button} disabled={!testKey || !!result} onClick={() => run(async () => {
                const start = new Date(Date.now() + 60000); const end = new Date(start.getTime() + 7 * 86400000);
                const storageKey = `calendly.setup.${connection.id}.${testKey}`;
                const p = await api('POST', '/test-proposals', { appointment_key: testKey, timezone: tz, start_datetime: start.toISOString(), end_datetime: end.toISOString(), operation: proposal?.operation || sessionStorage.getItem(storageKey) || undefined });
                sessionStorage.setItem(storageKey, p.operation);
                setProposal(p); setSlot(''); setAnswers({});
            })}>Find test times</button>
            {proposal && <>
                <label className="block text-sm">Available time<select className={input} value={slot} onChange={e => setSlot(e.target.value)}><option value="">Choose a time</option>{proposal.slots.map(s => <option key={s.slot_reference} value={s.slot_reference}>{s.start_datetime}</option>)}</select></label>
                {!proposal.slots.length && <p>No supported times in this search window. Review availability in Calendly.</p>}
                <label className="block text-sm">Test invitee name<input className={input} value={name} onChange={e => setName(e.target.value)} /></label>
                <label className="block text-sm">Test recipient email<input className={input} type="email" value={recipient} onChange={e => setRecipient(e.target.value)} /></label>
                {proposal.questions.map(q => <label key={q.position} className="block text-sm">{q.question}{q.type === 'single_select' ? <select className={input} value={answers[q.position] || ''} onChange={e => setAnswers({ ...answers, [q.position]: e.target.value })}><option value="">Choose an answer</option>{q.choices.map(c => <option key={c} value={c}>{c}</option>)}</select> : <input className={input} value={answers[q.position] || ''} onChange={e => setAnswers({ ...answers, [q.position]: e.target.value })} />}</label>)}
                <div className="rounded border border-border p-3 text-sm"><p>{proposal.appointment_name} · {proposal.duration_minutes} minutes · {proposal.location_summary}</p><p>{proposal.slots.find(s => s.slot_reference === slot)?.start_datetime} · {tz}</p><p>{name} · {recipient}</p></div>
                <label className="flex gap-2 text-sm"><input type="checkbox" checked={approved} onChange={e => setApproved(e.target.checked)} />I approve these details and authorize the invitation to this test email.</label>
                <button type="button" className={button} disabled={!approved || !slot || !name || !recipient || !!result} onClick={() => run(async () => {
                    const booked = await api('POST', '/test-bookings', { operation: proposal.operation, slot_reference: slot, name, email: recipient, answers: proposal.questions.map(q => ({ position: q.position, answer: answers[q.position] || '' })), booking_confirmed: approved, email_confirmed: approved });
                    setResult(booked); setSetupAttempt(booked.attempt_reference || '');
                    await refresh();
                })}>Create test booking and send invitation</button>
            </>}
            {result && <div className="space-y-3 rounded border border-border p-3"><p role="status">{result.message} {result.reservation_status}</p>
                {result.attempt_reference && <button type="button" className={button} onClick={() => run(async () => setResult(await api('GET', `/attempts/${result.attempt_reference}`)))}>Refresh saved result and management links</button>}
                {result.reservation_status === 'confirmed' && result.attempt_reference && <button type="button" className={button} onClick={() => run(async () => { await api('POST', `/attempts/${result.attempt_reference}/qualify`); await refresh(); setNotice('Exact and list tracking readback verified. Discovery reconciliation is qualified for this connection.'); })}>Verify tracking readback</button>}
                {result.reschedule_url && <a className="block underline text-primary" href={result.reschedule_url} target="_blank" rel="noopener noreferrer">Manage rescheduling in Calendly</a>}
                {result.cancel_url && <a className="block underline text-primary" href={result.cancel_url} target="_blank" rel="noopener noreferrer">Cancel test booking in Calendly</a>}
                <label className="flex gap-2 text-sm"><input type="checkbox" checked={invitationChecked} onChange={e => setInvitationChecked(e.target.checked)} />I received the invitation and checked its management links.</label>
                <label className="flex gap-2 text-sm"><input type="checkbox" checked={calendarChecked} onChange={e => setCalendarChecked(e.target.checked)} />I verified calendar placement and conflict blocking.</label>
                <label className="flex gap-2 text-sm"><input type="checkbox" checked={cleanupChecked} onChange={e => setCleanupChecked(e.target.checked)} />I cleaned up the test appointment in Calendly.</label>
                {result.reservation_status === 'confirmed' && result.attempt_reference && <button type="button" className={button} onClick={() => run(async () => {
                    await api('POST', `/attempts/${result.attempt_reference}/observations`, { invitation_received: invitationChecked, calendar_verified: calendarChecked, cleanup_reported: cleanupChecked });
                    setNotice('Operator checks recorded with a timestamp; these do not prove API delivery or RSVP.');
                })}>Record operator checks</button>}
                {result.reservation_status === 'confirmed' && cleanupChecked && proposal && result.attempt_reference === setupAttempt && <button type="button" className={button} onClick={() => {
                    sessionStorage.removeItem(`calendly.setup.${connection.id}.${testKey}`);
                    setProposal(null); setResult(null); setSlot(''); setApproved(false);
                    setCleanupChecked(false); setInvitationChecked(false); setCalendarChecked(false); setSetupAttempt('');
                }}>Start another setup test after cleanup</button>}
                <p className="text-xs text-muted-foreground">These checks are operator observations, not API proof of delivery or current booking status. Voice behavior also needs a test call.</p>
            </div>}
        </fieldset>}
        <fieldset disabled={busy} className="space-y-3 min-w-0"><legend className="text-lg font-medium">Pending requests and setup results</legend>
            <p className="text-sm">Discovery recovery: {connection.qualified ? 'Readback qualified' : 'Not qualified; lost responses without saved IDs require staff'}. A missing result never makes a second submission safe.</p>
            <button type="button" className={button} onClick={() => run(refresh)}>Refresh pending requests</button>
            <label className="block text-sm">External resolution note<input className={input} maxLength={500} value={resolutionNote} onChange={e => setResolutionNote(e.target.value)} /></label>
            {attempts.map(a => <div key={a.id} className="border-b border-border py-3 space-y-2"><p>{a.appointment_name || 'Scheduling request'} · {a.state} · {new Date(a.updated * 1000).toLocaleString()}</p><p className="text-xs break-all">Request reference: <span className="font-mono">{a.id}</span></p><div className="flex flex-wrap gap-2"><button type="button" className={button} onClick={() => run(async () => setResult(await api('GET', `/attempts/${a.id}`)))}>Inspect result</button>{['uncertain', 'needs_operator', 'submitting'].includes(a.state) && <button type="button" className={button} onClick={() => run(async () => { setResult(await api('POST', `/attempts/${a.id}/reconcile`)); await refresh(); })}>Check booking status</button>}{['uncertain', 'needs_operator'].includes(a.state) && <button type="button" className={button} disabled={!resolutionNote.trim()} onClick={() => run(async () => { await api('POST', `/attempts/${a.id}/external-resolution`, { note: resolutionNote }); setResolutionNote(''); await refresh(); })}>Record external resolution</button>}</div></div>)}
            <a href="https://calendly.com/app/scheduled_events/user/me" target="_blank" rel="noopener noreferrer" className="block underline text-primary">Open bookings in Calendly</a>
        </fieldset>
        {busy && <p role="status">Working… A booking is never resubmitted automatically.</p>}
    </div>;
}
