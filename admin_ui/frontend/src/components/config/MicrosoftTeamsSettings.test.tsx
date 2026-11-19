// @vitest-environment jsdom
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import ToolForm from './ToolForm';

vi.mock('axios', () => ({ default: {
    get: vi.fn(() => Promise.reject(new Error('synthetic defaults unavailable'))),
    post: vi.fn(), isCancel: vi.fn(() => false),
} }));

const initial = { microsoft_calendar: { enabled: true, invitations_enabled: true,
    accounts: { default: { tenant_id: 'synthetic', client_id: 'synthetic',
        user_principal_name: 'scheduler@example.com', calendar_id: 'selected', timezone: 'America/Phoenix',
        token_cache_path: '/synthetic/cache' } } }, transfer: { enabled: false } };

function Harness({ saved = () => undefined }: { saved?: (value: typeof initial) => void }) {
    const [config, setConfig] = useState(initial);
    return <ToolForm config={config} section="microsoft_calendar" onChange={next => { setConfig(next); saved(next); }} />;
}

beforeEach(() => {
    vi.mocked(axios.post).mockReset();
    vi.mocked(axios.get).mockReset().mockRejectedValue(new Error('synthetic defaults unavailable'));
});

describe('Microsoft Teams configuration and verification', () => {
    it('retains the saved named calendar when reconnecting discovers a different default', async () => {
        const saved = vi.fn();
        vi.mocked(axios.post).mockResolvedValue({ data: { flow_id: 'synthetic-flow' } });
        vi.mocked(axios.get).mockImplementation(async url => {
            if (String(url).includes('/device/status/')) return { data: { status: 'success', result: {
                user_principal_name: 'scheduler@example.com', token_cache_path: '/synthetic/cache',
                calendars: [{ id: 'another-default', name: 'Default', is_default_calendar: true }],
            } } } as never;
            throw new Error('synthetic defaults unavailable');
        });
        render(<Harness saved={saved} />);
        await act(async () => { fireEvent.click(screen.getByTitle('Start device-code OAuth. The operator signs in at microsoft.com/devicelogin.')); });
        expect(saved.mock.calls[saved.mock.calls.length - 1]?.[0].microsoft_calendar.accounts.default.calendar_id).toBe('selected');
        expect(screen.getByText('Connected as scheduler@example.com.')).toBeInTheDocument();
    });

    it('defaults off, saves and reopens Teams mode without changing the selected account or other tools', async () => {
        const saved = vi.fn();
        const view = render(<Harness saved={saved} />);
        await act(async () => {});
        expect(screen.getByLabelText('Create Microsoft Teams meetings')).not.toBeChecked();
        expect(saved).not.toHaveBeenCalled();
        fireEvent.click(screen.getByLabelText('Create Microsoft Teams meetings'));
        const next = saved.mock.calls[saved.mock.calls.length - 1]?.[0];
        expect(next.microsoft_calendar.teams_enabled).toBe(true);
        expect(next.microsoft_calendar.accounts).toEqual(initial.microsoft_calendar.accounts);
        expect(next.transfer).toEqual(initial.transfer);
        view.rerender(<ToolForm config={next} onChange={saved} section="microsoft_calendar" />);
        expect(screen.getByLabelText('Create Microsoft Teams meetings')).toBeChecked();
    });

    it('verifies the draft Teams setting and clears success after mode changes', async () => {
        vi.mocked(axios.post).mockResolvedValue({ data: { calendar_name: 'Selected', teams_supported: true } });
        render(<Harness />);
        await act(async () => {});
        fireEvent.click(screen.getByLabelText('Create Microsoft Teams meetings'));
        await act(async () => { fireEvent.click(screen.getByTitle('Verify the connected account can see the configured calendar.')); });
        expect(axios.post).toHaveBeenCalledWith('/api/config/microsoft-calendar/verify', expect.objectContaining({ calendar_id: 'selected', teams_enabled: true }));
        expect(screen.getByText(/Teams supported.*Save & Apply/)).toBeInTheDocument();
        fireEvent.click(screen.getByLabelText('Create Microsoft Teams meetings'));
        expect(screen.queryByText(/Reachable: Selected/)).not.toBeInTheDocument();
    });

    it.each(['calendar', 'timezone', 'teams'])('discards delayed success after editing %s', async field => {
        let resolve!: (value: unknown) => void;
        vi.mocked(axios.post).mockReturnValue(new Promise(done => { resolve = done; }));
        render(<Harness />);
        await act(async () => {});
        fireEvent.click(screen.getByTitle('Verify the connected account can see the configured calendar.'));
        if (field === 'teams') fireEvent.click(screen.getByLabelText('Create Microsoft Teams meetings'));
        else fireEvent.change(screen.getByLabelText(field === 'calendar' ? 'Calendar ID' : 'Timezone'), { target: { value: 'changed' } });
        await act(async () => { resolve({ data: { calendar_name: 'STALE', teams_supported: true } }); });
        expect(screen.queryByText(/Reachable: STALE/)).not.toBeInTheDocument();
    });

    it('discards a delayed error after selection changes', async () => {
        let reject!: (value: unknown) => void;
        vi.mocked(axios.post).mockReturnValue(new Promise((_done, fail) => { reject = fail; }));
        render(<Harness />);
        await act(async () => {});
        fireEvent.click(screen.getByTitle('Verify the connected account can see the configured calendar.'));
        fireEvent.change(screen.getByLabelText('Calendar ID'), { target: { value: 'changed' } });
        await act(async () => { reject({ response: { data: { detail: { message: 'STALE FAILURE' } } } }); });
        expect(screen.queryByText(/STALE FAILURE/)).not.toBeInTheDocument();
    });
});
