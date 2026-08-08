// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import '@testing-library/jest-dom/vitest';

import { SIDEBAR_COLLAPSED_KEY } from '../../hooks/useSidebarCollapsed';

// Sidebar reads the auth context; stub it so the component renders in isolation.
vi.mock('../../auth/AuthContext', () => ({
    useAuth: () => ({ user: { username: 'admin' }, logout: vi.fn() }),
}));

import Sidebar from './Sidebar';

const renderSidebar = () =>
    render(
        <MemoryRouter>
            <Sidebar />
        </MemoryRouter>
    );

beforeEach(() => {
    localStorage.clear();
});

/**
 * Accessibility (WCAG 1.3.1): the primary navigation must be exposed as a
 * navigation landmark so screen-reader users can jump to it. The sidebar was a
 * plain <aside> with the nav links in an unlabeled <div>.
 */
describe('Sidebar — navigation landmark', () => {
    it('exposes the primary navigation as a named navigation landmark', () => {
        renderSidebar();
        expect(screen.getByRole('navigation', { name: /main navigation/i })).toBeInTheDocument();
    });
});

describe('Sidebar — collapse (issue #596)', () => {
    it('hides nav labels when collapsed but keeps items reachable by accessible name', () => {
        localStorage.setItem(SIDEBAR_COLLAPSED_KEY, '1');
        renderSidebar();

        // Visible label text is gone...
        expect(screen.queryByText('Dashboard')).not.toBeInTheDocument();
        // ...but the link is still named (aria-label) for screen readers.
        expect(screen.getByRole('link', { name: 'Dashboard' })).toBeInTheDocument();
        // Toggle reflects collapsed state.
        expect(screen.getByRole('button', { name: /expand sidebar/i })).toHaveAttribute('aria-expanded', 'false');
    });

    it('toggle button expands the sidebar and reveals labels', async () => {
        const user = userEvent.setup();
        localStorage.setItem(SIDEBAR_COLLAPSED_KEY, '1');
        renderSidebar();

        await user.click(screen.getByRole('button', { name: /expand sidebar/i }));

        expect(screen.getByText('Dashboard')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /collapse sidebar/i })).toHaveAttribute('aria-expanded', 'true');
    });
});
