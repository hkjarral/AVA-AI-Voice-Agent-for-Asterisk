// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ProfilesPage from './ProfilesPage';

const mocks = vi.hoisted(() => ({
    confirm: vi.fn(),
    toastError: vi.fn(),
    config: {
        profiles: {
            default: 'telephony_ulaw_8k',
            telephony_ulaw_8k: {
                internal_rate_hz: 8000,
                provider_pref: { output_sample_rate_hz: 8000 },
                transport_out: { encoding: 'ulaw', sample_rate_hz: 8000 },
            },
            openai_realtime_24k: {
                internal_rate_hz: 24000,
                provider_pref: { output_sample_rate_hz: 24000 },
                transport_out: { encoding: 'slin', sample_rate_hz: 8000 },
            },
            wideband_pcm_16k: {
                internal_rate_hz: 16000,
                provider_pref: { output_sample_rate_hz: 16000 },
                transport_out: { encoding: 'slin16', sample_rate_hz: 16000 },
            },
        },
    },
}));

vi.mock('axios');
vi.mock('sonner', () => ({
    toast: {
        error: mocks.toastError,
        success: vi.fn(),
        warning: vi.fn(),
    },
}));
vi.mock('../hooks/useConfirmDialog', () => ({
    useConfirmDialog: () => ({ confirm: mocks.confirm }),
}));
vi.mock('../utils/configCache', () => ({
    getCachedConfig: () => ({ config: mocks.config, yamlError: null }),
    loadConfigYaml: vi.fn().mockResolvedValue({ config: mocks.config, yamlError: null }),
}));

describe('ProfilesPage audio contract safety', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it('shows the effective support class and blocks deletion of an in-use profile', async () => {
        vi.mocked(axios.get).mockResolvedValue({
            data: [{ slug: 'ava-demo', name: 'Ava Demo', audio_profile: 'openai_realtime_24k' }],
        });

        render(<ProfilesPage />);

        expect(await screen.findByText('Provider Native · 8 kHz Wire')).toBeInTheDocument();
        expect(screen.getByText('Experimental Wideband')).toBeInTheDocument();
        expect(await screen.findByText('Used By Agents')).toBeInTheDocument();
        expect(screen.getByText('Ava Demo')).toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: 'Delete profile openai_realtime_24k' }));

        await waitFor(() => expect(mocks.toastError).toHaveBeenCalled());
        expect(mocks.confirm).not.toHaveBeenCalled();
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('fails closed when Agent usage cannot be loaded', async () => {
        const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
        vi.mocked(axios.get).mockRejectedValue(new Error('unavailable'));

        render(<ProfilesPage />);

        expect(await screen.findByText(/Agent usage could not be verified/i)).toBeInTheDocument();
        expect(
            screen.getByRole('button', { name: 'Delete profile openai_realtime_24k' })
        ).toBeDisabled();
        expect(axios.post).not.toHaveBeenCalled();
        consoleError.mockRestore();
    });
});
