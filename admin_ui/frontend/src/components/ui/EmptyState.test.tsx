// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { Phone, Terminal } from 'lucide-react';
import { describe, expect, it, vi } from 'vitest';

import { EmptyState } from './EmptyState';

describe('EmptyState', () => {
    it('renders a heading and message with a decorative icon', () => {
        render(<EmptyState icon={Phone} title="No Calls Found" message="Call history will appear here." />);

        const title = screen.getByRole('heading', { name: 'No Calls Found', level: 2 });
        expect(screen.getByText('Call history will appear here.')).toBeInTheDocument();
        expect(title.parentElement?.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
        expect(screen.queryByRole('button')).not.toBeInTheDocument();
    });

    it('keeps an optional action interactive without requiring a message', () => {
        const onClick = vi.fn();
        render(<EmptyState icon={Phone} title="No Calls Found" action={<button onClick={onClick}>Clear filters</button>} />);

        fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
        expect(onClick).toHaveBeenCalledTimes(1);
        expect(screen.queryByRole('paragraph')).not.toBeInTheDocument();
    });

    it('preserves rich guidance and supports the surrounding heading hierarchy', () => {
        render(
            <EmptyState icon={Terminal} title="No debug logs found." variant="console" heading="h4"
                message={<>Enable <code>LOG_LEVEL=DEBUG</code><br />Then restart the container.</>} />,
        );

        expect(screen.getByRole('heading', { name: 'No debug logs found.', level: 4 })).toBeInTheDocument();
        expect(screen.getByText('LOG_LEVEL=DEBUG')).toBeInTheDocument();
        expect(screen.getByText(/Then restart the container/)).toBeInTheDocument();
    });
});
