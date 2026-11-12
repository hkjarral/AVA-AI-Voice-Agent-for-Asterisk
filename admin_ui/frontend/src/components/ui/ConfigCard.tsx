import React from 'react';

interface ConfigCardProps {
    children: React.ReactNode;
    className?: string;
    title?: string;
    icon?: React.ReactNode;
    action?: React.ReactNode;
}

export const ConfigCard = ({ children, className = '', title, icon, action }: ConfigCardProps) => {
    return (
        <div className={`bg-card border border-border rounded-lg shadow-sm p-6 transition-all duration-200 hover:shadow-md hover:border-border/80 ${className}`}>
            {(title || action) && (
                <div className="mb-4 flex items-center justify-between gap-3">
                    {title && <h2 className="flex items-center gap-2 font-semibold">{icon}{title}</h2>}
                    {action}
                </div>
            )}
            {children}
        </div>
    );
};
