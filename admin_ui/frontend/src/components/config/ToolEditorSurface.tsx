import { ComponentProps, RefObject, useEffect, useRef } from 'react';
import { ArrowLeft } from 'lucide-react';
import { Modal } from '../ui/Modal';

// Use the same form and footer in either presentation. Embedded callers keep
// their existing modal; the Tools page gets a full-width, accessible editor.
export function ToolEditorSurface({ inline, fallbackFocusRef, testRequestAvailable = true, ...props }: ComponentProps<typeof Modal> & { inline?: boolean; fallbackFocusRef?: RefObject<HTMLElement | null>; testRequestAvailable?: boolean }) {
    const titleRef = useRef<HTMLHeadingElement>(null);
    useEffect(() => {
        if (!inline || !props.isOpen) return;
        const previouslyFocused = document.activeElement as HTMLElement | null;
        titleRef.current?.focus();
        return () => {
            // An updated tool may no longer match the active search filter.
            const target = previouslyFocused?.isConnected ? previouslyFocused : fallbackFocusRef?.current;
            target?.focus();
        };
    }, [inline, props.isOpen, fallbackFocusRef]);
    if (!inline) return <Modal {...props} />;
    if (!props.isOpen) return null;
    return (
        <section aria-label={props.title} className="space-y-6">
            <button type="button" onClick={props.onClose} className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
                <ArrowLeft className="h-4 w-4" /> Cancel and return to tools
            </button>
            <div>
                <h2 ref={titleRef} tabIndex={-1} className="text-2xl font-semibold tracking-tight">{props.title}</h2>
                <p className="text-sm text-muted-foreground mt-2">Update the tool to keep these edits, then Save &amp; Apply to publish them.{testRequestAvailable && ' Test sends a real HTTP request.'}</p>
            </div>
            {props.children}
            <div className="sticky bottom-0 z-10 flex flex-wrap justify-end gap-3 border-t border-border bg-background py-4">{props.footer}</div>
        </section>
    );
}
