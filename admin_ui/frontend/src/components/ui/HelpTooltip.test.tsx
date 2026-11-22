// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import HelpTooltip from './HelpTooltip';

afterEach(() => vi.restoreAllMocks());

it('keeps the right edge inside the scrolling pane, including its scrollbar gutter', () => {
    const { container } = render(
        <div style={{ overflowX: 'auto' }}><HelpTooltip content="Keyterm instructions" /></div>
    );
    const pane = container.firstElementChild as HTMLElement;
    const trigger = screen.getByRole('button');
    vi.spyOn(pane, 'getBoundingClientRect').mockReturnValue(new DOMRect(300, 100, 460, 400));
    vi.spyOn(pane, 'clientWidth', 'get').mockReturnValue(440);
    vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue(new DOMRect(700, 250, 16, 16));
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(256);

    fireEvent.focus(trigger);

    const tooltip = screen.getByRole('tooltip');
    const right = 700 + parseFloat(tooltip.style.left) + tooltip.offsetWidth;
    expect(right).toBe(300 + pane.clientWidth - 8);
});
