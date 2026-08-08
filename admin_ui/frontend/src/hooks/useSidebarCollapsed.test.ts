// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

import { useSidebarCollapsed, SIDEBAR_COLLAPSED_KEY } from './useSidebarCollapsed';

describe('useSidebarCollapsed', () => {
    beforeEach(() => {
        localStorage.clear();
    });

    it('defaults to expanded when nothing is stored', () => {
        const { result } = renderHook(() => useSidebarCollapsed());
        expect(result.current.collapsed).toBe(false);
    });

    it('initializes collapsed when localStorage says so', () => {
        localStorage.setItem(SIDEBAR_COLLAPSED_KEY, '1');
        const { result } = renderHook(() => useSidebarCollapsed());
        expect(result.current.collapsed).toBe(true);
    });

    it('toggle flips the state and persists it', () => {
        const { result } = renderHook(() => useSidebarCollapsed());

        act(() => result.current.toggle());

        expect(result.current.collapsed).toBe(true);
        expect(localStorage.getItem(SIDEBAR_COLLAPSED_KEY)).toBe('1');
    });
});
