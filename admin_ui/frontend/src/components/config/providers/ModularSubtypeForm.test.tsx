// @vitest-environment jsdom
import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { MODULAR_SUBTYPES } from '../../../config/modularProviderSubtypes';
import ModularSubtypeForm from './ModularSubtypeForm';

const fishAudio = MODULAR_SUBTYPES.tts.find(subtype => subtype.id === 'fishaudio')!;

describe('ModularSubtypeForm', () => {
    it('renders Fish Audio models as a closed dropdown', () => {
        const onChange = vi.fn();
        render(
            <ModularSubtypeForm
                subtype={fishAudio}
                config={{ model: 's2.1-pro-free' }}
                onChange={onChange}
            />
        );

        const model = screen.getByLabelText('Model *');
        expect(model.tagName).toBe('SELECT');
        expect(model).toHaveValue('s2.1-pro-free');
        expect(
            Array.from((model as HTMLSelectElement).options).map(option => option.textContent)
        ).toEqual([
            's2.1-pro',
            's2.1-pro-free',
            's2-pro',
            'drama-3',
            's3-pro',
            's1',
        ]);

        fireEvent.change(model, { target: { value: 's2-pro' } });
        expect(onChange).toHaveBeenCalledWith('model', 's2-pro');
    });

    it('preserves an existing unsupported model until the operator chooses a replacement', () => {
        render(
            <ModularSubtypeForm
                subtype={fishAudio}
                config={{ model: 'retired-model' }}
                onChange={vi.fn()}
            />
        );

        const model = screen.getByLabelText('Model *');
        expect(model).toHaveValue('retired-model');
        expect(
            screen.getByRole('option', { name: 'retired-model (unsupported current value)' })
        ).toBeDisabled();
    });
});

const google = MODULAR_SUBTYPES.llm.find(subtype => subtype.id === 'google')!;
const temperatureHelp = 'This model manages temperature automatically. Your saved value is preserved for models that support it.';

describe('Google temperature compatibility', () => {
    it.each([
        'gemini-3.6-flash',
        'gemini-3.5-flash',
        'models/gemini-3.5-flash-lite',
        'models/gemini-3.8-flash',
        'gemini-3.10-flash',
        'gemini-4',
        undefined,
    ])('disables temperature without changing its saved value for %s', model => {
        const onChange = vi.fn();
        render(<ModularSubtypeForm subtype={google} config={{ llm_model: model, temperature: 0 }} onChange={onChange} />);

        const temperature = screen.getByRole('spinbutton', { name: 'Temperature' });
        expect(temperature).toBeDisabled();
        expect(temperature).toHaveValue(0);
        expect(temperature).toHaveAccessibleDescription(temperatureHelp);
        expect(screen.getByText(temperatureHelp)).toBeVisible();
        expect(onChange).not.toHaveBeenCalled();
    });

    it.each(['gemini-2.5-flash', 'gemini-3.1-flash-lite', 'gemini-3-flash', 'custom-model', ''])('allows editing temperature for %s', model => {
        const onChange = vi.fn();
        render(<ModularSubtypeForm subtype={google} config={{ llm_model: model, temperature: 0.4 }} onChange={onChange} />);
        const temperature = screen.getByRole('spinbutton', { name: 'Temperature' });
        expect(temperature).toBeEnabled();
        expect(screen.queryByText(temperatureHelp)).not.toBeInTheDocument();
        fireEvent.change(temperature, { target: { value: '0.2' } });
        expect(onChange).toHaveBeenCalledWith('temperature', 0.2);
    });

    it('retains the selected temperature while switching newer and older models', () => {
        const Form = () => {
            const [config, setConfig] = useState({ llm_model: 'gemini-2.5-flash', temperature: 0.4 });
            return <ModularSubtypeForm subtype={google} config={config} onChange={(key, value) => setConfig(previous => ({ ...previous, [key]: value }))} />;
        };
        render(<Form />);
        const model = screen.getByPlaceholderText('e.g., gemini-3.8-flash');
        const temperature = screen.getByRole('spinbutton', { name: 'Temperature' });
        fireEvent.change(temperature, { target: { value: '0.25' } });
        fireEvent.change(model, { target: { value: 'gemini-3.8-flash' } });
        expect(temperature).toBeDisabled();
        expect(temperature).toHaveValue(0.25);
        fireEvent.change(model, { target: { value: 'gemini-3.5-flash-lite' } });
        expect(temperature).toBeDisabled();
        expect(temperature).toHaveValue(0.25);
        fireEvent.change(model, { target: { value: 'gemini-2.5-flash' } });
        expect(temperature).toBeEnabled();
        expect(temperature).toHaveValue(0.25);
        expect(screen.queryByText(temperatureHelp)).not.toBeInTheDocument();
    });

    it('leaves other providers editable even with a Gemini model name', () => {
        const openai = MODULAR_SUBTYPES.llm.find(subtype => subtype.id === 'openai')!;
        render(<ModularSubtypeForm subtype={openai} config={{ chat_model: 'gemini-3.8-flash', llm_model: 'gemini-3.8-flash', temperature: 0.4 }} onChange={vi.fn()} />);
        expect(screen.getByRole('spinbutton', { name: 'Temperature' })).toBeEnabled();
        expect(screen.queryByText(temperatureHelp)).not.toBeInTheDocument();
    });
});

describe('model-aware speech options', () => {
    it('keeps a custom Speech endpoint on its runtime legacy model and matching voices when the model is blank', () => {
        const openai = MODULAR_SUBTYPES.tts.find(subtype => subtype.id === 'openai')!;
        const onChange = vi.fn();
        render(<ModularSubtypeForm subtype={openai} config={{ tts_base_url: 'https://speech-gateway.example/v1/audio/speech', tts_model: '', voice: 'fable' }} onChange={onChange} />);
        expect(screen.getByDisplayValue('tts-1')).toBeInTheDocument();
        expect(screen.getByDisplayValue('fable')).toBeInTheDocument();
        expect(screen.queryByText(/configured voice is incompatible/)).not.toBeInTheDocument();
        expect(onChange).not.toHaveBeenCalled();
    });
    it('preserves an incompatible configured OpenAI voice and asks the operator to fix it', () => {
        const openai = MODULAR_SUBTYPES.tts.find(subtype => subtype.id === 'openai')!;
        const onChange = vi.fn();
        render(<ModularSubtypeForm subtype={openai} config={{ tts_model: 'tts-1', voice: 'ballad' }} onChange={onChange} />);
        expect(screen.getByDisplayValue('ballad')).toBeInTheDocument();
        expect(screen.getByRole('alert')).toHaveTextContent('incompatible with this model');
        expect(onChange).not.toHaveBeenCalled();
    });

    it('offers the version-specific Flux model list without rewriting the saved Aura voice', () => {
        const deepgram = MODULAR_SUBTYPES.tts.find(subtype => subtype.id === 'deepgram')!;
        const onChange = vi.fn();
        render(<ModularSubtypeForm subtype={deepgram} config={{ tts_version: 'v2', tts_model: 'aura-2-thalia-en' }} onChange={onChange} />);
        expect(screen.getByDisplayValue('aura-2-thalia-en')).toBeInTheDocument();
        expect(screen.getByRole('spinbutton', { name: 'Flux Expressivity' })).toBeInTheDocument();
        expect(onChange).not.toHaveBeenCalled();
    });

    it('keeps typed separators until committing multilingual Flux hints', async () => {
        const flux = MODULAR_SUBTYPES.stt.find(subtype => subtype.id === 'deepgram_flux')!;
        const onChange = vi.fn();
        const Form = () => {
            const [config, setConfig] = useState({ language_hints: [] as string[] });
            return <ModularSubtypeForm subtype={flux} config={config} onChange={(key, value) => {
                onChange(key, value);
                setConfig(previous => ({ ...previous, [key]: value }));
            }} />;
        };
        render(<Form />);
        const user = userEvent.setup();
        const hints = screen.getByRole('textbox', { name: 'Language Hints' });
        await user.type(hints, 'en, fr');
        expect(hints).toHaveValue('en, fr');
        expect(onChange).not.toHaveBeenCalled();
        await user.tab();
        expect(onChange).toHaveBeenCalledWith('language_hints', ['en', 'fr']);
    });

    it('clears Flux-only expressivity on an explicit switch to Aura', () => {
        const deepgram = MODULAR_SUBTYPES.tts.find(subtype => subtype.id === 'deepgram')!;
        const onChange = vi.fn();
        render(<ModularSubtypeForm subtype={deepgram} config={{ tts_version: 'v2', tts_model: 'flux-kit-en', tts_expressivity: 0 }} onChange={onChange} />);
        expect(onChange).not.toHaveBeenCalled();
        fireEvent.change(screen.getByLabelText('TTS API Version'), { target: { value: 'v1' } });
        expect(onChange).toHaveBeenCalledWith('tts_version', 'v1', { tts_expressivity: null });
    });
});

describe('current model-specific controls', () => {
    it('selects a matching Arabic voice only when the operator changes the Groq model', () => {
        const groq = MODULAR_SUBTYPES.tts.find(subtype => subtype.id === 'groq')!;
        const onChange = vi.fn();
        render(<ModularSubtypeForm subtype={groq} config={{ tts_model: 'canopylabs/orpheus-v1-english', voice: 'hannah' }} onChange={onChange} />);
        expect(onChange).not.toHaveBeenCalled();
        fireEvent.change(screen.getByPlaceholderText('e.g., canopylabs/orpheus-v1-english'), { target: { value: 'canopylabs/orpheus-arabic-saudi' } });
        expect(onChange).toHaveBeenCalledWith('tts_model', 'canopylabs/orpheus-arabic-saudi', { voice: 'abdullah' });
    });
    it('preserves an invalid Eleven v3 stability and offers only its supported replacements', () => {
        const eleven = MODULAR_SUBTYPES.tts.find(subtype => subtype.id === 'elevenlabs')!;
        const onChange = vi.fn();
        render(<ModularSubtypeForm subtype={eleven} config={{ model_id: 'eleven_v3', stability: 0.3 }} onChange={onChange} />);
        expect(screen.getByRole('combobox', { name: 'Stability' })).toHaveValue('0.3');
        expect(screen.getByRole('alert')).toHaveTextContent('must be 0, 0.5, or 1');
        expect(screen.queryByRole('spinbutton', { name: 'Similarity' })).not.toBeInTheDocument();
        expect(onChange).not.toHaveBeenCalled();
    });
    it('clears incompatible none reasoning only when selecting GPT-6.2', () => {
        const openai = MODULAR_SUBTYPES.llm.find(subtype => subtype.id === 'openai')!;
        const onChange = vi.fn();
        render(<ModularSubtypeForm subtype={openai} config={{ chat_model: 'gpt-6-luna', reasoning_effort: 'none' }} onChange={onChange} />);
        fireEvent.change(screen.getByPlaceholderText('mlx-community/Qwen3-8B-4bit'), { target: { value: 'gpt-6.2-sol' } });
        expect(onChange).toHaveBeenCalledWith('chat_model', 'gpt-6.2-sol', { reasoning_effort: null });
    });
});
