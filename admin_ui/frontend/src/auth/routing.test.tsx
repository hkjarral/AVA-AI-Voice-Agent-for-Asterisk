// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { Link, MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RequireAuth } from './RequireAuth';
import LoginPage from '../pages/LoginPage';

const auth = vi.hoisted(() => ({
    isAuthenticated: false,
    loading: false,
    mustChangePassword: false,
    login: vi.fn(),
    changePassword: vi.fn(),
    logout: vi.fn(),
}));

vi.mock('./AuthContext', () => ({ useAuth: () => auth }));

function History() {
    const navigate = useNavigate();
    return <><h1>Protected history</h1><Link to="/agents">Agents</Link><button onClick={() => navigate(-1)}>Back</button></>;
}

function renderRoutes() {
    return render(
        <MemoryRouter initialEntries={['/public', '/history']} initialIndex={1}>
            <Routes>
                <Route path="/public" element={<h1>Public page</h1>} />
                <Route path="/login" element={<LoginPage />} />
                <Route path="/history" element={<RequireAuth><History /></RequireAuth>} />
                <Route path="/agents" element={<RequireAuth><History /></RequireAuth>} />
            </Routes>
        </MemoryRouter>,
    );
}

function submitLogin() {
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'review-user' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'synthetic-password' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
}

describe('authenticated route lifecycle', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        auth.isAuthenticated = false;
        auth.loading = false;
        auth.mustChangePassword = false;
        auth.login.mockResolvedValue(undefined);
    });

    it('returns to the protected destination after login and replaces the login history entry', async () => {
        auth.login.mockImplementation(async () => { auth.isAuthenticated = true; });
        renderRoutes();
        expect(screen.queryByText('Protected history')).not.toBeInTheDocument();
        submitLogin();
        await screen.findByText('Protected history');
        expect(auth.login).toHaveBeenCalledWith('review-user', 'synthetic-password');
        fireEvent.click(screen.getByRole('link', { name: 'Agents' }));
        fireEvent.click(screen.getByRole('button', { name: 'Back' }));
        expect(screen.getByText('Protected history')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Back' }));
        expect(await screen.findByText('Public page')).toBeInTheDocument();
        expect(screen.queryByLabelText('Password')).not.toBeInTheDocument();
    });

    it('keeps a failed login outside protected routes', async () => {
        auth.login.mockRejectedValue(new Error('synthetic authentication failure'));
        renderRoutes();
        submitLogin();
        expect(await screen.findByText('Invalid username or password')).toBeInTheDocument();
        expect(screen.queryByText('Protected history')).not.toBeInTheDocument();
    });

    it('does not reveal protected content while authentication is loading', () => {
        auth.loading = true;
        renderRoutes();
        expect(screen.getByRole('status')).toHaveTextContent('Loading');
        expect(screen.queryByText('Protected history')).not.toBeInTheDocument();
        expect(screen.queryByLabelText('Password')).not.toBeInTheDocument();
    });

    it('retains the mandatory password-change boundary for an authenticated account', () => {
        auth.isAuthenticated = true;
        auth.mustChangePassword = true;
        renderRoutes();
        expect(screen.getByRole('heading', { name: 'Password Change Required' })).toBeInTheDocument();
        expect(screen.queryByText('Protected history')).not.toBeInTheDocument();
    });
});
