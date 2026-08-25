// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import yaml from 'js-yaml';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ProfilesPage from './ProfilesPage';

const mocks = vi.hoisted(() => ({
    confirm: vi.fn(),
    toastError: vi.fn(),
    config: {
        profiles: {
            default: 'telephony_ulaw_8k',
            telephony_ulaw_8k: {
                output_resampler: 'linear',
                internal_rate_hz: 8000,
                chunk_ms: 'auto',
                idle_cutoff_ms: 800,
                provider_pref: {
                    input_encoding: 'mulaw',
                    input_sample_rate_hz: 8000,
                    output_encoding: 'mulaw',
                    output_sample_rate_hz: 8000,
                },
                transport_out: { encoding: 'ulaw', sample_rate_hz: 8000 },
            },
            wideband_pcm_16k: {
                internal_rate_hz: 16000,
                provider_pref: {
                    input_encoding: 'linear16',
                    input_sample_rate_hz: 16000,
                    output_encoding: 'linear16',
                    output_sample_rate_hz: 16000,
                },
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

const mockGets = (agents: unknown[] = []) => {
    vi.mocked(axios.get).mockImplementation(async (url) => {
        if (url === '/api/config/profiles/audio/baselines') {
            return { data: { built_in_profiles: ['telephony_ulaw_8k', 'wideband_pcm_16k'] } };
        }
        if (url === '/api/agents') {
            return { data: agents };
        }
        return { data: {} };
    });
};

const openEditDialog = async (profileName: string) => {
    fireEvent.click(await screen.findByText(profileName));
    return screen.findByRole('dialog', { name: `Edit Profile: ${profileName}` });
};

const lastSavedProfiles = () => {
    const calls = vi.mocked(axios.post).mock.calls;
    const request = calls[calls.length - 1][1] as { content: string };
    return (yaml.load(request.content) as any).profiles as Record<string, any>;
};

describe('ProfilesPage audio markup', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mockGets();
        vi.mocked(axios.post).mockResolvedValue({
            data: { recommended_apply_method: 'hot_reload' },
        });
    });

    it('offers A-law for provider preferences and transport encodings', async () => {
        render(<ProfilesPage />);
        const dialog = await openEditDialog('telephony_ulaw_8k');

        const inputEncoding = within(dialog).getByRole('combobox', { name: 'Input Encoding' });
        expect(within(inputEncoding).getByRole('option', { name: 'A-law — G.711, 8 kHz' })).toBeInTheDocument();

        const transportEncoding = within(dialog).getByRole('combobox', { name: 'Encoding' });
        expect(within(transportEncoding).getByRole('option', { name: 'A-law — G.711, 8 kHz' })).toBeInTheDocument();
        expect(within(transportEncoding).getByRole('option', { name: 'SLIN — PCM16, 8 kHz' })).toBeInTheDocument();
        expect(within(transportEncoding).getByRole('option', { name: 'SLIN16 — PCM16, 16 kHz' })).toBeInTheDocument();
    });

    it('locks G.711 provider sample rates to 8000 and frees them for PCM', async () => {
        render(<ProfilesPage />);
        const dialog = await openEditDialog('telephony_ulaw_8k');

        const inputRate = within(dialog).getByRole('spinbutton', { name: 'Input Sample Rate (Hz)' });
        expect(inputRate).toBeDisabled();
        expect(inputRate).toHaveValue(8000);

        fireEvent.change(within(dialog).getByRole('combobox', { name: 'Input Encoding' }), {
            target: { value: 'pcm16' },
        });
        expect(within(dialog).getByRole('spinbutton', { name: 'Input Sample Rate (Hz)' })).toBeEnabled();

        fireEvent.change(within(dialog).getByRole('combobox', { name: 'Input Encoding' }), {
            target: { value: 'alaw' },
        });
        const relocked = within(dialog).getByRole('spinbutton', { name: 'Input Sample Rate (Hz)' });
        expect(relocked).toBeDisabled();
        expect(relocked).toHaveValue(8000);
    });

    it('keeps the transport rate locked to the encoding and saves the pair', async () => {
        render(<ProfilesPage />);
        const dialog = await openEditDialog('telephony_ulaw_8k');

        const transportRate = within(dialog).getByRole('spinbutton', { name: 'Sample Rate (Hz)' });
        expect(transportRate).toBeDisabled();
        expect(transportRate).toHaveValue(8000);

        fireEvent.change(within(dialog).getByRole('combobox', { name: 'Encoding' }), {
            target: { value: 'slin16' },
        });
        expect(within(dialog).getByRole('spinbutton', { name: 'Sample Rate (Hz)' })).toHaveValue(16000);

        fireEvent.change(within(dialog).getByRole('combobox', { name: 'Encoding' }), {
            target: { value: 'alaw' },
        });
        fireEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }));

        await waitFor(() => expect(axios.post).toHaveBeenCalled());
        const saved = lastSavedProfiles();
        expect(saved.telephony_ulaw_8k.transport_out).toEqual({ encoding: 'alaw', sample_rate_hz: 8000 });
    });

    it('adds and removes the optional transport_in leg via the toggle', async () => {
        render(<ProfilesPage />);
        const dialog = await openEditDialog('telephony_ulaw_8k');

        const toggle = within(dialog).getByRole('checkbox', { name: 'Set Transport Input separately' });
        expect(toggle).not.toBeChecked();

        fireEvent.click(toggle);
        // Transport Input section renders its own Encoding select seeded from transport_out.
        const encodingSelects = within(dialog).getAllByRole('combobox', { name: 'Encoding' });
        expect(encodingSelects).toHaveLength(2);
        fireEvent.change(encodingSelects[1], { target: { value: 'alaw' } });

        fireEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }));
        await waitFor(() => expect(axios.post).toHaveBeenCalled());
        let saved = lastSavedProfiles();
        expect(saved.telephony_ulaw_8k.transport_in).toEqual({ encoding: 'alaw', sample_rate_hz: 8000 });

        // Re-open, switch the toggle off: transport_in must be removed again.
        mocks.config.profiles.telephony_ulaw_8k = saved.telephony_ulaw_8k;
        const dialog2 = await openEditDialog('telephony_ulaw_8k');
        const toggle2 = within(dialog2).getByRole('checkbox', { name: 'Set Transport Input separately' });
        expect(toggle2).toBeChecked();
        fireEvent.click(toggle2);
        fireEvent.click(within(dialog2).getByRole('button', { name: 'Save Changes' }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
        saved = lastSavedProfiles();
        expect(saved.telephony_ulaw_8k).not.toHaveProperty('transport_in');
    });
});

describe('ProfilesPage in-use profile confirmation', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it('confirms an in-use edit and sends the override flag', async () => {
        mockGets([{ slug: 'ava-demo', display_name: 'Ava Demo', audio_profile: 'telephony_ulaw_8k' }]);
        mocks.confirm.mockResolvedValue(true);
        vi.mocked(axios.post).mockResolvedValue({
            data: { recommended_apply_method: 'hot_reload' },
        });

        render(<ProfilesPage />);
        expect(await screen.findByText('Ava Demo')).toBeInTheDocument();
        const dialog = await openEditDialog('telephony_ulaw_8k');
        fireEvent.change(within(dialog).getByRole('spinbutton', { name: 'Idle Cutoff (ms)' }), {
            target: { value: '900' },
        });
        fireEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }));

        await waitFor(() => expect(axios.post).toHaveBeenCalled());
        expect(mocks.confirm).toHaveBeenCalledTimes(1);
        expect(mocks.confirm.mock.calls[0][0].title).toMatch(/in-use profile/i);
        expect(mocks.confirm.mock.calls[0][0].description).toMatch(/Ava Demo/);
        expect(mocks.confirm.mock.calls[0][0].description).toMatch(/read once at call start/i);
        const body = vi.mocked(axios.post).mock.calls[0][1] as Record<string, unknown>;
        expect(body.allow_in_use_profile_changes).toBe(true);
        expect(mocks.toastError).not.toHaveBeenCalled();
    });

    it('does not save when the in-use confirmation is declined', async () => {
        mockGets([{ slug: 'ava-demo', display_name: 'Ava Demo', audio_profile: 'telephony_ulaw_8k' }]);
        mocks.confirm.mockResolvedValue(false);

        render(<ProfilesPage />);
        expect(await screen.findByText('Ava Demo')).toBeInTheDocument();
        const dialog = await openEditDialog('telephony_ulaw_8k');
        fireEvent.change(within(dialog).getByRole('spinbutton', { name: 'Idle Cutoff (ms)' }), {
            target: { value: '900' },
        });
        fireEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }));

        await waitFor(() => expect(mocks.confirm).toHaveBeenCalled());
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('recovers from the backend 409 guard with a confirmed retry', async () => {
        // Agent list is empty client-side (stale), so the first save goes out
        // without the flag and the backend guard answers 409.
        mockGets([]);
        mocks.confirm.mockResolvedValue(true);
        const guardError = Object.assign(new Error('Conflict'), {
            response: {
                status: 409,
                data: {
                    detail:
                        'Cannot change audio profile configuration used by an Agent. '
                        + 'Assign or migrate the Agent first (telephony_ulaw_8k: Ava Demo).',
                },
            },
        });
        vi.mocked(axios.post)
            .mockRejectedValueOnce(guardError)
            .mockResolvedValueOnce({ data: { recommended_apply_method: 'hot_reload' } });

        render(<ProfilesPage />);
        const dialog = await openEditDialog('telephony_ulaw_8k');
        fireEvent.change(within(dialog).getByRole('spinbutton', { name: 'Idle Cutoff (ms)' }), {
            target: { value: '900' },
        });
        fireEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }));

        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
        const firstBody = vi.mocked(axios.post).mock.calls[0][1] as Record<string, unknown>;
        expect(firstBody.allow_in_use_profile_changes).toBeUndefined();
        const retryBody = vi.mocked(axios.post).mock.calls[1][1] as Record<string, unknown>;
        expect(retryBody.allow_in_use_profile_changes).toBe(true);
        expect(mocks.confirm).toHaveBeenCalledTimes(1);
        expect(mocks.confirm.mock.calls[0][0].description).toMatch(/Ava Demo/);
        expect(mocks.toastError).not.toHaveBeenCalled();
    });
});
