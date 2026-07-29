// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import EnvPage from './EnvPage';

vi.mock('axios');
vi.mock('../../auth/AuthContext', () => ({
    useAuth: () => ({ token: 'test-token', loading: false }),
}));

const { confirmMock } = vi.hoisted(() => ({
    confirmMock: vi.fn().mockResolvedValue(false),
}));
vi.mock('../../hooks/useConfirmDialog', () => ({
    useConfirmDialog: () => ({ confirm: confirmMock }),
}));

describe('EnvPage Call History privacy', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        window.history.replaceState(null, '', '/env?section=call-history#system');
        window.requestAnimationFrame = callback => {
            callback(0);
            return 1;
        };
        Element.prototype.scrollIntoView = vi.fn();
        vi.mocked(axios.get).mockImplementation(async url => {
            if (url === '/api/config/env') {
                return {
                    data: {
                        CALL_HISTORY_ENABLED: 'true',
                        CALL_HISTORY_RETENTION_DAYS: '0',
                        CALL_HISTORY_DB_PATH: '/app/data/call_history.db',
                        CALL_HISTORY_TOOL_REDACTION_MODE: 'show_routing',
                    },
                };
            }
            if (url === '/api/config/env/status') {
                return { data: { apply_plan: [], pending_restart: false } };
            }
            if (url === '/api/config/yaml') return { data: { providers: {} } };
            throw new Error(`Unexpected GET ${url}`);
        });
    });

    it('shows all modes, warns for off, and requires confirmation before saving it', async () => {
        render(
            <MemoryRouter>
                <EnvPage />
            </MemoryRouter>
        );

        const select = await screen.findByLabelText('Tool Diagnostic Redaction');
        expect(select).toHaveValue('show_routing');
        expect(screen.getByRole('option', { name: /Strict/ })).toBeInTheDocument();
        expect(screen.getByRole('option', { name: /Show routing/ })).toBeInTheDocument();
        expect(screen.getByRole('option', { name: /Off/ })).toBeInTheDocument();
        expect(Element.prototype.scrollIntoView).toHaveBeenCalled();

        fireEvent.change(select, { target: { value: 'off' } });
        expect(screen.getByText(/Redaction is off/)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));

        await waitFor(() =>
            expect(confirmMock).toHaveBeenCalledWith(
                expect.objectContaining({
                    title: 'Disable Call History redaction?',
                    variant: 'destructive',
                })
            )
        );
        expect(axios.post).not.toHaveBeenCalled();
    });
});
