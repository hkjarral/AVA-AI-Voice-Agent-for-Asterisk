// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, expect, it, vi } from 'vitest';
import ConnectionRecoveryFields from './ConnectionRecoveryFields';

 describe('initial connection recovery', () => {
    it('displays legacy defaults without mutating an existing provider', () => {
        const onChange = vi.fn();
        const config = { enabled: true, greeting: 'Hello' };
        const { rerender } = render(<ConnectionRecoveryFields config={config} onChange={onChange} />);
        expect(screen.getByLabelText('Connection timeout per attempt (sec)')).toHaveValue(10);
        expect(screen.getByLabelText('Initial connection retries')).toHaveValue(0);
        expect(screen.getByLabelText('Maximum connection wait (sec)')).toHaveValue(null);
        rerender(<ConnectionRecoveryFields config={{ ...config, greeting: 'Updated' }} onChange={onChange} />);
        expect(onChange).not.toHaveBeenCalled();
        expect(config).toEqual({ enabled: true, greeting: 'Hello' });
    });
    it('preserves explicit zero on save/reload', () => {
        const onChange = vi.fn();
        const { rerender } = render(<ConnectionRecoveryFields config={{ connect_max_retries: 1 }} onChange={onChange} />);
        fireEvent.change(screen.getByLabelText('Initial connection retries'), { target: { value: '0' } });
        expect(onChange).toHaveBeenLastCalledWith({ connect_max_retries: 0 });
        rerender(<ConnectionRecoveryFields config={JSON.parse(JSON.stringify(onChange.mock.calls[0][0]))} onChange={onChange} />);
        expect(screen.getByLabelText('Initial connection retries')).toHaveValue(0);
    });
    it('clears an optional deadline without setting it to zero', () => {
        const onChange = vi.fn();
        render(<ConnectionRecoveryFields config={{ connect_total_timeout_sec: 25, connect_max_retries: 0 }} onChange={onChange} />);
        fireEvent.change(screen.getByLabelText('Maximum connection wait (sec)'), { target: { value: '' } });
        expect(onChange).toHaveBeenLastCalledWith({ connect_total_timeout_sec: undefined, connect_max_retries: 0 });
    });
 });
