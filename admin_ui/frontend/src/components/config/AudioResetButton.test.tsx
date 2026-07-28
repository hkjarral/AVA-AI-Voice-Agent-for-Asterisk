// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AudioResetButton from './AudioResetButton';

const mocks = vi.hoisted(() => ({
    confirm: vi.fn(),
    toastSuccess: vi.fn(),
    toastError: vi.fn(),
}));

vi.mock('axios');
vi.mock('sonner', () => ({
    toast: {
        success: mocks.toastSuccess,
        error: mocks.toastError,
    },
}));
vi.mock('../../hooks/useConfirmDialog', () => ({
    useConfirmDialog: () => ({ confirm: mocks.confirm }),
}));

describe('AudioResetButton', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it('previews preserved provider settings and calls the contextual endpoint', async () => {
        const onResetComplete = vi.fn();
        mocks.confirm.mockResolvedValue(true);
        vi.mocked(axios.post).mockResolvedValue({
            data: { recommended_apply_method: 'restart' },
        });

        render(
            <AudioResetButton
                scope="provider"
                target="acme/openai"
                onResetComplete={onResetComplete}
            />,
        );

        fireEvent.click(screen.getByRole('button', { name: 'Restore audio defaults' }));

        await waitFor(() => expect(mocks.confirm).toHaveBeenCalled());
        expect(mocks.confirm.mock.calls[0][0].description).toMatch(/Credentials, models, voices, prompts.*preserved/i);
        await waitFor(() => {
            expect(axios.post).toHaveBeenCalledWith(
                '/api/config/providers/acme%2Fopenai/audio/reset',
            );
        });
        expect(onResetComplete).toHaveBeenCalledWith({ recommended_apply_method: 'restart' });
        expect(mocks.toastSuccess).toHaveBeenCalled();
    });

    it('explains custom-profile behavior and surfaces backend failure details', async () => {
        mocks.confirm.mockResolvedValue(true);
        vi.mocked(axios.post).mockRejectedValue({
            response: { data: { detail: 'Baseline validation failed' } },
        });

        render(
            <AudioResetButton
                scope="profile"
                target="customer_wideband"
                customProfile
                onResetComplete={vi.fn()}
            />,
        );

        fireEvent.click(screen.getByRole('button', { name: 'Restore profile baseline' }));

        await waitFor(() => expect(mocks.confirm).toHaveBeenCalled());
        expect(mocks.confirm.mock.calls[0][0].description).toMatch(/keeping its current name/i);
        await waitFor(() => {
            expect(mocks.toastError).toHaveBeenCalledWith(
                'Failed to restore audio settings for "customer_wideband"',
                { description: 'Baseline validation failed' },
            );
        });
    });

    it('does not direct the operator to an apply banner when the reset is already active', async () => {
        mocks.confirm.mockResolvedValue(true);
        vi.mocked(axios.post).mockResolvedValue({
            data: { recommended_apply_method: 'none' },
        });

        render(
            <AudioResetButton
                scope="pipeline"
                target="local_only"
                onResetComplete={vi.fn()}
            />,
        );

        fireEvent.click(screen.getByRole('button', { name: 'Restore audio defaults' }));

        await waitFor(() => {
            expect(mocks.toastSuccess).toHaveBeenCalledWith(
                'Restore audio defaults',
                { description: 'Audio settings for "local_only" were restored and are already active.' },
            );
        });
        expect(mocks.toastSuccess.mock.calls[0][1].description).not.toMatch(/apply banner/i);
    });
});
