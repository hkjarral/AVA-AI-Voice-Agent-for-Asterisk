import { useState, useEffect } from 'react';

export const SIDEBAR_COLLAPSED_KEY = 'ava-sidebar-collapsed';

export function useSidebarCollapsed() {
    const [collapsed, setCollapsed] = useState<boolean>(() => {
        return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1';
    });

    useEffect(() => {
        localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? '1' : '0');
    }, [collapsed]);

    const toggle = () => setCollapsed((c) => !c);

    return { collapsed, setCollapsed, toggle };
}
