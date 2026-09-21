// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, expect, it, vi } from 'vitest';

import GenericProviderForm from './GenericProviderForm';

vi.mock('./ProviderCredentialsCard', () => ({
    default: () => <div data-testid="provider-credentials" />,
}));

const fishAudioConfig = {
    name: 'fishaudio_tts',
    type: 'fishaudio',
    capabilities: ['tts'],
    enabled: true,
    base_url: 'https://api.fish.audio/v1',
    model: 's2.1-pro-free',
    reference_id: 'voice-reference',
    audio_format: 'pcm',
    latency: 'low',
    chunk_length: 200,
    temperature: 0.7,
    top_p: 0.7,
    connect_timeout_sec: 10,
    read_timeout_sec: 30,
    output_resampler: 'inherit',
    normalize: true,
};

describe('GenericProviderForm subtype fields', () => {
    it('shows subtype-owned settings once and keeps only advanced keys below', () => {
        render(<GenericProviderForm config={fishAudioConfig} onChange={vi.fn()} />);

        expect(screen.getByLabelText('Model *')).toHaveValue('s2.1-pro-free');
        expect(screen.getByLabelText('Voice Reference ID *')).toHaveValue('voice-reference');
        expect(screen.getByText('Additional Configuration Fields')).toBeInTheDocument();

        expect(screen.queryByDisplayValue('model')).not.toBeInTheDocument();
        expect(screen.queryByDisplayValue('reference_id')).not.toBeInTheDocument();
        expect(screen.queryByDisplayValue('audio_format')).not.toBeInTheDocument();
        expect(screen.getByDisplayValue('normalize')).toBeInTheDocument();
    });

    it('updates the structured model without a stale custom field overriding it', () => {
        const onChange = vi.fn();
        render(<GenericProviderForm config={fishAudioConfig} onChange={onChange} />);

        fireEvent.change(screen.getByLabelText('Model *'), { target: { value: 's2.1-pro' } });

        expect(onChange).toHaveBeenLastCalledWith({
            ...fishAudioConfig,
            model: 's2.1-pro',
        });
        expect(screen.queryByDisplayValue('model')).not.toBeInTheDocument();
    });

    it('preserves structured values while editing an additional field', () => {
        const onChange = vi.fn();
        render(<GenericProviderForm config={fishAudioConfig} onChange={onChange} />);

        fireEvent.change(screen.getByDisplayValue('true'), { target: { value: 'false' } });

        expect(onChange).toHaveBeenLastCalledWith({
            ...fishAudioConfig,
            normalize: false,
        });
    });
});
