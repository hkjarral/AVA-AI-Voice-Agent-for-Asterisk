import React, { useState, useRef, useLayoutEffect, useCallback } from 'react';
import { HelpCircle, ExternalLink } from 'lucide-react';

interface HelpTooltipProps {
    content: React.ReactNode;
    link?: string;
    linkText?: string;
    ariaLabel?: string;
}

// Intersect the viewport with each ancestor that clips on the relevant axis.
// clientWidth/clientHeight exclude borders and space reserved for scrollbars.
function visibleBounds(element: HTMLElement) {
    let left = 0;
    let top = 0;
    let right = document.documentElement.clientWidth || window.innerWidth;
    let bottom = document.documentElement.clientHeight || window.innerHeight;
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
        const { overflowX, overflowY } = getComputedStyle(parent);
        if (overflowX === 'visible' && overflowY === 'visible') continue;
        const rect = parent.getBoundingClientRect();
        if (overflowX && overflowX !== 'visible') {
            const x = rect.left + parent.clientLeft;
            left = Math.max(left, x);
            right = Math.min(right, x + parent.clientWidth);
        }
        if (overflowY && overflowY !== 'visible') {
            const y = rect.top + parent.clientTop;
            top = Math.max(top, y);
            bottom = Math.min(bottom, y + parent.clientHeight);
        }
    }
    return { left, top, right, bottom };
}

const HelpTooltip: React.FC<HelpTooltipProps> = ({
    content,
    link,
    linkText = 'Learn more',
    ariaLabel = 'Show help',
}) => {
    const tooltipId = React.useId();
    const [isOpen, setIsOpen] = useState(false);
    const [placement, setPlacement] = useState<'top' | 'bottom'>('top');
    const [horizontalOffset, setHorizontalOffset] = useState<number>(-112);
    const buttonRef = useRef<HTMLButtonElement | null>(null);
    const popoverRef = useRef<HTMLDivElement | null>(null);

    const recomputePlacement = useCallback(() => {
        const button = buttonRef.current;
        const popover = popoverRef.current;
        if (!button || !popover) return;

        const bounds = visibleBounds(button);
        const gap = 8;
        // Measure after constraining the width so wrapped text has its final height.
        popover.style.maxWidth = `${Math.max(0, bounds.right - bounds.left - 2 * gap)}px`;
        const rect = button.getBoundingClientRect();
        const width = popover.offsetWidth;
        const center = rect.left + rect.width / 2;
        const left = Math.max(bounds.left + gap, Math.min(center - width / 2, bounds.right - width - gap));
        setHorizontalOffset(left - rect.left);

        const above = rect.top - bounds.top;
        const below = bounds.bottom - rect.bottom;
        setPlacement(above < popover.offsetHeight + gap && below > above ? 'bottom' : 'top');
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
                aria-describedby={isOpen ? tooltipId : undefined}
                className="inline-flex items-center justify-center text-muted-foreground hover:text-foreground transition-colors"
                onFocus={() => setIsOpen(true)}
                onBlur={event => {
                    if (!event.currentTarget.parentElement?.contains(event.relatedTarget)) {
                        setIsOpen(false);
                    }
                }}
                onClick={e => {
                    e.preventDefault();
                    setIsOpen(true);
                }}
                onKeyDown={event => {
                    if (event.key === 'Escape' && isOpen) {
                        event.stopPropagation();
                        setIsOpen(false);
                    }
                }}
            >
                <HelpCircle className="w-4 h-4" />
            </button>

            {isOpen && (
                <div
                    id={tooltipId}
                    role="tooltip"
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
                        style={{ left: `calc(${-horizontalOffset}px + 0.25rem)` }}
                    />
                </div>
            )}
        </div>
    );
};

export default HelpTooltip;
