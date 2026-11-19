import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { Loader2 } from 'lucide-react';
import { Modal } from '../ui/Modal';
import { FormInput } from '../ui/FormComponents';
import {
    additionErrors, DestinationAddition, discoveryMessage, EndpointCandidate,
    endpointIdentity, extensionContext, parseDiscovery, proposeAdditions, TransferConfig,
} from './endpointDiscovery';

interface Props {
    transfer: TransferConfig | undefined;
    onClose: () => void;
    onAdd: (rows: DestinationAddition[]) => void;
}

const secondary = 'px-3 py-2 rounded-md border border-border text-sm hover:bg-accent disabled:opacity-50';
const primary = 'px-3 py-2 rounded-md bg-primary text-primary-foreground text-sm hover:bg-primary/90 disabled:opacity-50';

export default function EndpointDiscoveryModal({ transfer, onClose, onAdd }: Props) {
    const [endpoints, setEndpoints] = useState<EndpointCandidate[]>([]);
    const [checkedAt, setCheckedAt] = useState('');
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [search, setSearch] = useState('');
    const [rows, setRows] = useState<DestinationAddition[] | null>(null);
    const [loading, setLoading] = useState(true);
    const [validating, setValidating] = useState(false);
    const [error, setError] = useState('');
    const [stale, setStale] = useState(false);
    const controller = useRef<AbortController | null>(null);
    const latestTransfer = useRef(transfer);
    latestTransfer.current = transfer;
    const busy = useRef(false);
    const reviewIntro = useRef<HTMLParagraphElement>(null);
    const reviewing = rows !== null;
    useEffect(() => {
        if (reviewing) reviewIntro.current?.focus();
    }, [reviewing]);
    const reviewedEndpoints = endpoints.filter(endpoint => selected.has(endpointIdentity(endpoint)));
    const errors = rows ? additionErrors(rows, transfer?.destinations || {}, extensionContext(transfer)) : [];

    const discover = async () => {
        controller.current?.abort();
        const request = new AbortController();
        controller.current = request;
        setLoading(true);
        setError('');
        setSelected(new Set());
        setRows(null);
        setStale(true);
        try {
            const response = await axios.get('/api/system/ari/endpoints', { signal: request.signal, timeout: 12000 });
            if (request.signal.aborted) return;
            const data = parseDiscovery(response.data);
            setEndpoints(data.endpoints);
            setCheckedAt(data.discovered_at);
            setStale(false);
        } catch (err) {
            if (!request.signal.aborted) setError(discoveryMessage(err));
        } finally {
            if (!request.signal.aborted) setLoading(false);
        }
    };

    useEffect(() => {
        void discover();
        return () => controller.current?.abort();
        // Fetch once per opening; draft edits must not trigger network requests.
    }, []);

    const add = async () => {
        if (!rows || busy.current || errors.some(Boolean) || stale) return;
        busy.current = true;
        setValidating(true);
        setError('');
        const request = new AbortController();
        controller.current = request;
        const snapshot = latestTransfer.current;
        try {
            await axios.post('/api/tools/transfer-destinations/validate', {
                additions: rows,
                existing_destinations: snapshot?.destinations || {},
                extension_context: extensionContext(snapshot),
            }, { signal: request.signal, timeout: 12000 });
            if (request.signal.aborted) return;
            // Recheck against current edits; unrelated background updates are retained.
            const conflict = additionErrors(rows, latestTransfer.current?.destinations || {}, extensionContext(latestTransfer.current)).find(Boolean);
            if (conflict) {
                setError(conflict);
                return;
            }
            onAdd(rows);
        } catch (err) {
            if (!request.signal.aborted) {
                setError('The destinations could not be validated. Review the fields and existing destinations, then retry. No destinations were added.');
            }
        } finally {
            busy.current = false;
            if (!request.signal.aborted) setValidating(false);
        }
    };

    const visible = endpoints.filter(endpoint => (endpoint.technology + '/' + endpoint.resource).toLowerCase().includes(search.toLowerCase()));
    return (
        <Modal isOpen onClose={onClose} size="xl"
            title={rows ? 'Review transfer destinations' : 'Discover Asterisk endpoints'}
            footer={<>
                <button type="button" className={secondary} onClick={onClose}>Cancel</button>
                {rows ? <>
                    <button type="button" className={secondary} disabled={validating} onClick={() => { setRows(null); setError(''); }}>Back</button>
                    <button type="button" className={primary} disabled={validating || errors.some(Boolean)} onClick={() => void add()}>
                        {validating ? 'Validating…' : 'Add to configuration'}
                    </button>
                </> : <button type="button" className={primary} disabled={loading || stale || !selected.size}
                    onClick={() => setRows(proposeAdditions(reviewedEndpoints, transfer?.destinations || {}, extensionContext(transfer)))}>
                    Review selected ({selected.size})
                </button>}
            </>}>
            <div className="space-y-4">
                <p className="text-sm text-muted-foreground">
                    Uses your configured Asterisk connection. Endpoints may include trunks; select only intended destinations.
                    Reported state does not guarantee someone can answer. This is not a complete PBX directory.
                </p>
                <p className="text-sm text-muted-foreground">
                    Connection settings saved in Asterisk settings may differ from those currently used by the AI Engine.
                </p>
                {error && <div role="alert" className="rounded-md border border-destructive/40 p-3 text-sm text-destructive">
                    {error} {!rows && <a className="underline" href="/asterisk" target="_blank" rel="noopener noreferrer">Open Asterisk settings</a>}
                </div>}
                {!rows ? <>
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <p role="status" className="text-sm text-muted-foreground">
                            {loading ? <span className="inline-flex items-center gap-2"><Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />Discovering endpoints…</span>
                                : checkedAt ? endpoints.length + ' endpoints · ' + (stale ? 'Previous snapshot: ' : 'Checked: ') + new Date(checkedAt).toLocaleString()
                                    : 'No discovery results'}
                        </p>
                        <button type="button" className={secondary} disabled={loading} onClick={() => void discover()}>{error ? 'Retry discovery' : 'Refresh endpoints'}</button>
                    </div>
                    {stale && !loading && checkedAt && <p className="text-sm text-muted-foreground">These are older results. Retry discovery before selecting endpoints.</p>}
                    <FormInput label="Search endpoints" value={search} onChange={event => setSearch(event.target.value)} />
                    <div className="max-h-80 overflow-auto rounded-md border border-border">
                        <table className="w-full text-left text-sm">
                            <thead className="bg-muted"><tr><th className="p-3">Select</th><th className="p-3">Endpoint</th><th className="p-3">Reported state</th></tr></thead>
                            <tbody>{visible.map(endpoint => {
                                const identity = endpointIdentity(endpoint);
                                const name = endpoint.technology + '/' + endpoint.resource;
                                return <tr key={identity} className="border-t border-border">
                                    <td className="p-3"><input type="checkbox" aria-label={'Select ' + name}
                                        checked={selected.has(identity)} disabled={loading || stale || (selected.size >= 100 && !selected.has(identity))}
                                        onChange={event => setSelected(current => {
                                            const next = new Set(current);
                                            if (event.target.checked) next.add(identity); else next.delete(identity);
                                            return next;
                                        })} /></td>
                                    <td className="p-3 break-all">{name}</td>
                                    <td className="p-3 capitalize">{endpoint.state}</td>
                                </tr>;
                            })}</tbody>
                        </table>
                        {!loading && !visible.length && <p className="p-4 text-sm text-muted-foreground">
                            {endpoints.length ? 'No endpoints match your search.' : 'No endpoints found. You can still add destinations manually.'}
                        </p>}
                    </div>
                    <p className="text-sm text-muted-foreground">{selected.size} selected · Up to 100 per batch. Nothing is saved by discovery.</p>
                </> : <>
                    <p ref={reviewIntro} tabIndex={-1} className="text-sm focus:outline-none">Confirm each dialable number, description, and context. Suggested endpoint numbers may not match your PBX dialplan.</p>
                    {rows.map((row, index) => (
                        <fieldset key={endpointIdentity(reviewedEndpoints[index])} disabled={validating} className="rounded-md border border-border p-4">
                            <legend className="px-2 text-sm font-medium break-all">
                                {reviewedEndpoints[index].technology}/{reviewedEndpoints[index].resource} · Extension
                            </legend>
                            <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
                                {([
                                    ['key', 'Destination key', 'A stable key used by Agent destination selections.'],
                                    ['target', 'Dialable extension', 'Confirm the number your PBX dials; leading zeros are preserved.'],
                                    ['description', 'Description / label', 'Enter the destination name you want the AI to understand.'],
                                    ['dialplan_context', 'Dialplan context', 'Confirm the context through which your PBX routes this number.'],
                                ] as const).map(([field, label, tooltip]) => <FormInput key={field} label={label} tooltip={tooltip}
                                    value={row[field]} onChange={event => setRows(current => current?.map((value, position) => position === index ? { ...value, [field]: event.target.value } : value) || null)} />)}
                            </div>
                            {errors[index] && <p role="alert" className="text-sm text-destructive">{errors[index]}</p>}
                        </fieldset>
                    ))}
                    <p className="text-sm text-muted-foreground">
                        Adds to your edits only. Save &amp; Apply activates destinations for new calls; active calls keep their configuration.
                        Agents using Inherit gain access when their transfer tools are enabled. Selected and None policies remain unchanged.
                        Transfer enablement, attended transfer, and Live Agents are not changed.
                    </p>
                </>}
            </div>
        </Modal>
    );
}
