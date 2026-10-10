// @vitest-environment jsdom
import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import ToolForm from './ToolForm';
import { MicrosoftBookingSettings, previewMicrosoftTemplate } from './MicrosoftBookingSettings';

vi.mock('axios', () => ({ default: { get: vi.fn(() => Promise.reject(new Error('synthetic'))), post: vi.fn(() => Promise.reject(new Error('synthetic'))), isCancel: vi.fn(() => false) } }));

function Harness({ saved }: { saved: (value: any) => void }) {
    const [config, setConfig] = useState<any>({ microsoft_calendar: { enabled: true, accounts: { default: { timezone: 'America/Phoenix', calendar_id: 'named-calendar' } } }, transfer: { enabled: false } });
    return <ToolForm config={config} onChange={next => { saved(next); setConfig(next); }} />;
}

describe('Microsoft calendar booking settings', () => {
    it('persists operator policy/hours/templates while preserving the named calendar and other tools', () => {
        const saved = vi.fn();
        render(<Harness saved={saved} />);
        expect(screen.queryByLabelText('Invitation body template (plain text)')).not.toBeInTheDocument();
        fireEvent.change(screen.getByLabelText('Working hours start (0–23)'), { target: { value: '8' } });
        fireEvent.click(screen.getByLabelText('Allow caller invitations'));
        fireEvent.change(screen.getByLabelText('Business name'), { target: { value: 'Example Business' } });
        fireEvent.change(screen.getByLabelText('Invitation subject template'), { target: { value: '{{business_name}}: {{meeting_purpose}}' } });
        fireEvent.change(screen.getByLabelText('Invitation body template (plain text)'), { target: { value: '{{business_name}}\n{{appointment_date}} {{start_time}}–{{end_time}}\n{{confirmed_notes}}' } });
        const stored = saved.mock.calls.at(-1)?.[0];
        expect(stored.microsoft_calendar.working_hours_start).toBe(8);
        expect(stored.microsoft_calendar.invitations_enabled).toBe(true);
        expect(stored.microsoft_calendar.accounts.default.calendar_id).toBe('named-calendar');
        expect(stored.transfer.enabled).toBe(false);
        const preview = screen.getByLabelText('Invitation preview');
        expect(preview).toHaveTextContent('Example Business: Consultation');
        expect(preview).toHaveTextContent('13:00–13:30');
        expect(preview).toHaveTextContent('Discuss service options.');
    });

    it('shows weekday defaults and saves changed working days', () => {
        const update = vi.fn();
        render(<MicrosoftBookingSettings config={{}} onChange={update} />);
        expect(screen.getByLabelText('Microsoft working day Monday')).toBeChecked();
        expect(screen.getByLabelText('Microsoft working day Sunday')).not.toBeChecked();
        fireEvent.click(screen.getByLabelText('Microsoft working day Sunday'));
        expect(update).toHaveBeenCalledWith({ working_days: [0, 1, 2, 3, 4, 6] });
    });

    it('renders text without interpreting injected markup or caller placeholders', () => {
        expect(previewMicrosoftTemplate('Hello {{caller_name}}', { caller_name: '{{contact_details}}' })).toBe('Hello {{contact_details}}');
        render(<MicrosoftBookingSettings config={{ invitations_enabled: true, business_name: '<script>unsafe()</script>' }} onChange={vi.fn()} />);
        expect(within(screen.getByLabelText('Invitation preview')).getByText(/<script>unsafe/)).toBeInTheDocument();
        expect(document.querySelector('script')).toBeNull();
        expect(previewMicrosoftTemplate('{{unsupported}}', {})).toContain('Unsupported placeholder');
    });
});


describe('Microsoft numeric field editing', () => {
    it.each([
        ['Working hours start (0–23)', 'working_hours_start', 8, 9],
        ['Working hours end (1–24)', 'working_hours_end', 18, 17],
        ['Maximum booking duration (minutes)', 'max_event_duration_minutes', 60, 240],
        ['Booking horizon (days)', 'booking_horizon_days', 90, 365],
    ])('keeps %s empty until replacement or blur', (label, key, replacement, fallback) => {
        const saved = vi.fn();
        render(<Harness saved={saved} />);
        const input = screen.getByLabelText(String(label));
        saved.mockClear();
        fireEvent.change(input, { target: { value: '' } });
        expect(input).toHaveValue(null);
        expect(saved).not.toHaveBeenCalled();
        fireEvent.change(input, { target: { value: String(replacement) } });
        expect(input).toHaveValue(replacement);
        expect(saved.mock.calls.at(-1)?.[0].microsoft_calendar[String(key)]).toBe(replacement);
        fireEvent.change(input, { target: { value: '' } });
        expect(input).toHaveValue(null);
        fireEvent.blur(input);
        expect(input).toHaveValue(fallback);
        expect(saved.mock.calls.at(-1)?.[0].microsoft_calendar[String(key)]).toBeUndefined();
    });

    it('persists zero for disabled limits', () => {
        const saved = vi.fn();
        render(<Harness saved={saved} />);
        const input = screen.getByLabelText('Booking horizon (days)');
        fireEvent.change(input, { target: { value: '' } });
        fireEvent.change(input, { target: { value: '0' } });
        fireEvent.blur(input);
        expect(input).toHaveValue(0);
        expect(saved.mock.calls.at(-1)?.[0].microsoft_calendar.booking_horizon_days).toBe(0);
    });
});

it('keeps upgrade settings unchanged until the operator explicitly adopts booking limits', () => {
    const saved = vi.fn();
    render(<Harness saved={saved} />);
    expect(screen.getByLabelText('Enforce working hours and booking horizon')).not.toBeChecked();
    expect(screen.getByLabelText('Allow caller invitations')).not.toBeChecked();
    expect(saved).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText('Enforce working hours and booking horizon'));
    expect(saved.mock.calls.at(-1)?.[0].microsoft_calendar.enforce_booking_limits).toBe(true);
    expect(saved.mock.calls.at(-1)?.[0].microsoft_calendar.accounts.default.calendar_id).toBe('named-calendar');
    expect(saved.mock.calls.at(-1)?.[0].microsoft_calendar.invitations_enabled).toBeUndefined();
});
