// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom/vitest';
import { useState } from 'react';
import { Modal } from './Modal';

/**
 * Accessibility coverage for the shared dialog (WCAG 2.4.3 focus order, 4.1.2
 * name/role/value): the modal had role=dialog + aria-modal but no accessible
 * name, did not move focus into the dialog on open, did not trap Tab (focus
 * could reach background controls), and did not restore focus on close.
 */
function Harness({ initialOpen = false }: { initialOpen?: boolean }) {
    const [open, setOpen] = useState(initialOpen);
    return (
        <>
            <button onClick={() => setOpen(true)}>Open</button>
            <Modal isOpen={open} onClose={() => setOpen(false)} title="Edit Provider">
                <input aria-label="Name" />
                <button>Save</button>
            </Modal>
        </>
    );
}

describe('Modal — dialog accessibility', () => {
    it('exposes the title as the dialog accessible name (aria-labelledby)', () => {
        render(<Harness initialOpen />);
        expect(screen.getByRole('dialog', { name: 'Edit Provider' })).toBeInTheDocument();
    });

    it('gives the close button an accessible name', () => {
        render(<Harness initialOpen />);
        expect(screen.getByRole('button', { name: /close/i })).toBeInTheDocument();
    });

    it('moves focus into the dialog when opened', async () => {
        const user = userEvent.setup();
        render(<Harness />);
        await user.click(screen.getByRole('button', { name: 'Open' }));
        const dialog = screen.getByRole('dialog');
        expect(dialog.contains(document.activeElement)).toBe(true);
    });

    it('traps Tab focus within the dialog', async () => {
        const user = userEvent.setup();
        render(<Harness initialOpen />);
        const dialog = screen.getByRole('dialog');
        const buttons = within(dialog).getAllByRole('button');
        buttons[buttons.length - 1].focus();
        await user.tab();
        expect(dialog.contains(document.activeElement)).toBe(true);
    });

    it('restores focus to the trigger when closed', async () => {
        const user = userEvent.setup();
        render(<Harness />);
        const trigger = screen.getByRole('button', { name: 'Open' });
        await user.click(trigger);
        await user.keyboard('{Escape}');
        expect(document.activeElement).toBe(trigger);
    });
});
