// @vitest-environment jsdom

import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, expect, it, vi } from 'vitest';

import DeepgramProviderForm from './DeepgramProviderForm';
import ElevenLabsProviderForm from './ElevenLabsProviderForm';
import OpenAIRealtimeProviderForm from './OpenAIRealtimeProviderForm';
import { enforceOpenAIRealtimeGaAudioContract } from '../../../utils/providerAudioContracts';

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
            model: 'gpt-realtime',
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
