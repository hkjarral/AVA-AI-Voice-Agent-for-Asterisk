import { useState, useEffect } from 'react';

export const SIDEBAR_COLLAPSED_KEY = 'ava-sidebar-collapsed';

export function useSidebarCollapsed() {
    const [collapsed, setCollapsed] = useState<boolean>(() => {
        try {
            return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1';
        } catch {
            // Storage blocked (private mode, policy) — default to expanded.
            return false;
        }
    });

    useEffect(() => {
        try {
            localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? '1' : '0');
        } catch {
            // Keep the in-memory preference when storage is unavailable.
        }
    }, [collapsed]);

    const toggle = () => setCollapsed((c) => !c);

    return { collapsed, setCollapsed, toggle };
}
