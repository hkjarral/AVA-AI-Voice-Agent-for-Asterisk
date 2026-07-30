// @vitest-environment jsdom

import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, expect, it, vi } from 'vitest';

import DeepgramProviderForm from './DeepgramProviderForm';

vi.mock('../../../hooks/useConfirmDialog', () => ({
    useConfirmDialog: () => ({ confirm: vi.fn() }),
}));

const selectBesideLabel = (label: string): HTMLSelectElement => {
    const labelNode = screen.getByText(label, { selector: 'label' });
    const select = labelNode.parentElement?.parentElement?.querySelector('select');
    if (!(select instanceof HTMLSelectElement)) throw new Error(`No select found for ${label}`);
    return select;
};

describe('Deepgram Voice Agent supported language surface', () => {
    it('surfaces the narrow telephony model set and omits non-streaming Whisper', () => {
        render(<DeepgramProviderForm config={{}} onChange={vi.fn()} />);

        const model = selectBesideLabel('STT Model');
        expect(model).toHaveValue('nova-3');
        expect(model.querySelector('option[value="flux-general-en"]')).toBeInTheDocument();
        expect(model.querySelector('option[value="flux-general-multi"]')).toBeInTheDocument();
        expect(model.querySelector('option[value="nova-3"]')).toBeInTheDocument();
        expect(model.querySelector('option[value="nova-2-phonecall"]')).toBeInTheDocument();
        expect(model.querySelector('option[value="whisper-cloud"]')).not.toBeInTheDocument();
        expect(model.querySelector('option[value="nova-2-medical"]')).not.toBeInTheDocument();
    });

    it('preserves an existing custom model without adding it to the recommended catalog', () => {
        render(
            <DeepgramProviderForm config={{ model: 'customer-private-model' }} onChange={vi.fn()} />
        );

        expect(selectBesideLabel('STT Model')).toHaveValue('customer-private-model');
        expect(
            screen.getByText(/outside the verified AAVA Voice Agent surface/i)
        ).toBeInTheDocument();
    });

    it('offers only the seven end-to-end Aura languages for new selections', () => {
        render(<DeepgramProviderForm config={{}} onChange={vi.fn()} />);

        const language = selectBesideLabel('Agent Language');
        const values = Array.from(language.options).map(option => option.value);
        expect(values).toEqual(['en', 'es', 'de', 'fr', 'it', 'nl', 'ja']);
        expect(language.querySelector('option[value="pt"]')).not.toBeInTheDocument();
        expect(language.querySelector('option[value="zh"]')).not.toBeInTheDocument();
    });

    it('preserves a legacy locale value for review', () => {
        render(
            <DeepgramProviderForm
                config={{ agent_language: 'en-US', tts_model: 'aura-2-thalia-en' }}
                onChange={vi.fn()}
            />
        );

        expect(selectBesideLabel('Agent Language')).toHaveValue('en-US');
        expect(
            screen.queryByText(/not supported by the AAVA Deepgram Voice Agent surface/i)
        ).not.toBeInTheDocument();
    });

    it('disables English-only listen models for a Spanish agent', () => {
        render(
            <DeepgramProviderForm
                config={{ agent_language: 'es', tts_model: 'aura-2-celeste-es' }}
                onChange={vi.fn()}
            />
        );

        const model = selectBesideLabel('STT Model');
        expect(model.querySelector('option[value="flux-general-en"]')).toBeDisabled();
        expect(model.querySelector('option[value="nova-2-phonecall"]')).toBeDisabled();
        expect(model.querySelector('option[value="flux-general-multi"]')).not.toBeDisabled();
        expect(model.querySelector('option[value="nova-3"]')).not.toBeDisabled();
    });

    it('filters Aura choices to the selected language', () => {
        render(
            <DeepgramProviderForm
                config={{ agent_language: 'es', tts_model: 'aura-2-celeste-es' }}
                onChange={vi.fn()}
            />
        );

        const voice = selectBesideLabel('Default Voice Model');
        const spanishGroup = voice
            .querySelector('option[value="aura-2-celeste-es"]')
            ?.closest('optgroup');
        const englishGroup = voice
            .querySelector('option[value="aura-2-thalia-en"]')
            ?.closest('optgroup');
        expect(spanishGroup).not.toHaveAttribute('hidden');
        expect(spanishGroup).not.toBeDisabled();
        expect(englishGroup).toHaveAttribute('hidden');
        expect(englishGroup).toBeDisabled();
    });

    it('shows an actionable error instead of silently replacing a mismatched voice', () => {
        const onChange = vi.fn();
        render(
            <DeepgramProviderForm
                config={{ agent_language: 'es', tts_model: 'aura-2-thalia-en' }}
                onChange={onChange}
            />
        );

        expect(screen.getByText(/AAVA will not silently replace it/i)).toBeInTheDocument();
        expect(onChange).not.toHaveBeenCalled();
    });

    it('keeps a known cross-language voice visible and enabled for preservation', () => {
        render(
            <DeepgramProviderForm
                config={{ agent_language: 'es', tts_model: 'aura-2-thalia-en' }}
                onChange={vi.fn()}
            />
        );

        const voice = selectBesideLabel('Default Voice Model');
        expect(voice).toHaveValue('aura-2-thalia-en');
        const preservationOption = screen.getByRole('option', {
            name: /Current configured value — aura-2-thalia-en/i,
        });
        expect(preservationOption).not.toHaveAttribute('hidden');
        expect(preservationOption).not.toBeDisabled();
        expect(preservationOption.closest('optgroup')).toBeNull();
        expect(voice.selectedOptions[0]).toBe(preservationOption);
    });

    it('keeps an unknown configured voice visible and enabled for preservation', () => {
        render(
            <DeepgramProviderForm
                config={{ agent_language: 'en', tts_model: 'aura-future-en' }}
                onChange={vi.fn()}
            />
        );

        const voice = selectBesideLabel('Default Voice Model');
        expect(voice).toHaveValue('aura-future-en');
        const preservationOption = screen.getByRole('option', {
            name: /Current configured value — aura-future-en/i,
        });
        expect(preservationOption).not.toHaveAttribute('hidden');
        expect(preservationOption).not.toBeDisabled();
        expect(preservationOption.closest('optgroup')).toBeNull();
        expect(voice.selectedOptions[0]).toBe(preservationOption);
        expect(screen.getByText(/AAVA will not silently replace it/i)).toBeInTheDocument();
    });

    it('keeps the unset English fallback visible and enabled for a Spanish agent', () => {
        render(<DeepgramProviderForm config={{ agent_language: 'es' }} onChange={vi.fn()} />);

        const voice = selectBesideLabel('Default Voice Model');
        expect(voice).toHaveValue('aura-asteria-en');
        const preservationOption = screen.getByRole('option', {
            name: /Current effective fallback — aura-asteria-en/i,
        });
        expect(preservationOption).not.toHaveAttribute('hidden');
        expect(preservationOption).not.toBeDisabled();
        expect(preservationOption.closest('optgroup')).toBeNull();
        expect(voice.selectedOptions[0]).toBe(preservationOption);
        expect(screen.getByText(/AAVA will not silently replace it/i)).toBeInTheDocument();
    });

    it('keeps modular stt_language untouched when changing Voice Agent language', () => {
        const onChange = vi.fn();
        render(
            <DeepgramProviderForm
                config={{
                    stt_language: 'en-US',
                    agent_language: 'en',
                    tts_model: 'aura-2-thalia-en',
                }}
                onChange={onChange}
            />
        );

        fireEvent.change(selectBesideLabel('Agent Language'), { target: { value: 'fr' } });
        expect(onChange).toHaveBeenCalledWith(
            expect.objectContaining({
                stt_language: 'en-US',
                agent_language: 'fr',
                tts_model: 'aura-2-thalia-en',
            })
        );
    });
});
