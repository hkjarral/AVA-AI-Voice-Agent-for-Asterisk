// @vitest-environment jsdom

import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, expect, it, vi } from 'vitest';

import GoogleLiveProviderForm from './GoogleLiveProviderForm';
import DeepgramProviderForm from './DeepgramProviderForm';
import ElevenLabsProviderForm from './ElevenLabsProviderForm';
import OpenAIRealtimeProviderForm from './OpenAIRealtimeProviderForm';
import { enforceOpenAIRealtimeGaAudioContract } from '../../../utils/providerAudioContracts';

vi.mock('axios', () => ({ default: { get: vi.fn().mockResolvedValue({ data: {} }) } }));

vi.mock('../../../hooks/useConfirmDialog', () => ({
    useConfirmDialog: () => ({ confirm: vi.fn() }),
}));

const controlBesideLabel = (label: string): HTMLInputElement | HTMLSelectElement => {
    const labelNode = screen.getByText(label, { selector: 'label' });
    const container = labelNode.parentElement?.parentElement;
    const control = container?.querySelector('input, select');
    if (!(control instanceof HTMLInputElement) && !(control instanceof HTMLSelectElement)) {
        throw new Error(`No form control found for ${label}`);
    }
    return control;
};

describe('provider audio displayed defaults', () => {
    it('shows Deepgram wire input as μ-law at 8 kHz and omits unsupported provider-input controls', () => {
        render(<DeepgramProviderForm config={{}} onChange={vi.fn()} />);

        expect(controlBesideLabel('Input Encoding')).toHaveValue('mulaw');
        expect(controlBesideLabel('Input Sample Rate (Hz)')).toHaveValue(8000);
        expect(screen.queryByText('Provider Input Encoding', { selector: 'label' })).not.toBeInTheDocument();
        expect(screen.queryByText('Provider Input Sample Rate (Hz)', { selector: 'label' })).not.toBeInTheDocument();
    });

    it('distinguishes ElevenLabs Asterisk input from provider-native input', () => {
        render(<ElevenLabsProviderForm config={{}} onChange={vi.fn()} />);

        expect(controlBesideLabel('Asterisk Input Sample Rate (Hz)')).toHaveValue(8000);
        expect(controlBesideLabel('ElevenLabs Input Sample Rate (Hz)')).toHaveValue(16000);
    });

    it('shows the enforced OpenAI GA output contract instead of stale configured values', () => {
        render(
            <OpenAIRealtimeProviderForm
                config={{ api_version: 'ga', output_encoding: 'ulaw', output_sample_rate_hz: 8000 }}
                onChange={vi.fn()}
            />,
        );

        expect(controlBesideLabel('Output Encoding')).toHaveValue('linear16');
        expect(controlBesideLabel('Output Encoding')).toBeDisabled();
        expect(controlBesideLabel('Output Sample Rate (Hz)')).toHaveValue(24000);
        expect(controlBesideLabel('Output Sample Rate (Hz)')).toBeDisabled();
        expect(screen.getByText(/Fixed by the GA wire contract/i)).toBeInTheDocument();
    });

    it('replaces stale Beta output fields when switching to the GA wire contract', () => {
        const onChange = vi.fn();
        render(
            <OpenAIRealtimeProviderForm
                config={{
                    api_version: 'beta',
                    model: 'gpt-4o-realtime-preview',
                    output_encoding: 'mulaw',
                    output_sample_rate_hz: 8000,
                    voice: 'alloy',
                }}
                onChange={onChange}
            />,
        );

        fireEvent.change(controlBesideLabel('Realtime API Version'), {
            target: { value: 'ga' },
        });

        expect(onChange).toHaveBeenCalledWith({
            api_version: 'ga',
            model: 'gpt-realtime-2.1',
            output_encoding: 'linear16',
            output_sample_rate_hz: 24000,
            voice: 'alloy',
        });
    });

    it('enforces GA audio fields again at the provider save boundary', () => {
        expect(enforceOpenAIRealtimeGaAudioContract({
            api_version: 'ga',
            output_encoding: 'mulaw',
            output_sample_rate_hz: 8000,
            voice: 'alloy',
        })).toEqual({
            api_version: 'ga',
            output_encoding: 'linear16',
            output_sample_rate_hz: 24000,
            voice: 'alloy',
        });
    });

    it('preserves explicitly selected Beta audio fields at the provider save boundary', () => {
        const beta = {
            api_version: 'beta',
            output_encoding: 'mulaw',
            output_sample_rate_hz: 8000,
        };
        expect(enforceOpenAIRealtimeGaAudioContract(beta)).toBe(beta);
    });
});


describe('legacy farewell delay controls (issue #677)', () => {
    it.each([
        ['Google Live', GoogleLiveProviderForm],
        ['Deepgram', DeepgramProviderForm],
        ['ElevenLabs', ElevenLabsProviderForm],
        ['OpenAI Realtime', OpenAIRealtimeProviderForm],
    ])('omits the inactive control in %s and preserves legacy values on unrelated edits', (_name, Form) => {
        const onChange = vi.fn();
        const config = { farewell_hangup_delay_sec: 4 };
        render(<Form config={config} onChange={onChange} />);
        expect(screen.queryByText('Farewell Hangup Delay (seconds)')).not.toBeInTheDocument();
        const control = document.querySelector('input[type="text"]:not([disabled]), input[type="password"]:not([disabled])');
        expect(control).not.toBeNull();
        fireEvent.change(control!, { target: { value: 'test-value' } });
        expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ farewell_hangup_delay_sec: 4 }));
    });
});


describe('input gain noise floor (issue #853)', () => {
    it.each([
        ['Google Live', GoogleLiveProviderForm],
        ['OpenAI Realtime', OpenAIRealtimeProviderForm],
    ])('shows the default and preserves explicit zero and other settings in %s', (_name, Form) => {
        const onChange = vi.fn();
        const config = { input_gain_target_rms: 2500, input_gain_max_db: 12 };
        const { rerender } = render(<Form config={config} onChange={onChange} />);
        const control = screen.getByLabelText('Input Gain Noise Floor RMS');
        expect(control).toHaveValue(200);
        fireEvent.change(control, { target: { value: '350' } });
        expect(onChange).toHaveBeenLastCalledWith({ ...config, input_gain_noise_floor_rms: 350 });
        rerender(<Form config={{ ...config, input_gain_noise_floor_rms: 0 }} onChange={onChange} />);
        expect(screen.getByLabelText('Input Gain Noise Floor RMS')).toHaveValue(0);
        fireEvent.change(screen.getByLabelText('Input Gain Noise Floor RMS'), { target: { value: '' } });
        expect(onChange).toHaveBeenLastCalledWith({ ...config, input_gain_noise_floor_rms: 200 });
    });
});
