import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { twMerge } from 'tailwind-merge';

interface EmptyStateProps {
    icon: LucideIcon;
    title: string;
    message?: ReactNode;
    action?: ReactNode;
    variant?: 'default' | 'console';
    className?: string;
    heading?: 'h2' | 'h3' | 'h4';
}

export const EmptyState = ({
    icon: Icon,
    title,
    message,
    action,
    variant = 'default',
    className,
    heading: Heading = 'h2',
}: EmptyStateProps) => {
    const consoleStyle = variant === 'console';

    return (
        <div className={twMerge(
            consoleStyle ? 'py-2 text-center' : 'bg-card border rounded-lg p-12 text-center',
            className,
        )}>
            <div className={twMerge(
                'mx-auto rounded-full flex items-center justify-center',
                consoleStyle ? 'w-10 h-10 mb-2 bg-white/5' : 'w-16 h-16 mb-4 bg-muted',
            )}>
                <Icon aria-hidden="true" className={consoleStyle ? 'w-6 h-6 text-gray-400' : 'w-8 h-8 text-muted-foreground'} />
            </div>
            <Heading className={consoleStyle ? 'text-sm font-semibold mb-2 text-gray-400' : 'text-xl font-semibold mb-2'}>
                {title}
            </Heading>
            {message && <p className={consoleStyle ? 'text-gray-500' : 'text-muted-foreground'}>{message}</p>}
            {action && <div className="mt-4">{action}</div>}
        </div>
    );
};
