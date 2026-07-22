// @vitest-environment jsdom

import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, expect, it, vi } from 'vitest';

import OutputResamplerField from './OutputResamplerField';


describe('OutputResamplerField', () => {
    it('defaults to profile inheritance and explains a no-op path', () => {
        render(
            <OutputResamplerField
                sourceRate={8000}
                targetRate={8000}
                onChange={vi.fn()}
            />
        );

        expect(screen.getByRole('combobox')).toHaveValue('inherit');
        expect(screen.getByRole('option', { name: /inherit from audio profile/i })).toBeInTheDocument();
        expect(screen.getByText(/currently a no-op/i)).toBeInTheDocument();
    });

    it('selects the alias-safe policy for an active downsample path', () => {
        const onChange = vi.fn();
        render(
            <OutputResamplerField
                value="linear"
                sourceRate={24000}
                targetRate={8000}
                onChange={onChange}
            />
        );

        expect(screen.getByText(/24000 Hz → 8000 Hz/i)).toBeInTheDocument();
        fireEvent.change(screen.getByRole('combobox'), {
            target: { value: 'bandlimited' },
        });
        expect(onChange).toHaveBeenCalledWith('bandlimited');
    });
});
