// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import TransferHandoffFields from './TransferHandoffFields';

function Harness({ override = false, disabled = false, global = undefined as any, change = vi.fn() }) {
    const [value, setValue] = useState({});
    return <TransferHandoffFields value={value} global={global} override={override} disabled={disabled} onChange={next => { setValue(next); change(next); }} />;
}

describe('transfer announcement configuration', () => {
    it('defaults off and retains the sentence and bounded timing edits', () => {
        const change = vi.fn();
        render(<Harness change={change} />);
        fireEvent.click(screen.getByLabelText('Wait for transfer announcement'));
        expect(screen.getByRole('alert')).toHaveTextContent('Enter an announcement');
        fireEvent.change(screen.getByLabelText('Global announcement'), { target: { value: 'Please leave a message.' } });
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(screen.getByLabelText('Time to start speaking (seconds)')).toHaveValue(4);
        expect(screen.getByLabelText('Time to finish speaking (seconds)')).toHaveValue(20);
        fireEvent.change(screen.getByLabelText('Time to finish speaking (seconds)'), { target: { value: '100' } });
        fireEvent.blur(screen.getByLabelText('Time to finish speaking (seconds)'));
        expect(change.mock.lastCall?.[0].finish_timeout_sec).toBe(60);
        expect(change.mock.lastCall?.[0].message).toBe('Please leave a message.');
        fireEvent.click(screen.getByLabelText('Wait for transfer announcement'));
        expect(change.mock.lastCall?.[0].enabled).toBe(false);
        expect(change.mock.lastCall?.[0].message).toBe('Please leave a message.');
    });
    it('allows clearing and replacing timing values and clamps only on blur', () => {
        const change = vi.fn();
        render(<Harness change={change} />);
        fireEvent.click(screen.getByLabelText('Wait for transfer announcement'));
        const input = screen.getByLabelText('Time to start speaking (seconds)');
        fireEvent.change(input, { target: { value: '' } });
        expect(input).toHaveValue(null);
        fireEvent.change(input, { target: { value: '6' } });
        expect(input).toHaveValue(6);
        fireEvent.blur(input);
        expect(change.mock.lastCall?.[0].start_timeout_sec).toBe(6);
        fireEvent.change(input, { target: { value: '0' } });
        fireEvent.blur(input);
        expect(input).toHaveValue(0.1);
        fireEvent.change(input, { target: { value: '' } });
        fireEvent.blur(input);
        expect(input).toHaveValue(4);
        expect(change.mock.lastCall?.[0].start_timeout_sec).toBe(4);
    });
    it('shows inheritance, explicit disable, and custom message independently', () => {
        render(<Harness override global={{ enabled: true, message: 'Global sentence' }} />);
        expect(screen.getByText('Effective announcement: Global sentence')).toBeInTheDocument();
        fireEvent.change(screen.getByLabelText('Announcement setting'), { target: { value: 'disabled' } });
        expect(screen.getByText('Effective announcement: Disabled')).toBeInTheDocument();
        fireEvent.change(screen.getByLabelText('Announcement setting'), { target: { value: 'custom' } });
        fireEvent.change(screen.getByLabelText('Custom announcement'), { target: { value: 'Custom sentence' } });
        expect(screen.getByText('Effective announcement: Custom sentence')).toBeInTheDocument();
    });
    it('disables the configuration when deferred transfer is disabled', () => {
        render(<Harness disabled />);
        expect(screen.getByLabelText('Wait for transfer announcement')).toBeDisabled();
    });
});
