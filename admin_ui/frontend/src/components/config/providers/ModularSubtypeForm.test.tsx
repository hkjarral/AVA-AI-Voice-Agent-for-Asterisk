// @vitest-environment jsdom
import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
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
            's1',
            'drama-3-preview',
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
