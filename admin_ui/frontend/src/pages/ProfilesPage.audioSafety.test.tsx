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
                provider_pref: { output_sample_rate_hz: 8000 },
                transport_out: { encoding: 'ulaw', sample_rate_hz: 8000 },
            },
            telephony_enhanced_8k: {
                output_resampler: 'bandlimited',
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
                talk_detect_talking_threshold: 1000,
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

const mockProfilePageGets = (
    agents: unknown[] | Error,
    builtInProfiles = [
        'telephony_ulaw_8k',
        'telephony_enhanced_8k',
        'openai_realtime_24k',
        'wideband_pcm_16k',
    ],
) => {
    vi.mocked(axios.get).mockImplementation(async (url) => {
        if (url === '/api/config/profiles/audio/baselines') {
            return {
                data: {
                    built_in_profiles: builtInProfiles,
                },
            };
        }
        if (url === '/api/agents') {
            if (agents instanceof Error) throw agents;
            return { data: agents };
        }
        return { data: {} };
    });
};

describe('ProfilesPage audio contract safety', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it('shows the effective support class and blocks deletion of an in-use profile', async () => {
        mockProfilePageGets([
            { slug: 'ava-demo', name: 'Ava Demo', audio_profile: 'openai_realtime_24k' },
        ]);

        render(<ProfilesPage />);

        expect(await screen.findByText('Provider Native · 8 kHz Wire')).toBeInTheDocument();
        expect(screen.getByText('Enhanced Telephony')).toBeInTheDocument();
        expect(screen.getByText('Alias-safe')).toBeInTheDocument();
        expect(screen.getByText('Opt-in Wideband · Asterisk 20.17+')).toBeInTheDocument();
        expect(screen.getByText('1000')).toBeInTheDocument();
        expect(await screen.findByText('Used By Agents')).toBeInTheDocument();
        expect(screen.getByText('Ava Demo')).toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: 'Delete profile openai_realtime_24k' }));

        await waitFor(() => expect(mocks.toastError).toHaveBeenCalled());
        expect(mocks.confirm).not.toHaveBeenCalled();
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('fails closed when Agent usage cannot be loaded', async () => {
        const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
        mockProfilePageGets(new Error('unavailable'));

        render(<ProfilesPage />);

        expect(await screen.findByText(/Agent usage could not be verified/i)).toBeInTheDocument();
        expect(
            screen.getByRole('button', { name: 'Delete profile openai_realtime_24k' })
        ).toBeDisabled();
        expect(axios.post).not.toHaveBeenCalled();
        consoleError.mockRestore();
    });

    it('treats agents without an explicit profile as users of the configured default', async () => {
        mockProfilePageGets([
            { slug: 'default-agent', display_name: 'Default Agent', audio_profile: null },
        ]);

        render(<ProfilesPage />);

        expect(await screen.findByText('Default Agent')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Delete profile telephony_ulaw_8k' }));

        await waitFor(() => expect(mocks.toastError).toHaveBeenCalled());
        expect(mocks.confirm).not.toHaveBeenCalled();
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('removes a per-profile TALK_DETECT threshold when the field is cleared', async () => {
        mockProfilePageGets([]);
        vi.mocked(axios.post).mockResolvedValue({
            data: { recommended_apply_method: 'restart' },
        });

        render(<ProfilesPage />);

        fireEvent.click(await screen.findByText('wideband_pcm_16k'));
        const threshold = screen.getByRole('spinbutton', {
            name: /TALK_DETECT Talking Threshold/i,
        });
        fireEvent.change(threshold, { target: { value: '' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));

        await waitFor(() => expect(axios.post).toHaveBeenCalled());
        const request = vi.mocked(axios.post).mock.calls[0][1] as { content: string };
        const saved = yaml.load(request.content) as {
            profiles: Record<string, Record<string, unknown>>;
        };
        expect(saved.profiles.wideband_pcm_16k).not.toHaveProperty(
            'talk_detect_talking_threshold'
        );
    });

    it('restores an in-use built-in profile through the dedicated backend action', async () => {
        mockProfilePageGets([
            { slug: 'default-agent', display_name: 'Default Agent', audio_profile: null },
        ]);
        mocks.confirm.mockResolvedValue(true);
        vi.mocked(axios.post).mockResolvedValue({
            data: { recommended_apply_method: 'hot_reload' },
        });

        render(<ProfilesPage />);

        fireEvent.click(await screen.findByRole('button', {
            name: 'Restore profile baseline for telephony_ulaw_8k',
        }));

        await waitFor(() => {
            expect(axios.post).toHaveBeenCalledWith(
                '/api/config/profiles/telephony_ulaw_8k/audio/reset',
            );
        });
        expect(axios.get).toHaveBeenCalledWith('/api/config/profiles/audio/baselines');
        expect(mocks.confirm.mock.calls[0][0].description).toMatch(/built-in profile.*shipped baseline/i);
        expect(mocks.confirm.mock.calls[0][0].description).toMatch(/Agent assignments.*not changed/i);
        expect(await screen.findByText('Changes saved. Apply to make them active.')).toBeInTheDocument();
    });

    it('uses server metadata rather than local profile-name assumptions', async () => {
        mockProfilePageGets([], []);
        mocks.confirm.mockResolvedValue(true);
        vi.mocked(axios.post).mockResolvedValue({
            data: { recommended_apply_method: 'none' },
        });

        render(<ProfilesPage />);

        await waitFor(() => {
            expect(axios.get).toHaveBeenCalledWith('/api/config/profiles/audio/baselines');
        });
        fireEvent.click(screen.getByRole('button', {
            name: 'Restore profile baseline for telephony_enhanced_8k',
        }));

        await waitFor(() => expect(mocks.confirm).toHaveBeenCalled());
        expect(mocks.confirm.mock.calls[0][0].description).toMatch(
            /custom profile.*standard 8 kHz telephony baseline/i,
        );
    });

    it.each(['hot_reload', 'none'] as const)(
        'does not clear or downgrade a pending restart when a later reset recommends %s',
        async (laterMethod) => {
            mockProfilePageGets([]);
            mocks.confirm.mockResolvedValue(true);
            vi.mocked(axios.post)
                .mockResolvedValueOnce({ data: { recommended_apply_method: 'restart' } })
                .mockResolvedValueOnce({ data: { recommended_apply_method: laterMethod } });

            render(<ProfilesPage />);

            const reset = await screen.findByRole('button', {
                name: 'Restore profile baseline for telephony_enhanced_8k',
            });
            fireEvent.click(reset);
            expect(await screen.findByText('Changes saved. Restart required to make them active.')).toBeInTheDocument();

            fireEvent.click(reset);
            await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));

            expect(screen.getByText('Changes saved. Restart required to make them active.')).toBeInTheDocument();
            expect(screen.getByRole('button', { name: 'Restart AI Engine' })).toBeInTheDocument();
        },
    );

    it('starts with hot reload and escalates the pending action to restart', async () => {
        mockProfilePageGets([]);
        mocks.confirm.mockResolvedValue(true);
        vi.mocked(axios.post)
            .mockResolvedValueOnce({ data: { recommended_apply_method: 'hot_reload' } })
            .mockResolvedValueOnce({ data: { recommended_apply_method: 'restart' } });

        render(<ProfilesPage />);

        const reset = await screen.findByRole('button', {
            name: 'Restore profile baseline for telephony_enhanced_8k',
        });
        fireEvent.click(reset);
        expect(await screen.findByText('Changes saved. Apply to make them active.')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Apply Changes' })).toBeInTheDocument();

        fireEvent.click(reset);
        expect(await screen.findByText('Changes saved. Restart required to make them active.')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Restart AI Engine' })).toBeInTheDocument();
    });

    it('does not downgrade a pending restart when a later profile save recommends hot reload', async () => {
        mockProfilePageGets([]);
        mocks.confirm.mockResolvedValue(true);
        vi.mocked(axios.post)
            .mockResolvedValueOnce({ data: { recommended_apply_method: 'restart' } })
            .mockResolvedValueOnce({ data: { recommended_apply_method: 'hot_reload' } });

        render(<ProfilesPage />);

        fireEvent.click(await screen.findByRole('button', {
            name: 'Restore profile baseline for telephony_enhanced_8k',
        }));
        expect(await screen.findByText('Changes saved. Restart required to make them active.')).toBeInTheDocument();

        fireEvent.click(screen.getByText('telephony_enhanced_8k'));
        const dialog = await screen.findByRole('dialog', {
            name: 'Edit Profile: telephony_enhanced_8k',
        });
        fireEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));

        expect(screen.getByText('Changes saved. Restart required to make them active.')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Restart AI Engine' })).toBeInTheDocument();
    });
});
