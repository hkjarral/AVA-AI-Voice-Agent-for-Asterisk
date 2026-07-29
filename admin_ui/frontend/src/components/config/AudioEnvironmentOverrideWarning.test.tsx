// @vitest-environment jsdom

import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AudioEnvironmentOverrideWarning from './AudioEnvironmentOverrideWarning';

vi.mock('axios');

describe('AudioEnvironmentOverrideWarning', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it('shows known active resampler overrides without offering to clear them', async () => {
        vi.mocked(axios.get).mockResolvedValue({
            data: {
                AAVA_OPENAI_OUTPUT_RESAMPLER: 'bandlimited',
                AAVA_GOOGLE_OUTPUT_RESAMPLER: '',
                OPENAI_API_KEY: 'must-not-render',
            },
        });

        render(
            <MemoryRouter>
                <AudioEnvironmentOverrideWarning />
            </MemoryRouter>,
        );

        expect(await screen.findByText('Environment audio override active')).toBeInTheDocument();
        expect(screen.getByText('AAVA_OPENAI_OUTPUT_RESAMPLER=bandlimited')).toBeInTheDocument();
        expect(screen.queryByText(/must-not-render/)).not.toBeInTheDocument();
        expect(screen.getByRole('link', { name: /System.*Environment/i })).toHaveAttribute('href', '/env');
    });

    it('stays hidden when no known override is active', async () => {
        const dataRead = vi.fn(() => ({ OPENAI_API_KEY: 'hidden' }));
        vi.mocked(axios.get).mockResolvedValue({
            get data() {
                return dataRead();
            },
        });

        render(
            <MemoryRouter>
                <AudioEnvironmentOverrideWarning />
            </MemoryRouter>,
        );

        await waitFor(() => {
            expect(dataRead).toHaveBeenCalled();
        });
        expect(axios.get).toHaveBeenCalledWith('/api/config/env');
        expect(screen.queryByText('Environment audio override active')).not.toBeInTheDocument();
    });
});
