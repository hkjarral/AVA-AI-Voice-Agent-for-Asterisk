// @vitest-environment jsdom

import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, expect, it, vi } from 'vitest';

import TransportPage from './TransportPage';

const mocks = vi.hoisted(() => ({
    config: {
        audio_transport: 'externalmedia',
        asterisk: {
            app_name: 'asterisk-ai-voice-agent',
            ws_ping_interval_sec: 10,
            ws_ping_timeout_sec: 10,
        },
        external_media: {
            rtp_host: '127.0.0.1',
            rtp_port: 18080,
            codec: 'ulaw',
            format: 'slin16',
            sample_rate: 16000,
        },
    } as Record<string, unknown>,
}));

vi.mock('sonner', () => ({
    toast: {
        error: vi.fn(),
        success: vi.fn(),
        warning: vi.fn(),
        info: vi.fn(),
    },
}));
vi.mock('../../hooks/useConfirmDialog', () => ({
    useConfirmDialog: () => ({ confirm: vi.fn() }),
}));
vi.mock('../../hooks/useRestartRequired', () => ({
    useRestartRequired: () => ({ restartRequired: false, refetch: vi.fn() }),
}));
vi.mock('../../utils/configCache', () => ({
    getCachedConfig: () => ({ config: mocks.config, yamlError: null }),
    loadConfigYaml: vi.fn().mockResolvedValue({ config: mocks.config, yamlError: null }),
}));

describe('TransportPage audio profile guidance', () => {
    it('shows the supported profile boundary for ExternalMedia RTP', async () => {
        render(<TransportPage />);

        expect(await screen.findByText('Supported Audio Profiles')).toBeInTheDocument();
        expect(screen.getByText('telephony_ulaw_8k')).toBeInTheDocument();
        expect(screen.getByText('telephony_enhanced_8k')).toBeInTheDocument();
        expect(screen.getByText('wideband_pcm_16k')).toBeInTheDocument();
        expect(screen.getByText(/AudioSocket-only/i)).toBeInTheDocument();
    });

    it('has no transport-level Format control for AudioSocket — the audio profile owns the wire format', async () => {
        const originalTransport = mocks.config.audio_transport;
        mocks.config.audio_transport = 'audiosocket';
        mocks.config.audiosocket = { host: '127.0.0.1', port: 8090, format: 'slin' };
        try {
            render(<TransportPage />);

            expect(await screen.findByText('AudioSocket Settings')).toBeInTheDocument();
            expect(screen.getByLabelText('Port')).toBeInTheDocument();
            expect(screen.queryByLabelText('Format')).not.toBeInTheDocument();
            expect(
                screen.getByText('Wire format comes from the audio profile')
            ).toBeInTheDocument();
        } finally {
            mocks.config.audio_transport = originalTransport;
            delete mocks.config.audiosocket;
        }
    });

    it('shows the bounded ARI keepalive controls', async () => {
        render(<TransportPage />);

        const interval = await screen.findByLabelText('ARI Ping Interval (seconds)');
        const timeout = screen.getByLabelText('ARI Ping Timeout (seconds)');

        expect(interval).toHaveValue(10);
        expect(timeout).toHaveValue(10);

        fireEvent.change(interval, { target: { value: '99' } });
        expect(interval).toHaveValue(60);
        fireEvent.change(interval, { target: { value: '1' } });
        expect(interval).toHaveValue(5);
        fireEvent.change(timeout, { target: { value: '' } });
        expect(timeout).toHaveValue(10);
    });
});
