// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { FormInput, FormSelect, FormSwitch } from './FormComponents';

/**
 * Accessibility regression coverage (WCAG 1.3.1 / 3.3.2 / 4.1.2): the shared form
 * primitives previously rendered the label with `htmlFor={props.id}`, so when a
 * caller passed no `id` (the common case) the label and control were not
 * programmatically associated and screen readers announced an unnamed control.
 */
describe('FormComponents — label association & error semantics', () => {
    it('associates the visible label with the input when no id is provided', () => {
        render(<FormInput label="Energy Threshold" />);
        // getByLabelText only resolves when label htmlFor ↔ input id are linked
        expect(screen.getByLabelText('Energy Threshold').tagName).toBe('INPUT');
    });

    it('associates the visible label with the select when no id is provided', () => {
        render(<FormSelect label="VAD Mode" options={[{ value: 'auto', label: 'Auto' }]} />);
        expect(screen.getByLabelText('VAD Mode').tagName).toBe('SELECT');
    });

    it('associates the visible label with the switch when no id is provided', () => {
        render(<FormSwitch label="Enhanced VAD" />);
        expect(screen.getByLabelText('Enhanced VAD').tagName).toBe('INPUT');
    });

    it('marks an invalid input and links its error message', () => {
        render(<FormInput label="Port" error="Port is required" />);
        const input = screen.getByLabelText('Port');
        expect(input).toHaveAttribute('aria-invalid', 'true');
        expect(input).toHaveAccessibleDescription('Port is required');
    });

    it('respects a caller-provided id (does not override it)', () => {
        render(<FormInput id="custom-id" label="Custom" />);
        expect(screen.getByLabelText('Custom')).toHaveAttribute('id', 'custom-id');
    });

    it('falls back to aria-label when there is no visible label', () => {
        render(<FormInput aria-label="Search transcripts" />);
        expect(screen.getByLabelText('Search transcripts').tagName).toBe('INPUT');
    });
});
