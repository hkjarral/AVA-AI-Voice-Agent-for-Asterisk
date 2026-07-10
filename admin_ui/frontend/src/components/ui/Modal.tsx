import React, { useEffect, useId, useRef, useState } from 'react';
import { Maximize2, Minimize2, X } from 'lucide-react';
import { createPortal } from 'react-dom';

interface ModalProps {
    isOpen: boolean;
    onClose: () => void;
    title: string;
    children: React.ReactNode;
    footer?: React.ReactNode;
    size?: 'sm' | 'md' | 'lg' | 'xl' | 'full';
    allowFullscreen?: boolean;
}

const FOCUSABLE_SELECTOR =
    'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export const Modal = ({
    isOpen,
    onClose,
    title,
    children,
    footer,
    size = 'md',
    allowFullscreen = false,
}: ModalProps) => {
    const modalRef = useRef<HTMLDivElement>(null);
    const titleId = useId();
    const [isFullscreen, setIsFullscreen] = useState(false);
    // Keep the latest onClose without re-running the focus effect on every render.
    const onCloseRef = useRef(onClose);
    onCloseRef.current = onClose;

    useEffect(() => {
        if (!isOpen) return;
        // Remember what was focused so we can restore it on close (WCAG 2.4.3).
        const previouslyFocused = document.activeElement as HTMLElement | null;

        const handleKeyDown = (e: KeyboardEvent) => {
            if (e.key === 'Escape') {
                onCloseRef.current();
                return;
            }
            if (e.key !== 'Tab') return;
            const dialog = modalRef.current;
            if (!dialog) return;
            const focusables = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
            if (focusables.length === 0) {
                e.preventDefault();
                dialog.focus();
                return;
            }
            const first = focusables[0];
            const last = focusables[focusables.length - 1];
            const active = document.activeElement;
            if (e.shiftKey) {
                // `active === dialog` covers the open state where focus is on the
                // container itself: Shift+Tab must wrap to the last control, not escape.
                if (active === first || active === dialog || !dialog.contains(active)) {
                    e.preventDefault();
                    last.focus();
                }
            } else if (active === last || !dialog.contains(active)) {
                e.preventDefault();
                first.focus();
            }
        };

        document.addEventListener('keydown', handleKeyDown);
        const previousBodyOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';

        // Move focus into the dialog so keyboard/SR users are taken there and the
        // accessible name (title) is announced.
        modalRef.current?.focus();

        return () => {
            document.removeEventListener('keydown', handleKeyDown);
            document.body.style.overflow = previousBodyOverflow;
            previouslyFocused?.focus?.();
        };
    }, [isOpen]);

    useEffect(() => {
        if (!isOpen) {
            setIsFullscreen(false);
        }
    }, [isOpen]);

    if (!isOpen) return null;

    const sizeClasses = {
        sm: 'max-w-md',
        md: 'max-w-lg',
        lg: 'max-w-2xl',
        xl: 'max-w-4xl',
        full: 'max-w-[96vw] h-[92vh]',
    };
    const effectiveSize = allowFullscreen && isFullscreen ? 'full' : size;

    return createPortal(
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-in fade-in duration-200">
            <div
                ref={modalRef}
                tabIndex={-1}
                className={`bg-card border border-border rounded-lg shadow-lg w-full flex flex-col max-h-[92vh] animate-in zoom-in-95 duration-200 focus:outline-none ${sizeClasses[effectiveSize]}`}
                role="dialog"
                aria-modal="true"
                aria-labelledby={titleId}
            >
                <div className="flex items-center justify-between p-6 border-b border-border">
                    <h2 id={titleId} className="text-lg font-semibold tracking-tight">
                        {title}
                    </h2>
                    <div className="flex items-center gap-2">
                        {allowFullscreen && (
                            <button
                                type="button"
                                onClick={() => setIsFullscreen(value => !value)}
                                aria-label={isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
                                aria-pressed={isFullscreen}
                                title={isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
                                className="p-1 rounded-md hover:bg-accent text-muted-foreground hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-ring"
                            >
                                {isFullscreen ? (
                                    <Minimize2 className="w-5 h-5" />
                                ) : (
                                    <Maximize2 className="w-5 h-5" />
                                )}
                            </button>
                        )}
                        <button
                            type="button"
                            onClick={onClose}
                            aria-label="Close"
                            className="p-1 rounded-md hover:bg-accent text-muted-foreground hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-ring"
                        >
                            <X className="w-5 h-5" />
                        </button>
                    </div>
                </div>

                <div className="flex-1 overflow-y-auto p-6">{children}</div>

                {footer && (
                    <div className="p-6 border-t border-border bg-muted/10 flex justify-end gap-3">
                        {footer}
                    </div>
                )}
            </div>
        </div>,
        document.body
    );
};
