// @vitest-environment jsdom
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
