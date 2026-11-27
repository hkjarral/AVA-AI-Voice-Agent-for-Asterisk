// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import CompletionSettings, { type CompletionPolicy } from './CompletionSettings';
const destinations = {survey: {type:'dialplan',requires_opt_in:true}, menu:{type:'dialplan'}};
function Harness({change = vi.fn()}) {
    const [value,setValue] = useState<CompletionPolicy>({});
    return <CompletionSettings value={value} destinations={destinations} onChange={next => {setValue(next);change(next);}} />;
}
describe('conversation completion settings', () => {
    it('allows survey selection but never offers it as an automatic default', () => {
        const change=vi.fn();
        render(<Harness change={change} />);
        fireEvent.click(screen.getByLabelText(/survey · dialplan/));
        expect(change.mock.lastCall?.[0].destination_keys).toEqual(['survey']);
        expect(screen.queryByRole('option',{name:'Hand off to survey'})).not.toBeInTheDocument();
        fireEvent.click(screen.getByLabelText('menu · dialplan'));
        fireEvent.change(screen.getByLabelText('Default after conversation'),{target:{value:'menu'}});
        expect(change.mock.lastCall?.[0].default_next_step).toBe('menu');
        fireEvent.click(screen.getByLabelText('menu · dialplan'));
        expect(change.mock.lastCall?.[0].default_next_step).toBe('hangup');
    });
    it('respects the global transfer disable', () => {
        render(<CompletionSettings destinations={destinations} disabled onChange={vi.fn()} />);
        expect(screen.getByLabelText(/survey · dialplan/)).toBeDisabled();
        expect(screen.getByLabelText('Default after conversation')).toBeDisabled();
    });
});
