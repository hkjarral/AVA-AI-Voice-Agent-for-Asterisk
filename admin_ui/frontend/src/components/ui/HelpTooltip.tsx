import React, { useState, useRef, useLayoutEffect, useCallback } from 'react';
import { HelpCircle, ExternalLink } from 'lucide-react';

interface HelpTooltipProps {
    content: React.ReactNode;
    link?: string;
    linkText?: string;
    ariaLabel?: string;
}

/**
 * Click-or-hover help tooltip with viewport-aware positioning.
 *
 * Defaults to opening ABOVE the trigger. If the popover would be clipped
 * by the top of the viewport (e.g. trigger near the top of a scrolled
 * modal), it flips BELOW. Horizontal alignment also nudges left/right to
 * keep the popover within the visible width.
 *
 * Previous version was always `bottom-full -left-28` which silently
 * truncated tooltips opened from trigger icons near the top of any
 * scrolled container (the Provider edit modal was the canonical bug).
 */
const HelpTooltip: React.FC<HelpTooltipProps> = ({
    content,
    link,
    linkText = 'Learn more',
    ariaLabel = 'Show help',
}) => {
    const [isOpen, setIsOpen] = useState(false);
    const [placement, setPlacement] = useState<'top' | 'bottom'>('top');
    const [horizontalOffset, setHorizontalOffset] = useState<number>(-112);
    const buttonRef = useRef<HTMLButtonElement | null>(null);
    const popoverRef = useRef<HTMLDivElement | null>(null);

    // The popover is 256px (w-64) wide + 24px padding (p-3). Use these to
    // compute available space without re-reading the popover DOM.
    const POPOVER_WIDTH = 256;
    const POPOVER_GAP = 8; // matches mb-2 / mt-2

    const recomputePlacement = useCallback(() => {
        const btn = buttonRef.current;
        const popover = popoverRef.current;
        if (!btn) return;

        const rect = btn.getBoundingClientRect();
        const viewportH = window.innerHeight;
        const viewportW = window.innerWidth;

        // Measured popover height if we have it; otherwise assume 200px (a
        // reasonable cap for our bulleted-list tooltips).
        const popoverH = popover?.offsetHeight ?? 200;

        // Prefer top; flip to bottom if the popover would be clipped above
        // AND there's more room below.
        const spaceAbove = rect.top;
        const spaceBelow = viewportH - rect.bottom;
        const needsFlip = spaceAbove < popoverH + POPOVER_GAP && spaceBelow > spaceAbove;
        setPlacement(needsFlip ? 'bottom' : 'top');

        // Horizontal: nudge so the popover stays on-screen. Default offset
        // aims to roughly center under the icon (-112px = -7rem ≈ half of
        // 256-32 to compensate for the icon width).
        const buttonCenterX = rect.left + rect.width / 2;
        const idealLeft = buttonCenterX - POPOVER_WIDTH / 2;
        const clampedLeft = Math.max(
            8, // 8px margin from viewport edge
            Math.min(idealLeft, viewportW - POPOVER_WIDTH - 8)
        );
        // Convert to an offset relative to the button (which is `position:
        // relative` parent via .relative on the wrapper).
        setHorizontalOffset(clampedLeft - rect.left);
    }, []);

    // Recompute every time the popover opens; also when window resizes /
    // any ancestor scrolls (the modal). useLayoutEffect so we measure
    // before paint and avoid a visible jump.
    useLayoutEffect(() => {
        if (!isOpen) return;
        recomputePlacement();
        const handler = () => recomputePlacement();
        window.addEventListener('resize', handler);
        window.addEventListener('scroll', handler, true); // capture = catch ancestor scrolls
        return () => {
            window.removeEventListener('resize', handler);
            window.removeEventListener('scroll', handler, true);
        };
    }, [isOpen, recomputePlacement]);

    const popoverPositionClasses = placement === 'top' ? 'mb-2 bottom-full' : 'mt-2 top-full';
    // Arrow points toward the trigger: arrow on the BOTTOM of the popover
    // when popover sits ABOVE the trigger, and vice versa.
    const arrowPositionClasses =
        placement === 'top' ? '-bottom-1 border-r border-b' : '-top-1 border-l border-t';

    return (
        // Hover handlers on the wrapper so the popover stays open while
        // the cursor traverses from the icon to the popover content
        // (links / Learn-more anchor were previously unreachable because
        // onMouseLeave on the button alone closed the popover the moment
        // the pointer crossed the gap). CodeRabbit major on PR #396.
        <div
            className="relative inline-block"
            onMouseEnter={() => setIsOpen(true)}
            onMouseLeave={() => setIsOpen(false)}
        >
            <button
                ref={buttonRef}
                type="button"
                aria-label={ariaLabel}
                aria-expanded={isOpen}
                className="inline-flex items-center justify-center text-muted-foreground hover:text-foreground transition-colors"
                onClick={e => {
                    e.preventDefault();
                    setIsOpen(prev => !prev);
                }}
            >
                <HelpCircle className="w-4 h-4" />
            </button>

            {isOpen && (
                <div
                    ref={popoverRef}
                    className={`absolute z-50 w-64 p-3 text-sm bg-popover border border-border rounded-md shadow-lg ${popoverPositionClasses}`}
                    style={{ left: `${horizontalOffset}px` }}
                >
                    <div className="text-foreground mb-2">{content}</div>
                    {link && (
                        <a
                            href={link}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center text-xs text-primary hover:underline"
                        >
                            {linkText}
                            <ExternalLink className="w-3 h-3 ml-1" />
                        </a>
                    )}
                    <div
                        className={`absolute w-2 h-2 bg-popover transform rotate-45 ${arrowPositionClasses}`}
                        style={{ left: `${-horizontalOffset - 4}px` }}
                    />
                </div>
            )}
        </div>
    );
};

export default HelpTooltip;
