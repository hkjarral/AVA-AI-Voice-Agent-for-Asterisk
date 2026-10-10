// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, expect, it } from 'vitest';

import { FullscreenPanel } from './FullscreenPanel';

describe('FullscreenPanel accessible controls', () => {
    it('keeps the button name and tooltip in sync through entry, exit, and Escape', () => {
        render(<FullscreenPanel title="Calls">Call list</FullscreenPanel>);

        const enter = screen.getByRole('button', { name: 'Fullscreen', exact: true });
        expect(enter).toHaveAttribute('title', 'Fullscreen');
        fireEvent.click(enter);

        const exit = screen.getByRole('button', { name: 'Exit fullscreen' });
        expect(exit).toHaveAttribute('title', 'Exit fullscreen');
        expect(document.body.style.overflow).toBe('hidden');
        fireEvent.click(exit);

        expect(document.body.style.overflow).not.toBe('hidden');
        fireEvent.click(screen.getByRole('button', { name: 'Fullscreen', exact: true }));
        fireEvent.keyDown(document, { key: 'Escape' });
        expect(screen.getByRole('button', { name: 'Fullscreen', exact: true })).toHaveAttribute('title', 'Fullscreen');
        expect(screen.queryByRole('button', { name: 'Exit fullscreen' })).not.toBeInTheDocument();
    });
});
