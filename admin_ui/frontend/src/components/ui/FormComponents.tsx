import React, { useEffect, useMemo, useRef, useState } from 'react';
import { HelpCircle } from 'lucide-react';
import { createPortal } from 'react-dom';

type TooltipPlacement = 'top' | 'bottom';

type TooltipPosition = {
    left: number;
    top: number;
    placement: TooltipPlacement;
    maxWidth: number;
};

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

const Tooltip = ({ anchorEl, text, open }: { anchorEl: HTMLElement | null; text: string; open: boolean }) => {
    const [pos, setPos] = useState<TooltipPosition | null>(null);

    const compute = () => {
        if (!open || !anchorEl) return;
        const rect = anchorEl.getBoundingClientRect();
        const viewportW = window.innerWidth || 1024;
        const viewportH = window.innerHeight || 768;

        const maxWidth = clamp(Math.floor(viewportW * 0.6), 220, 360);

        // Prefer top placement, flip to bottom if not enough room.
        const preferTop = rect.top > 56;
        const placement: TooltipPlacement = preferTop ? 'top' : 'bottom';

        // Center horizontally on the icon, then clamp within viewport with some padding.
        const padding = 12;
        const left = clamp(rect.left + rect.width / 2, padding, viewportW - padding);

        const top = placement === 'top' ? clamp(rect.top - 8, padding, viewportH - padding) : clamp(rect.bottom + 8, padding, viewportH - padding);

        setPos({ left, top, placement, maxWidth });
    };

    useEffect(() => {
        compute();
        if (!open) return;
        const onScroll = () => compute();
        const onResize = () => compute();
        window.addEventListener('scroll', onScroll, true);
        window.addEventListener('resize', onResize);
        return () => {
            window.removeEventListener('scroll', onScroll, true);
            window.removeEventListener('resize', onResize);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, anchorEl, text]);

    if (!open || !anchorEl || !pos) return null;

    const style: React.CSSProperties = {
        position: 'fixed',
        left: pos.left,
        top: pos.top,
        transform: pos.placement === 'top' ? 'translate(-50%, -100%)' : 'translate(-50%, 0)',
        maxWidth: pos.maxWidth,
        zIndex: 1000,
    };

    return createPortal(
        <div style={style} className="px-2 py-1 bg-popover text-popover-foreground text-xs rounded shadow-md border border-border whitespace-normal break-words">
            {text}
        </div>,
        document.body
    );
};

interface LabelProps {
    children: React.ReactNode;
    htmlFor?: string;
    tooltip?: string;
    className?: string;
}

export const FormLabel = ({ children, htmlFor, tooltip, className = '' }: LabelProps) => (
    <FormLabelImpl htmlFor={htmlFor} tooltip={tooltip} className={className}>
        {children}
    </FormLabelImpl>
);

const FormLabelImpl = ({ children, htmlFor, tooltip, className = '' }: LabelProps) => {
    const iconRef = useRef<HTMLSpanElement>(null);
    const [open, setOpen] = useState(false);
    const anchorEl = iconRef.current;

    return (
        <label htmlFor={htmlFor} className={`block text-sm font-medium mb-1.5 flex items-center gap-1.5 ${className}`}>
            {children}
            {tooltip && (
                <>
                    <span
                        ref={iconRef}
                        className="inline-flex"
                        onMouseEnter={() => setOpen(true)}
                        onMouseLeave={() => setOpen(false)}
                        onFocus={() => setOpen(true)}
                        onBlur={() => setOpen(false)}
                        tabIndex={0}
                    >
                        <HelpCircle className="w-3.5 h-3.5 text-muted-foreground cursor-help" />
                    </span>
                    <Tooltip anchorEl={anchorEl} text={tooltip} open={open} />
                </>
            )}
        </label>
    );
};

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
    label?: string;
    tooltip?: string;
    error?: string;
}

export const FormInput = React.forwardRef<HTMLInputElement, InputProps>(
    ({ label, tooltip, error, className = '', id, 'aria-label': ariaLabel, ...props }, ref) => {
        const reactId = React.useId();
        const inputId = id ?? reactId;
        const errorId = error ? `${inputId}-error` : undefined;
        return (
            <div className="mb-4">
                {label && <FormLabel htmlFor={inputId} tooltip={tooltip}>{label}</FormLabel>}
                <input
                    ref={ref}
                    id={inputId}
                    aria-label={label ? undefined : ariaLabel}
                    aria-invalid={error ? true : undefined}
                    aria-describedby={errorId}
                    className={`flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 ${error ? 'border-destructive' : ''} ${className}`}
                    {...props}
                />
                {error && <p id={errorId} className="text-xs text-destructive mt-1">{error}</p>}
            </div>
        );
    }
);
FormInput.displayName = 'FormInput';

interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
    label?: string;
    tooltip?: string;
    options: { value: string; label: string }[];
    error?: string;
}

export const FormSelect = React.forwardRef<HTMLSelectElement, SelectProps>(
    ({ label, tooltip, options, error, className = '', id, 'aria-label': ariaLabel, ...props }, ref) => {
        const reactId = React.useId();
        const selectId = id ?? reactId;
        const errorId = error ? `${selectId}-error` : undefined;
        return (
            <div className="mb-4">
                {label && <FormLabel htmlFor={selectId} tooltip={tooltip}>{label}</FormLabel>}
                <div className="relative">
                    <select
                        ref={ref}
                        id={selectId}
                        aria-label={label ? undefined : ariaLabel}
                        aria-invalid={error ? true : undefined}
                        aria-describedby={errorId}
                        className={`flex h-9 w-full items-center justify-between rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 appearance-none ${error ? 'border-destructive' : ''} ${className}`}
                        {...props}
                    >
                        {options.map((opt) => (
                            <option key={opt.value} value={opt.value}>
                                {opt.label}
                            </option>
                        ))}
                    </select>
                    <div className="absolute right-3 top-2.5 pointer-events-none opacity-50">
                        <svg width="10" height="6" viewBox="0 0 10 6" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M1 1L5 5L9 1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                    </div>
                </div>
                {error && <p id={errorId} className="text-xs text-destructive mt-1">{error}</p>}
            </div>
        );
    }
);
FormSelect.displayName = 'FormSelect';

interface SwitchProps extends React.InputHTMLAttributes<HTMLInputElement> {
    label?: string;
    description?: string;
    tooltip?: string;
}

export const FormSwitch = React.forwardRef<HTMLInputElement, SwitchProps>(
    ({ label, description, tooltip, className = '', id, 'aria-label': ariaLabel, ...props }, ref) => {
        const reactId = React.useId();
        const inputId = id ?? reactId;
        const disabled = Boolean(props.disabled);
        return (
        <div
            className={[
                "flex items-center justify-between mb-4 p-3 border border-border rounded-lg bg-card/50",
                disabled ? "opacity-60" : "",
                className,
            ]
                .filter(Boolean)
                .join(" ")}
        >
            <div className="space-y-0.5">
                {label && (
                    <div className="flex items-center gap-1.5">
                        <label
                            htmlFor={inputId}
                            className={["text-sm font-medium", disabled ? "cursor-not-allowed" : "cursor-pointer"]
                                .filter(Boolean)
                                .join(" ")}
                        >
                            {label}
                        </label>
                        {tooltip && (
                            <FormSwitchTooltip tooltip={tooltip} />
                        )}
                    </div>
                )}
                {description && <p className="text-xs text-muted-foreground">{description}</p>}
            </div>
            <label className={["relative inline-flex items-center", disabled ? "cursor-not-allowed" : "cursor-pointer"].join(" ")}>
                <input
                    type="checkbox"
                    ref={ref}
                    id={inputId}
                    aria-label={label ? undefined : ariaLabel}
                    className="sr-only peer"
                    {...props}
                />
                <div className="w-9 h-5 bg-muted peer-focus-visible:outline-none peer-focus-visible:ring-2 peer-focus-visible:ring-offset-2 peer-focus-visible:ring-ring rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-background after:border-border after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-primary peer-disabled:bg-muted/60 peer-disabled:after:bg-muted peer-disabled:after:border-muted"></div>
            </label>
        </div>
        );
    }
);
FormSwitch.displayName = 'FormSwitch';

const FormSwitchTooltip = ({ tooltip }: { tooltip: string }) => {
    const iconRef = useRef<HTMLSpanElement>(null);
    const [open, setOpen] = useState(false);
    const anchorEl = iconRef.current;
    const tip = useMemo(() => tooltip, [tooltip]);
    return (
        <>
            <span
                ref={iconRef}
                className="inline-flex"
                onMouseEnter={() => setOpen(true)}
                onMouseLeave={() => setOpen(false)}
                onFocus={() => setOpen(true)}
                onBlur={() => setOpen(false)}
                tabIndex={0}
            >
                <HelpCircle className="w-3.5 h-3.5 text-muted-foreground cursor-help" />
            </span>
            <Tooltip anchorEl={anchorEl} text={tip} open={open} />
        </>
    );
};
