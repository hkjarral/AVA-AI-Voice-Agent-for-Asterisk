// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { SecretInput } from './SecretInput';

it('requires an explicit replacement and never puts a stored marker in an input', () => {
    const onChange = vi.fn();
    render(<SecretInput name="OPENAI_API_KEY" value={{ set: true }} onChange={onChange} />);
    expect(screen.getByText('Set')).toBeInTheDocument();
    expect(screen.queryByDisplayValue(/.+/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /replace/i }));
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('OPENAI_API_KEY'), { target: { value: 'replacement' } });
    expect(onChange).toHaveBeenCalledExactlyOnceWith('replacement');
});
