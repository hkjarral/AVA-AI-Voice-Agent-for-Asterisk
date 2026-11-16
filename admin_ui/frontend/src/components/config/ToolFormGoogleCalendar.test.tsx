// @vitest-environment jsdom
import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router-dom';
import ToolForm from './ToolForm';

vi.mock('axios', () => ({
    default: {
        get: vi.fn(() => Promise.reject(new Error('offline fixture'))),
        post: vi.fn(() => Promise.reject(new Error('offline fixture'))),
        isCancel: vi.fn(() => false),
    },
}));

const initialConfig = {
    google_calendar: {
        enabled: true,
        free_prefix: 'Open',
        busy_prefix: 'Reserved',
        calendars: {},
    },
};

describe('Google Calendar availability compatibility help', () => {
    it('explains durable bookings and one-time legacy repair while retaining prefix editing', async () => {
        const onChange = vi.fn();
        const Harness = () => {
            const [config, setConfig] = useState(initialConfig);
            return <ToolForm section="google_calendar" config={config} onChange={(next) => {
                setConfig(next);
                onChange(next);
            }} />;
        };
        const view = render(<MemoryRouter><Harness /></MemoryRouter>);
        expect(screen.getByText(/Older AVA bookings have no booking marker/)).toBeVisible();
        expect(screen.getByText(/Unrelated titles remain ignored/)).toBeVisible();
        expect(screen.getByText(/bookings created by AVA/)).toBeVisible();
        expect(screen.getByLabelText('Free prefix')).toHaveValue('Open');
        expect(screen.getByLabelText('Busy prefix')).toHaveValue('Reserved');
        fireEvent.change(screen.getByLabelText('Busy prefix'), { target: { value: 'Booked' } });
        await waitFor(() => expect(onChange).toHaveBeenLastCalledWith({
            google_calendar: { ...initialConfig.google_calendar, busy_prefix: 'Booked' },
        }));
        const savedConfig = onChange.mock.lastCall?.[0];
        view.unmount();
        render(<MemoryRouter><ToolForm section="google_calendar" config={savedConfig} onChange={onChange} /></MemoryRouter>);
        expect(screen.getByLabelText('Busy prefix')).toHaveValue('Booked');
        expect(screen.getByLabelText('Free prefix')).toHaveValue('Open');
        expect(screen.getByText(/add your busy prefix to each older appointment title once/)).toBeVisible();
    });
});
