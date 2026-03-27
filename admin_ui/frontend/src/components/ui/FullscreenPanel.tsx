import React, { useState, useEffect, useCallback } from 'react';
import { Maximize2, Minimize2 } from 'lucide-react';
import { createPortal } from 'react-dom';

// Shared ref-counted body scroll lock so multiple overlays compose correctly.
let scrollLockCount = 0;
let savedOverflow = '';

function acquireScrollLock() {
    if (scrollLockCount === 0) {
        savedOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
    }
    scrollLockCount++;
}

function releaseScrollLock() {
    scrollLockCount = Math.max(0, scrollLockCount - 1);
    if (scrollLockCount === 0) {
        document.body.style.overflow = savedOverflow;
    }
}

interface FullscreenPanelProps {
    title?: string;
    titleNode?: React.ReactNode;
    headerRight?: React.ReactNode;
    children: React.ReactNode;
    className?: string;
}

export const FullscreenPanel = ({ title, titleNode, headerRight, children, className = '' }: FullscreenPanelProps) => {
    const [isFullscreen, setIsFullscreen] = useState(false);

    const exitFullscreen = useCallback(() => setIsFullscreen(false), []);

    useEffect(() => {
        if (!isFullscreen) return;

        const handleEscape = (e: KeyboardEvent) => {
            if (e.key === 'Escape') exitFullscreen();
        };

        acquireScrollLock();
        document.addEventListener('keydown', handleEscape);

        return () => {
            document.removeEventListener('keydown', handleEscape);
            releaseScrollLock();
        };
    }, [isFullscreen, exitFullscreen]);

    const headerContent = (
        <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-muted/30">
            <div className="flex items-center gap-2 min-w-0">
                {titleNode ?? (title && <span className="text-sm font-medium truncate">{title}</span>)}
            </div>
            <div className="flex items-center gap-2 shrink-0">
                {headerRight}
                <button
                    onClick={() => setIsFullscreen(!isFullscreen)}
                    className="p-1.5 rounded-md hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"
                    title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
                >
                    {isFullscreen ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
                </button>
            </div>
        </div>
    );

    if (isFullscreen) {
        return createPortal(
            <div className="fixed inset-0 z-40 bg-background flex flex-col animate-in fade-in duration-200">
                {headerContent}
                <div className="flex-1 overflow-y-auto p-6">
                    {children}
                </div>
            </div>,
            document.body
        );
    }

    return (
        <div className={`rounded-lg border border-border bg-card overflow-hidden ${className}`}>
            {headerContent}
            <div className="p-4">
                {children}
            </div>
        </div>
    );
};
