// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom/vitest';
import { useState } from 'react';
import { Modal } from './Modal';
import { ConfirmDialogProvider, useConfirmDialog } from '../../hooks/useConfirmDialog';

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

function FullscreenHarness({ initialOpen = false }: { initialOpen?: boolean }) {
    const [open, setOpen] = useState(initialOpen);
    return (
        <>
            <button onClick={() => setOpen(true)}>Open</button>
            <Modal
                isOpen={open}
                onClose={() => setOpen(false)}
                title="Edit Provider"
                allowFullscreen
            >
                <input aria-label="Name" />
                <button>Save</button>
            </Modal>
        </>
    );
}

function ConfirmingHarness() {
    const { confirm } = useConfirmDialog();
    const [busy, setBusy] = useState(false);
    return <Modal isOpen busy={busy} onClose={() => undefined} title="Editor" footer={
        <button onClick={async () => {
            setBusy(true);
            await confirm({ title: 'Confirm deletion', description: 'Synthetic confirmation', confirmText: 'Delete' });
            setBusy(false);
        }}>Delete item</button>
    }><input aria-label="Draft" /></Modal>;
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

    it('traps Shift+Tab from the dialog boundary (reverse direction)', async () => {
        const user = userEvent.setup();
        render(<Harness initialOpen />); // focus starts on the dialog container
        const dialog = screen.getByRole('dialog');
        await user.tab({ shift: true }); // Shift+Tab as the very first key
        expect(dialog.contains(document.activeElement)).toBe(true);
    });

    it('restores the previous body overflow on close (does not clobber it)', async () => {
        document.body.style.overflow = 'scroll';
        const user = userEvent.setup();
        render(<Harness />);
        await user.click(screen.getByRole('button', { name: 'Open' }));
        expect(document.body.style.overflow).toBe('hidden'); // locked while open
        await user.keyboard('{Escape}');
        expect(document.body.style.overflow).toBe('scroll'); // restored, not 'unset'
        document.body.style.overflow = '';
    });

    it('keeps focus away from disabled fieldsets while busy and restores dismissal afterward', async () => {
        const user = userEvent.setup();
        const close = vi.fn();
        const content = <input aria-label="Draft" />;
        const { rerender } = render(<Modal isOpen busy onClose={close} title="Saving" footer={<button>Save</button>}>{content}</Modal>);
        const dialog = screen.getByRole('dialog');
        await user.tab();
        expect(document.activeElement).toBe(dialog);
        await user.tab({ shift: true });
        expect(document.activeElement).toBe(dialog);
        await user.keyboard('{Escape}');
        await user.click(screen.getByRole('button', { name: 'Close' }));
        expect(close).not.toHaveBeenCalled();
        expect(screen.getByRole('textbox', { name: 'Draft' })).toBeDisabled();
        rerender(<Modal isOpen onClose={close} title="Saving" footer={<button>Save</button>}>{content}</Modal>);
        expect(screen.getByRole('textbox', { name: 'Draft' })).toBeEnabled();
        await user.keyboard('{Escape}');
        expect(close).toHaveBeenCalledTimes(1);
    });

    it('lets the confirmation dialog own Tab while the editor is busy', async () => {
        const user = userEvent.setup();
        render(<ConfirmDialogProvider><ConfirmingHarness /></ConfirmDialogProvider>);
        await user.click(screen.getByRole('button', { name: 'Delete item' }));
        const confirmation = screen.getByRole('alertdialog');
        await user.tab();
        expect(confirmation.contains(document.activeElement)).toBe(true);
        await user.tab({ shift: true });
        expect(confirmation.contains(document.activeElement)).toBe(true);
        await user.click(within(confirmation).getByRole('button', { name: 'Cancel' }));
        expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Delete item' })).toBeEnabled();
    });

    it('only shows the fullscreen toggle when enabled', () => {
        render(<Harness initialOpen />);
        expect(screen.queryByRole('button', { name: 'Enter fullscreen' })).not.toBeInTheDocument();
    });

    it('toggles fullscreen state through the user-facing button', async () => {
        const user = userEvent.setup();
        render(<FullscreenHarness initialOpen />);

        const enterFullscreen = screen.getByRole('button', { name: 'Enter fullscreen' });
        expect(enterFullscreen).toHaveAttribute('aria-pressed', 'false');

        await user.click(enterFullscreen);
        const exitFullscreen = screen.getByRole('button', { name: 'Exit fullscreen' });
        expect(exitFullscreen).toHaveAttribute('aria-pressed', 'true');

        await user.click(exitFullscreen);
        expect(screen.getByRole('button', { name: 'Enter fullscreen' })).toHaveAttribute(
            'aria-pressed',
            'false'
        );
    });

    it('resets fullscreen state after the modal closes', async () => {
        const user = userEvent.setup();
        render(<FullscreenHarness />);

        await user.click(screen.getByRole('button', { name: 'Open' }));
        await user.click(screen.getByRole('button', { name: 'Enter fullscreen' }));
        expect(screen.getByRole('button', { name: 'Exit fullscreen' })).toHaveAttribute(
            'aria-pressed',
            'true'
        );

        await user.click(screen.getByRole('button', { name: 'Close' }));
        await user.click(screen.getByRole('button', { name: 'Open' }));
        expect(screen.getByRole('button', { name: 'Enter fullscreen' })).toHaveAttribute(
            'aria-pressed',
            'false'
        );
    });
});
