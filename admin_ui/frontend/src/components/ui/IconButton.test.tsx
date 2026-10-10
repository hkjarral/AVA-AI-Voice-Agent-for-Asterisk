// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom/vitest';
import { Copy, Play, Trash2 } from 'lucide-react';
import { describe, expect, it, vi } from 'vitest';

import { IconButton } from './IconButton';

describe('IconButton', () => {
    it('provides an accessible name, tooltip, and decorative icon', () => {
        render(<IconButton icon={Copy} label="Clone pipeline sales" />);

        const button = screen.getByRole('button', { name: 'Clone pipeline sales' });
        expect(button).toHaveAttribute('title', 'Clone pipeline sales');
        expect(button.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    });

    it('keeps disabled explanations separate from the accessible action name', async () => {
        const onClick = vi.fn();
        render(
            <IconButton
                icon={Trash2}
                label="Delete profile telephony"
                title="Cannot delete the last remaining audio profile"
                variant="destructive"
                disabled
                onClick={onClick}
            />,
        );

        const button = screen.getByRole('button', { name: 'Delete profile telephony' });
        expect(button).toBeDisabled();
        expect(button).toHaveAttribute('title', 'Cannot delete the last remaining audio profile');
        await userEvent.setup().click(button);
        expect(onClick).not.toHaveBeenCalled();
    });

    it('supports keyboard activation and retains native mouse events', async () => {
        const onClick = vi.fn();
        const onParentClick = vi.fn();
        render(
            <div onClick={onParentClick}>
                <IconButton icon={Trash2} label="Delete call" onClick={(event) => {
                    event.stopPropagation();
                    onClick(event.currentTarget.tagName);
                }} />
            </div>,
        );

        const user = userEvent.setup();
        await user.tab();
        expect(screen.getByRole('button', { name: 'Delete call' })).toHaveFocus();
        await user.keyboard('{Enter} ');
        expect(onClick).toHaveBeenCalledTimes(2);
        expect(onClick).toHaveBeenCalledWith('BUTTON');
        expect(onParentClick).not.toHaveBeenCalled();
    });

    it('does not submit a surrounding form unless explicitly requested', () => {
        const onSubmit = vi.fn((event) => event.preventDefault());
        const { rerender } = render(
            <form onSubmit={onSubmit}>
                <IconButton icon={Copy} label="Clone" />
            </form>,
        );

        fireEvent.click(screen.getByRole('button', { name: 'Clone' }));
        expect(onSubmit).not.toHaveBeenCalled();

        rerender(
            <form onSubmit={onSubmit}>
                <IconButton icon={Copy} label="Clone" type="submit" />
            </form>,
        );
        fireEvent.click(screen.getByRole('button', { name: 'Clone' }));
        expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    it('updates its name and tooltip when the action changes', () => {
        const { rerender } = render(<IconButton icon={Play} label="Play recording" />);
        rerender(<IconButton icon={Play} label="Pause recording" />);

        expect(screen.queryByRole('button', { name: 'Play recording' })).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Pause recording' })).toHaveAttribute('title', 'Pause recording');
    });

    it('allows existing padding, rounding, and icon dimensions to override defaults', () => {
        render(
            <IconButton icon={Copy} label="Copy" size="sm"
                className="p-3 rounded-full" iconClassName="w-5 h-5" />,
        );

        const button = screen.getByRole('button', { name: 'Copy' });
        expect(button).toHaveClass('p-3', 'rounded-full');
        expect(button).not.toHaveClass('p-1.5', 'rounded-md');
        expect(button.querySelector('svg')).toHaveClass('w-5', 'h-5');
        expect(button.querySelector('svg')).not.toHaveClass('w-4', 'h-4');
    });
});
