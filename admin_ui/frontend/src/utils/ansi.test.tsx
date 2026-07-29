// @vitest-environment jsdom
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, expect, it } from 'vitest';

import { parseAnsi } from './ansi';


describe('parseAnsi', () => {
    it('strips ANSI color codes while preserving the styled text', () => {
        render(<div>{parseAnsi(`plain ${String.fromCharCode(27)}[31mred${String.fromCharCode(27)}[0m`)}</div>);

        expect(screen.getByText(/plain/)).toBeInTheDocument();
        expect(screen.getByText('red')).toHaveStyle({ color: 'rgb(255, 0, 0)' });
        expect(document.body.textContent).not.toContain('[31m');
    });
});
