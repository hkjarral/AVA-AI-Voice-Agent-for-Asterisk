// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import axios from 'axios';
import { ConfirmDialogProvider } from '../hooks/useConfirmDialog';
import AgentEditorPage from './AgentEditorPage';

vi.mock('axios');
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(axios.get).mockImplementation(async url => ({ data: url === '/api/agents/sales'
        ? { slug: 'sales', display_name: 'Sales', provider: 'p', prompt: 'Keep', is_active: 1, is_default: 1 }
        : String(url).endsWith('/yaml') ? { content: 'providers: {}' } : { tools: [], providers: [] } }));
});

it('protects browser Back with keep-editing and discard flows', async () => {
    const router = createMemoryRouter([
        { path: '/agents', element: <p>Agents overview</p> },
        { path: '/agents/:slug/edit', element: <AgentEditorPage /> },
    ], { initialEntries: ['/agents', '/agents/sales/edit'], initialIndex: 1 });
    render(<ConfirmDialogProvider><RouterProvider router={router} /></ConfirmDialogProvider>);
    await screen.findByDisplayValue('Sales');
    await userEvent.click(screen.getByRole('button', { name: 'Female' }));
    void router.navigate(-1);
    await userEvent.click(await screen.findByRole('button', { name: 'Keep editing' }));
    expect(screen.getByDisplayValue('Sales')).toBeInTheDocument();
    void router.navigate(-1);
    await userEvent.click(await screen.findByRole('button', { name: 'Discard changes' }));
    await waitFor(() => expect(screen.getByText('Agents overview')).toBeInTheDocument());
    expect(axios.patch).not.toHaveBeenCalled();
});
