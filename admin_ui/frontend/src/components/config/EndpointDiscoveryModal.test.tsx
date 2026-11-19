// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import EndpointDiscoveryModal from './EndpointDiscoveryModal';
import { additionErrors, EndpointCandidate, proposeAdditions, stageAdditions } from './endpointDiscovery';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
const endpoints: EndpointCandidate[] = [
    { technology: 'PJSIP', resource: '0012', state: 'offline' },
    { technology: 'PJSIP', resource: 'carrier-trunk', state: 'online' },
    { technology: 'SIP', resource: '103', state: 'unknown' },
];
const payload = { endpoints, discovered_at: '2026-10-20T10:00:00Z' };
const row = { key: 'sales', target: '0012', description: 'Sales', dialplan_context: 'custom-context' };
const transfer = { enabled: false, extension_context: 'custom-context', destinations: { support: { type: 'queue', target: '300', extra: 'preserve' }, placeholder: null } };

beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(axios.get).mockResolvedValue({ data: payload });
    vi.mocked(axios.post).mockResolvedValue({ data: { destinations: {} } });
});

async function review() {
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select PJSIP/0012' }));
    fireEvent.click(screen.getByRole('button', { name: 'Review selected (1)' }));
    fireEvent.change(screen.getByLabelText('Description / label'), { target: { value: 'Sales' } });
}

describe('Endpoint discovery', () => {
    it('requires selection and explicit review; offline candidates are selectable and discovery never writes', async () => {
        const onAdd = vi.fn();
        render(<EndpointDiscoveryModal transfer={transfer} onClose={vi.fn()} onAdd={onAdd} />);
        expect(screen.getByRole('button', { name: 'Review selected (0)' })).toBeDisabled();
        expect(await screen.findByText('PJSIP/carrier-trunk')).toBeInTheDocument();
        expect(axios.post).not.toHaveBeenCalled();
        expect(onAdd).not.toHaveBeenCalled();
        await review();
        expect(screen.getByLabelText('Dialable extension')).toHaveValue('0012');
        expect(screen.getByLabelText('Dialplan context')).toHaveValue('custom-context');
        expect(screen.getByText(/Agents using Inherit gain access/)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Add to configuration' }));
        await waitFor(() => expect(onAdd).toHaveBeenCalledOnce());
        expect(axios.post).toHaveBeenCalledWith('/api/tools/transfer-destinations/validate', {
            additions: [{ ...row, key: 'ext_0012' }], existing_destinations: transfer.destinations, extension_context: 'custom-context',
        }, expect.objectContaining({ signal: expect.any(AbortSignal), timeout: 12000 }));
    });

    it('keeps hidden search selections and leaves nonnumeric dialable numbers blank', async () => {
        render(<EndpointDiscoveryModal transfer={transfer} onClose={vi.fn()} onAdd={vi.fn()} />);
        fireEvent.click(await screen.findByRole('checkbox', { name: 'Select PJSIP/0012' }));
        fireEvent.change(screen.getByLabelText('Search endpoints'), { target: { value: 'carrier' } });
        expect(screen.queryByRole('checkbox', { name: 'Select PJSIP/0012' })).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('checkbox', { name: 'Select PJSIP/carrier-trunk' }));
        fireEvent.click(screen.getByRole('button', { name: 'Review selected (2)' }));
        expect(screen.getAllByLabelText('Dialable extension').map(input => (input as HTMLInputElement).value)).toEqual(['0012', '']);
        expect(screen.getByRole('button', { name: 'Add to configuration' })).toBeDisabled();
    });

    it('blocks duplicates including unsaved edits and null placeholders', async () => {
        const config = { ...transfer, destinations: { ...transfer.destinations, sales: null, old: { type: 'extension', target: '0012' } } };
        render(<EndpointDiscoveryModal transfer={config} onClose={vi.fn()} onAdd={vi.fn()} />);
        await review();
        expect(screen.getByText(/already uses this number and context/)).toBeInTheDocument();
        fireEvent.change(screen.getByLabelText('Dialable extension'), { target: { value: '104' } });
        fireEvent.change(screen.getByLabelText('Destination key'), { target: { value: 'sales' } });
        expect(screen.getByText(/destination key already exists/)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Add to configuration' })).toBeDisabled();
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('renders sanitized failure, retries, and prevents using an older snapshot after refresh failure', async () => {
        render(<EndpointDiscoveryModal transfer={transfer} onClose={vi.fn()} onAdd={vi.fn()} />);
        await screen.findByText('PJSIP/0012');
        vi.mocked(axios.get).mockRejectedValueOnce({ response: { data: { detail: { category: 'authentication', message: 'SECRET RESPONSE BODY' } } } });
        fireEvent.click(screen.getByRole('button', { name: 'Refresh endpoints' }));
        await screen.findByText(/Asterisk authentication failed/);
        expect(screen.queryByText(/SECRET RESPONSE BODY/)).not.toBeInTheDocument();
        expect(screen.getByRole('checkbox', { name: 'Select PJSIP/0012' })).toBeDisabled();
        fireEvent.click(screen.getByRole('button', { name: 'Retry discovery' }));
        await waitFor(() => expect(screen.getByRole('checkbox', { name: 'Select PJSIP/0012' })).toBeEnabled());
    });

    it('shows an empty inventory and rejects malformed or partial results', async () => {
        vi.mocked(axios.get).mockResolvedValueOnce({ data: { ...payload, endpoints: [] } });
        render(<EndpointDiscoveryModal transfer={transfer} onClose={vi.fn()} onAdd={vi.fn()} />);
        await screen.findByText(/No endpoints found/);
        vi.mocked(axios.get).mockResolvedValueOnce({ data: { ...payload, endpoints: [...endpoints, null] } });
        fireEvent.click(screen.getByRole('button', { name: 'Refresh endpoints' }));
        await screen.findByText(/Endpoint discovery failed/);
        expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    });

    it('aborts on close/unmount and ignores a late discovery response', async () => {
        let resolve!: (value: { data: unknown }) => void;
        vi.mocked(axios.get).mockReturnValueOnce(new Promise(done => { resolve = done; }));
        const onAdd = vi.fn();
        const { unmount } = render(<EndpointDiscoveryModal transfer={transfer} onClose={vi.fn()} onAdd={onAdd} />);
        const signal = vi.mocked(axios.get).mock.calls[0][1]?.signal;
        unmount();
        expect(signal?.aborted).toBe(true);
        await act(async () => resolve({ data: payload }));
        expect(onAdd).not.toHaveBeenCalled();
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('cancels pending validation without staging and allows only one submission', async () => {
        let resolve!: (value: { data: unknown }) => void;
        vi.mocked(axios.post).mockReturnValueOnce(new Promise(done => { resolve = done; }));
        const onAdd = vi.fn();
        const { unmount } = render(<EndpointDiscoveryModal transfer={transfer} onClose={vi.fn()} onAdd={onAdd} />);
        await review();
        fireEvent.click(screen.getByRole('button', { name: 'Add to configuration' }));
        expect(screen.getByRole('button', { name: 'Validating…' })).toBeDisabled();
        expect(axios.post).toHaveBeenCalledOnce();
        const signal = vi.mocked(axios.post).mock.calls[0][2]?.signal;
        unmount();
        await act(async () => resolve({ data: {} }));
        expect(signal?.aborted).toBe(true);
        expect(onAdd).not.toHaveBeenCalled();
    });

    it('retains reviewed edits when server validation fails without rendering its body', async () => {
        vi.mocked(axios.post).mockRejectedValueOnce({ response: { data: { detail: 'PRIVATE BODY' } } });
        const onAdd = vi.fn();
        render(<EndpointDiscoveryModal transfer={transfer} onClose={vi.fn()} onAdd={onAdd} />);
        await review();
        fireEvent.click(screen.getByRole('button', { name: 'Add to configuration' }));
        await screen.findByText(/No destinations were added/);
        expect(screen.getByLabelText('Description / label')).toHaveValue('Sales');
        expect(screen.queryByText(/PRIVATE BODY/)).not.toBeInTheDocument();
        expect(onAdd).not.toHaveBeenCalled();
    });

    it('rechecks conflicts against the latest draft while validation is pending', async () => {
        let resolve!: (value: { data: unknown }) => void;
        vi.mocked(axios.post).mockReturnValueOnce(new Promise(done => { resolve = done; }));
        const onAdd = vi.fn();
        const { rerender } = render(<EndpointDiscoveryModal transfer={transfer} onClose={vi.fn()} onAdd={onAdd} />);
        await review();
        fireEvent.click(screen.getByRole('button', { name: 'Add to configuration' }));
        rerender(<EndpointDiscoveryModal transfer={{ ...transfer, destinations: { ext_0012: null } }} onClose={vi.fn()} onAdd={onAdd} />);
        await act(async () => resolve({ data: {} }));
        expect(onAdd).not.toHaveBeenCalled();
        expect(screen.getAllByText(/destination key already exists/).length).toBeGreaterThan(0);
    });
});

describe('Destination staging', () => {
    it('preserves every manual field, disabled state, and literal credential reference without persisting candidates', () => {
        const existing = { ...transfer, custom: { ref: '${TRANSFER_SECRET}', falsy: false } };
        const before = structuredClone(existing);
        const staged = stageAdditions(existing, [row]);
        expect(existing).toEqual(before);
        expect(staged).toEqual({
            ...before, destinations: { ...before.destinations, sales: { type: 'extension', target: '0012', description: 'Sales', dialplan_context: 'custom-context', attended_allowed: false } },
        });
        expect(JSON.stringify(staged)).not.toContain('technology');
    });

    it('checks the entire batch before staging and uses collision-free proposed keys', () => {
        expect(() => stageAdditions(transfer, [row, { ...row, key: 'another' }])).toThrow(/already uses/);
        expect(() => stageAdditions(transfer, [row, { ...row, target: '123' }])).toThrow(/key already exists/);
        expect(proposeAdditions([endpoints[0], endpoints[0]], { ext_0012: null }, 'ctx').map(value => value.key)).toEqual(['ext_0012_2', 'ext_0012_3']);
    });

    it('rejects unsafe numbers and contexts before making a validation request', () => {
        expect(additionErrors([{ ...row, target: '12@outside' }], {}, 'ctx')[0]).toMatch(/dialable extension/);
        expect(additionErrors([{ ...row, dialplan_context: 'ctx/../../' }], {}, 'ctx')[0]).toMatch(/dialplan context/);
        expect(additionErrors([{ ...row, target: '102\n' }], {}, 'ctx')[0]).toMatch(/dialable extension/);
        expect(additionErrors([{ ...row, key: 'sales\n' }], {}, 'ctx')[0]).toMatch(/destination key/);
    });
});
