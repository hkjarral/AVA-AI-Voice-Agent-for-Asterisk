// @vitest-environment jsdom
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router-dom';
import axios from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import LogsPage from './LogsPage';

vi.mock('axios');

describe('LogsPage empty results', () => {
    beforeEach(() => {
        vi.mocked(axios.get).mockReset();
        Element.prototype.scrollIntoView = vi.fn();
    });
    afterEach(() => {
        vi.restoreAllMocks();
    });

    const renderLogs = (query: string, logs: string) => {
        vi.mocked(axios.get).mockResolvedValue({ data: { logs } });
        return render(<MemoryRouter initialEntries={['/logs?mode=raw' + query]}><LogsPage /></MemoryRouter>);
    };

    it('uses the shared empty state when the raw log output is empty', async () => {
        renderLogs('', '');
        expect(await screen.findByRole('heading', { name: 'No logs available...' })).toBeInTheDocument();
    });

    it('retains the search-specific message when existing lines do not match', async () => {
        renderLogs('&q=missing', 'INFO ready');
        expect(await screen.findByRole('heading', { name: 'No lines match the filter.' })).toBeInTheDocument();
        expect(screen.queryByRole('heading', { name: 'No logs available...' })).not.toBeInTheDocument();
    });

    it('preserves debug setup instructions and the selected container name', async () => {
        renderLogs('&raw_levels=debug&q=missing&container=local_ai_server', 'INFO ready');
        expect(await screen.findByRole('heading', { name: 'No debug logs found.' })).toBeInTheDocument();
        expect(screen.getByText('LOG_LEVEL=DEBUG')).toBeInTheDocument();
        expect(screen.getByText('docker compose up -d --force-recreate local_ai_server')).toBeInTheDocument();
    });

    it('retains guidance for a level-specific deep link with no matching lines', async () => {
        renderLogs('&raw_levels=trace&q=missing', 'INFO ready');
        expect(await screen.findByRole('heading', { name: 'No logs found for selected level(s): trace' })).toBeInTheDocument();
        expect(screen.getByText("Try selecting additional levels like 'info' or 'warning'.")).toBeInTheDocument();
    });
});
