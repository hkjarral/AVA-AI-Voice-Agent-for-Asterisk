import { useState, useEffect } from 'react';

type Theme = 'light' | 'dark' | 'system';

const STORAGE_KEY = 'ava-theme';

function getSystemTheme(): 'light' | 'dark' {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function applyTheme(theme: Theme) {
    const resolved = theme === 'system' ? getSystemTheme() : theme;
    document.documentElement.classList.toggle('dark', resolved === 'dark');
}

export function useTheme() {
    const [theme, setThemeState] = useState<Theme>(() => {
        const stored = localStorage.getItem(STORAGE_KEY) as Theme | null;
        return stored && ['light', 'dark', 'system'].includes(stored) ? stored : 'system';
    });

    useEffect(() => {
        applyTheme(theme);
        localStorage.setItem(STORAGE_KEY, theme);
    }, [theme]);

    // Listen for OS theme changes when in system mode
    useEffect(() => {
        const mq = window.matchMedia('(prefers-color-scheme: dark)');
        const handler = () => {
            if (theme === 'system') applyTheme('system');
        };
        mq.addEventListener('change', handler);
        return () => mq.removeEventListener('change', handler);
    }, [theme]);

    const setTheme = (t: Theme) => setThemeState(t);

    const cycleTheme = () => {
        const order: Theme[] = ['light', 'dark', 'system'];
        const next = order[(order.indexOf(theme) + 1) % order.length];
        setTheme(next);
    };

    return { theme, setTheme, cycleTheme };
}
