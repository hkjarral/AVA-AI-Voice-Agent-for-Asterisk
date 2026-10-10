import type { ButtonHTMLAttributes } from 'react';
import type { LucideIcon } from 'lucide-react';
import { twMerge } from 'tailwind-merge';

interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children' | 'aria-label'> {
    icon: LucideIcon;
    label: string;
    variant?: 'default' | 'destructive' | 'primary';
    size?: 'sm' | 'md';
    iconClassName?: string;
}

const variantClasses = {
    default: 'hover:bg-accent',
    destructive: 'text-destructive hover:bg-destructive/10',
    primary: 'bg-primary text-primary-foreground hover:bg-primary/90',
};

export const IconButton = ({
    icon: Icon,
    label,
    variant = 'default',
    size = 'md',
    className,
    iconClassName,
    title = label,
    type = 'button',
    ...props
}: IconButtonProps) => (
    <button
        {...props}
        type={type}
        aria-label={label}
        title={title}
        className={twMerge(
            'rounded-md disabled:opacity-50 disabled:cursor-not-allowed',
            size === 'sm' ? 'p-1.5' : 'p-2',
            variantClasses[variant],
            className,
        )}
    >
        <Icon aria-hidden="true" className={twMerge('w-4 h-4', iconClassName)} />
    </button>
);
