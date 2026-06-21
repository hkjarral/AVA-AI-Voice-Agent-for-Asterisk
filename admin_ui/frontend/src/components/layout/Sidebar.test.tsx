// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import '@testing-library/jest-dom/vitest';

// Sidebar reads the auth context; stub it so the component renders in isolation.
vi.mock('../../auth/AuthContext', () => ({
    useAuth: () => ({ user: { username: 'admin' }, logout: vi.fn() }),
}));

import Sidebar from './Sidebar';

/**
 * Accessibility (WCAG 1.3.1): the primary navigation must be exposed as a
 * navigation landmark so screen-reader users can jump to it. The sidebar was a
 * plain <aside> with the nav links in an unlabeled <div>.
 */
describe('Sidebar — navigation landmark', () => {
    it('exposes the primary navigation as a named navigation landmark', () => {
        render(
            <MemoryRouter>
                <Sidebar />
            </MemoryRouter>
        );
        expect(screen.getByRole('navigation', { name: /main navigation/i })).toBeInTheDocument();
    });
});
