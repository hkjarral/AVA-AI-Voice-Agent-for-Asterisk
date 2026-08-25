// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ElevenLabsProviderForm from './ElevenLabsProviderForm';
import { __resetProviderAudioBaselinesCacheForTests } from './ProviderAudioFormatSection';

vi.mock('../../../hooks/useConfirmDialog', () => ({
    useConfirmDialog: () => ({ confirm: vi.fn() }),
}));
vi.mock('axios');

const SERVER_BASELINES = {
    provider_baselines: {
        elevenlabs_agent: {
            input_encoding: 'ulaw',
            input_sample_rate_hz: 8000,
            provider_input_encoding: 'pcm16',
            provider_input_sample_rate_hz: 16000,
            output_encoding: 'pcm16',
            output_sample_rate_hz: 16000,
            target_encoding: 'ulaw',
            target_sample_rate_hz: 8000,
            output_resampler: 'inherit',
        },
    },
};

const controlBesideLabel = (label: string): HTMLInputElement | HTMLSelectElement => {
    const labelNode = screen.getByText(label, { selector: 'label' });
    const container = labelNode.parentElement?.parentElement;
    const control = container?.querySelector('input, select');
    if (!(control instanceof HTMLInputElement) && !(control instanceof HTMLSelectElement)) {
        throw new Error(`No form control found for ${label}`);
    }
    return control;
};

describe('ProviderAudioFormatSection (ElevenLabs Agent)', () => {
    beforeEach(() => {
        __resetProviderAudioBaselinesCacheForTests();
        vi.mocked(axios.get).mockReset();
        vi.mocked(axios.get).mockResolvedValue({ data: SERVER_BASELINES });
    });

    it('shows baseline encodings and rates as editable values when the config is empty', async () => {
        render(<ElevenLabsProviderForm config={{}} onChange={vi.fn()} />);

        expect(controlBesideLabel('Asterisk Input Encoding')).toHaveValue('ulaw');
        expect(controlBesideLabel('ElevenLabs Output Encoding')).toHaveValue('pcm16');
        expect(controlBesideLabel('Output Sample Rate (Hz)')).toHaveValue(16000);
        expect(controlBesideLabel('Asterisk Output Encoding')).toHaveValue('ulaw');
        // G.711 target rate is locked to 8000.
        const targetRate = controlBesideLabel('Asterisk Output Sample Rate (Hz)');
        expect(targetRate).toHaveValue(8000);
        expect(targetRate).toBeDisabled();
        await waitFor(() =>
            expect(axios.get).toHaveBeenCalledWith('/api/config/providers/audio/baselines'),
        );
    });

    it('stores an off-baseline output rate and removes it again when set back to the default', async () => {
        const onChange = vi.fn();
        const { rerender } = render(<ElevenLabsProviderForm config={{}} onChange={onChange} />);

        fireEvent.change(controlBesideLabel('Output Sample Rate (Hz)'), {
            target: { value: '8000' },
        });
        expect(onChange).toHaveBeenLastCalledWith({ output_sample_rate_hz: 8000 });

        // With the override present, setting the baseline value back must
        // delete the key (undefined patch) instead of persisting 16000.
        rerender(
            <ElevenLabsProviderForm config={{ output_sample_rate_hz: 8000 }} onChange={onChange} />,
        );
        expect(screen.getByText(/Overrides default: 16000 Hz/)).toBeInTheDocument();
        fireEvent.change(controlBesideLabel('Output Sample Rate (Hz)'), {
            target: { value: '16000' },
        });
        expect(onChange).toHaveBeenLastCalledWith({ output_sample_rate_hz: undefined });
    });

    it('offers A-law and locks its paired rate to 8000', () => {
        const onChange = vi.fn();
        render(<ElevenLabsProviderForm config={{}} onChange={onChange} />);

        const targetEncoding = controlBesideLabel('Asterisk Output Encoding');
        expect(
            Array.from((targetEncoding as HTMLSelectElement).options).map((o) => o.value),
        ).toContain('alaw');

        fireEvent.change(targetEncoding, { target: { value: 'alaw' } });
        expect(onChange).toHaveBeenLastCalledWith({
            target_encoding: 'alaw',
            // 8000 equals the baseline rate, so the key is removed rather than stored.
            target_sample_rate_hz: undefined,
        });
    });

    it('prefers server baselines over the static fallback', async () => {
        vi.mocked(axios.get).mockResolvedValue({
            data: {
                provider_baselines: {
                    elevenlabs_agent: {
                        ...SERVER_BASELINES.provider_baselines.elevenlabs_agent,
                        output_sample_rate_hz: 22050,
                    },
                },
            },
        });

        render(<ElevenLabsProviderForm config={{}} onChange={vi.fn()} />);

        await waitFor(() =>
            expect(controlBesideLabel('Output Sample Rate (Hz)')).toHaveValue(22050),
        );
    });
});
