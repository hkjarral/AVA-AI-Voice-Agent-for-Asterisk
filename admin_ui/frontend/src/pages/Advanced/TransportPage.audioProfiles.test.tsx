// @vitest-environment jsdom

import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, expect, it, vi } from 'vitest';

import TransportPage from './TransportPage';

const mocks = vi.hoisted(() => ({
    config: {
        audio_transport: 'externalmedia',
        asterisk: { app_name: 'asterisk-ai-voice-agent' },
        external_media: {
            rtp_host: '127.0.0.1',
            rtp_port: 18080,
            codec: 'ulaw',
            format: 'slin16',
            sample_rate: 16000,
        },
    },
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
});
