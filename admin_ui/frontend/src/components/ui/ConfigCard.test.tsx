// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { ConfigCard } from './ConfigCard';

describe('ConfigCard operator controls', () => {
    it('renders the status heading and makes its refresh action available', () => {
        const refresh = vi.fn();
        render(<ConfigCard title="System Status" icon={<span aria-hidden="true">✓</span>}
            action={<button onClick={refresh}>Refresh status</button>}>Ready</ConfigCard>);
        expect(screen.getByRole('heading', { name: 'System Status' })).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Refresh status' }));
        expect(refresh).toHaveBeenCalledTimes(1);
        expect(screen.getByText('Ready')).toBeInTheDocument();
    });
    it('keeps plain cards free of an empty heading', () => {
        render(<ConfigCard>Content</ConfigCard>);
        expect(screen.queryByRole('heading')).not.toBeInTheDocument();
    });
});
