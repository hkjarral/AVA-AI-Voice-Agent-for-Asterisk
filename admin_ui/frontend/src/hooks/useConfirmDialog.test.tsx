// @vitest-environment jsdom
import { expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { ConfirmDialogProvider, useConfirmDialog } from './useConfirmDialog';

it('requires an explicit decision and resolves cancellation only once', async () => {
    const resolved = vi.fn();
    const Launch = () => {
        const { confirm } = useConfirmDialog();
        return <button onClick={() => void confirm({ title: 'Delete item?', description: 'This removes the selected item.' }).then(resolved)}>Open</button>;
    };
    render(<ConfirmDialogProvider><Launch /></ConfirmDialogProvider>);
    fireEvent.click(screen.getByRole('button', { name: 'Open' }));
    const dialog = await screen.findByRole('alertdialog');
    fireEvent.keyDown(dialog, { key: 'Escape' });
    fireEvent.pointerDown(document.body);
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    expect(resolved).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(resolved).toHaveBeenCalledExactlyOnceWith(false));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
});
