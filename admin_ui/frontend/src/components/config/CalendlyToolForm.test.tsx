// @vitest-environment jsdom
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import CalendlyToolForm from './CalendlyToolForm';

vi.mock('../../auth/AuthContext', () => ({ useAuth: () => ({ token: 'synthetic-admin-token' }) }));
vi.mock('axios', () => ({ default: { get: vi.fn(), request: vi.fn(), isAxiosError: () => false } }));

const connection = { id: 'a'.repeat(32), revision: 1, name: 'Alex', timezone: 'UTC', enabled: true, qualified: false };
const entry = { uri: 'https://api.calendly.com/event_types/demo', name: 'Demo', host: 'Alex', duration: 30, supported: true, reason: '', location_summary: 'Office' };
const binding = { uri: entry.uri, connection_id: connection.id, name: 'Demo', duration: 30, one_to_one_confirmed: true };

function mock(connected = true) {
    vi.mocked(axios.get).mockResolvedValue({ data: connected ? connection : { revision: 0, enabled: false } });
    vi.mocked(axios.request).mockImplementation(async request => {
        const path = request.url || '';
        if (path.endsWith('/connection/verify')) return { data: { name: 'Alex' } };
        if (path.endsWith('/connection')) return { data: connection };
        if (path.endsWith('/appointment-types')) return { data: [entry, { ...entry, uri: entry.uri + 'group', name: 'Group event', supported: false, reason: 'Group events are unsupported' }] };
        if (path.endsWith('/test-proposals')) return { data: { operation: 'setup:00000000-0000-4000-8000-000000000000', appointment_name: 'Demo', duration_minutes: 30, timezone: 'UTC', location_summary: 'Office', questions: [], slots: [{ slot_reference: 'slot-ref', start_datetime: '2030-01-01T12:00:00Z' }] } };
        if (path.endsWith('/test-bookings')) return { data: { status: 'pending', reservation_status: 'uncertain', message: 'Confirmation pending; ask staff', attempt_reference: 'attempt' } };
        return { data: [] };
    });
}

afterEach(() => { cleanup(); vi.resetAllMocks(); sessionStorage.clear(); });

describe('Calendly guided setup', () => {
    it('presents Calendly as optional beside self-hosted MCP booking', async () => {
        mock(false);
        render(<CalendlyToolForm config={{}} onChange={vi.fn()} onSave={vi.fn()} />);
        await screen.findByText('Not connected');
        expect(screen.getByText(/optional adapter/i)).toBeInTheDocument();
        expect(screen.getByText(/Streamable HTTP MCP support/i)).toBeInTheDocument();
        expect(screen.getByText(/Pushover operator notifications remain independently configurable/i)).toBeInTheDocument();
    });

    it('shows the request ID for matching pending calls to recovery', async () => {
        mock();
        const request = vi.mocked(axios.request).getMockImplementation()!;
        vi.mocked(axios.request).mockImplementation(async r => r.url?.endsWith('/attempts')
            ? { data: [{ id: 'request-ref-123', appointment_name: 'Demo', state: 'uncertain', updated: 1 }] }
            : request(r));
        render(<CalendlyToolForm config={{}} onChange={vi.fn()} onSave={vi.fn()} />);
        await screen.findByText(/Alex · UTC/);
        expect(await screen.findByText('request-ref-123')).toBeInTheDocument();
    });
    it('requires a candidate token for a first connection and allows checking a saved connection while paused', async () => {
        mock(false);
        const form = render(<CalendlyToolForm config={{}} onChange={vi.fn()} onSave={vi.fn()} />);
        await screen.findByText('Not connected');
        const verify = screen.getByRole('button', { name: 'Verify connection' });
        expect(verify).toBeDisabled();
        await userEvent.type(screen.getByLabelText('Personal access token'), 'synthetic-pat');
        expect(verify).toBeEnabled();
        await userEvent.clear(screen.getByLabelText('Personal access token'));
        expect(verify).toBeDisabled();
        expect(vi.mocked(axios.request).mock.calls.some(([r]) => r.url?.endsWith('/connection/verify'))).toBe(false);
        form.unmount();
        vi.mocked(axios.get).mockResolvedValue({ data: { ...connection, enabled: false } });
        render(<CalendlyToolForm config={{}} onChange={vi.fn()} onSave={vi.fn()} />);
        await screen.findByText(/Scheduling paused/);
        expect(screen.getByRole('button', { name: 'Verify connection' })).toBeEnabled();
    });
    it('keeps the PAT masked and unsaved when verification fails', async () => {
        mock(false);
        vi.mocked(axios.request).mockRejectedValue(new Error('synthetic failure'));
        render(<CalendlyToolForm config={{}} onChange={vi.fn()} onSave={vi.fn()} />);
        await userEvent.type(screen.getByLabelText('Personal access token'), 'synthetic-pat');
        await userEvent.click(screen.getByRole('button', { name: 'Verify connection' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Connection verification did not finish. No booking was submitted.');
        expect(screen.getByRole('button', { name: 'Save connection' })).toBeDisabled();
        expect(screen.getByLabelText('Personal access token')).toHaveAttribute('type', 'password');
        expect(screen.queryByText('synthetic-pat')).not.toBeInTheDocument();
    });
    it('masks a candidate token, verifies without saving, and invalidates verification after edits', async () => {
        mock(false);
        render(<CalendlyToolForm config={{}} onChange={vi.fn()} onSave={vi.fn()} />);
        await screen.findByText('Not connected');
        const candidate = screen.getByLabelText('Personal access token');
        expect(candidate).toHaveAttribute('type', 'password');
        await userEvent.type(candidate, 'private-synthetic-pat');
        await userEvent.click(screen.getByRole('button', { name: 'Verify connection' }));
        await screen.findByText(/Connection verified for Alex/);
        expect(screen.getByRole('button', { name: 'Save connection' })).toBeEnabled();
        expect(vi.mocked(axios.request).mock.calls.every(([r]) => r.method !== 'PUT')).toBe(true);
        await userEvent.type(candidate, '-edited');
        expect(screen.getByRole('button', { name: 'Save connection' })).toBeDisabled();
        expect(screen.queryByText('private-synthetic-pat')).not.toBeInTheDocument();
    });

    it('requires one-to-one acknowledgement and keeps unsupported types unselectable', async () => {
        mock();
        const changed = vi.fn();
        render(<CalendlyToolForm config={{}} onChange={changed} onSave={vi.fn()} />);
        await screen.findByText(/Alex · UTC/);
        await userEvent.click(screen.getByRole('button', { name: 'Refresh from Calendly' }));
        const demo = await screen.findByLabelText(/Demo · Alex/);
        expect(demo).toBeDisabled();
        await userEvent.click(screen.getByLabelText(/I checked that selected appointments/));
        expect(demo).toBeEnabled();
        expect(screen.getByLabelText(/Group event · Alex/)).toBeDisabled();
        await userEvent.click(demo);
        expect(changed.mock.calls[0][0].appointments.demo.connection_id).toBe(connection.id);
    });

    it('requires final approval, invalidates it on detail changes, and never automatically repeats a pending write', async () => {
        mock();
        function Form() {
            const [cfg, setCfg] = useState({ enabled: true, appointments: { demo: binding } });
            return <CalendlyToolForm config={cfg} onChange={v => setCfg(v as typeof cfg)} onSave={async () => {}} />;
        }
        render(<Form />);
        await screen.findByText(/Alex · UTC/);
        await userEvent.selectOptions(screen.getByLabelText('Appointment'), 'demo');
        await userEvent.click(screen.getByRole('button', { name: 'Find test times' }));
        await userEvent.selectOptions(await screen.findByLabelText('Available time'), 'slot-ref');
        await userEvent.type(screen.getByLabelText('Test invitee name'), 'Test');
        await userEvent.type(screen.getByLabelText('Test recipient email'), 'test@example.com');
        const submit = screen.getByRole('button', { name: 'Create test booking and send invitation' });
        expect(submit).toBeDisabled();
        await userEvent.click(screen.getByLabelText(/I approve these details/));
        expect(submit).toBeEnabled();
        fireEvent.change(screen.getByLabelText('Test recipient email'), { target: { value: 'corrected@example.com' } });
        await waitFor(() => expect(submit).toBeDisabled());
        await userEvent.click(screen.getByLabelText(/I approve these details/));
        await userEvent.click(submit);
        await screen.findByText(/Confirmation pending; ask staff/);
        expect(submit).toBeDisabled();
        expect(vi.mocked(axios.request).mock.calls.filter(([r]) => r.url?.endsWith('/test-bookings'))).toHaveLength(1);
    });
});

it('keeps setup operations separate across appointments and remount, and cleans only the selected operation', async () => {
    mock();
    const original = vi.mocked(axios.request).getMockImplementation()!;
    vi.mocked(axios.request).mockImplementation(async r => {
        const response = await original(r);
        if (r.url?.endsWith('/test-proposals')) response.data.operation = (r.data as { operation: string }).operation;
        if (r.url?.endsWith('/test-bookings')) response.data = { status: 'success', reservation_status: 'confirmed', message: 'Appointment booked', attempt_reference: 'attempt' };
        if (r.url?.endsWith('/attempts')) response.data = ['attempt', 'unrelated'].map(id => ({ id, state: 'confirmed', updated: 1 }));
        if (r.url?.includes('/attempts/')) response.data = { status: 'success', reservation_status: 'confirmed', message: 'Appointment booked', attempt_reference: r.url.split('/').pop() };
        return response;
    });
    const a = `calendly.setup.${connection.id}.demo`;
    const b = `calendly.setup.${connection.id}.other`;
    sessionStorage.setItem(a, 'setup:appointment-a');
    sessionStorage.setItem(b, 'setup:appointment-b');
    const props = { config: { appointments: { demo: binding, other: { ...binding, name: 'Other' } } }, onChange: vi.fn(), onSave: vi.fn() };
    const first = render(<CalendlyToolForm {...props} />);
    await screen.findByText(/Alex · UTC/);
    await userEvent.selectOptions(screen.getByLabelText('Appointment'), 'demo');
    await userEvent.click(screen.getByRole('button', { name: 'Find test times' }));
    await screen.findByLabelText('Available time');
    await userEvent.selectOptions(screen.getByLabelText('Appointment'), 'other');
    await userEvent.click(screen.getByRole('button', { name: 'Find test times' }));
    await screen.findByLabelText('Available time');
    first.unmount();
    render(<CalendlyToolForm {...props} />);
    await screen.findByText(/Alex · UTC/);
    await userEvent.selectOptions(screen.getByLabelText('Appointment'), 'demo');
    await userEvent.click(screen.getByRole('button', { name: 'Find test times' }));
    await userEvent.selectOptions(await screen.findByLabelText('Available time'), 'slot-ref');
    expect(vi.mocked(axios.request).mock.calls.filter(([r]) => r.url?.endsWith('/test-proposals')).map(([r]) => (r.data as { operation: string }).operation))
        .toEqual(['setup:appointment-a', 'setup:appointment-b', 'setup:appointment-a']);
    await userEvent.type(screen.getByLabelText('Test invitee name'), 'Test');
    await userEvent.type(screen.getByLabelText('Test recipient email'), 'test@example.com');
    await userEvent.click(screen.getByLabelText(/I approve these details/));
    await userEvent.click(screen.getByRole('button', { name: 'Create test booking and send invitation' }));
    await screen.findByText(/Appointment booked/);
    await userEvent.click(screen.getByLabelText(/I cleaned up the test appointment/));
    await userEvent.click(screen.getAllByRole('button', { name: 'Inspect result' })[1]);
    expect(screen.getByLabelText(/I cleaned up the test appointment/)).not.toBeChecked();
    await userEvent.click(screen.getByLabelText(/I cleaned up the test appointment/));
    expect(screen.queryByRole('button', { name: 'Start another setup test after cleanup' })).not.toBeInTheDocument();
    expect(sessionStorage.getItem(a)).toBe('setup:appointment-a');
    await userEvent.click(screen.getAllByRole('button', { name: 'Inspect result' })[0]);
    await userEvent.click(screen.getByLabelText(/I cleaned up the test appointment/));
    await userEvent.click(screen.getByRole('button', { name: 'Start another setup test after cleanup' }));
    expect(sessionStorage.getItem(a)).toBeNull();
    expect(sessionStorage.getItem(b)).toBe('setup:appointment-b');
});


it.each([null, {}, 'invalid'])('shows a recovery-load error for malformed attempts %j', async value => {
    mock();
    const original = vi.mocked(axios.request).getMockImplementation()!;
    vi.mocked(axios.request).mockImplementation(async r => r.url?.endsWith('/attempts') ? { data: value } : original(r));
    render(<CalendlyToolForm config={{}} onChange={vi.fn()} onSave={vi.fn()} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Unable to load pending Calendly requests');
    await userEvent.click(screen.getByRole('button', { name: 'Refresh pending requests' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('This action could not be confirmed');
    expect(screen.getByRole('button', { name: 'Refresh pending requests' })).toBeEnabled();
});
