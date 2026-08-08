// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';

import { useSidebarCollapsed, SIDEBAR_COLLAPSED_KEY } from './useSidebarCollapsed';

describe('useSidebarCollapsed', () => {
    beforeEach(() => {
        localStorage.clear();
    });

    afterEach(() => {
        vi.restoreAllMocks();
    });

    it('falls back to expanded when localStorage.getItem throws', () => {
        vi.spyOn(localStorage, 'getItem').mockImplementation(() => {
            throw new Error('storage blocked');
        });

        const { result } = renderHook(() => useSidebarCollapsed());

        expect(result.current.collapsed).toBe(false);
    });

    it('does not throw when localStorage.setItem fails', () => {
        vi.spyOn(localStorage, 'setItem').mockImplementation(() => {
            throw new Error('quota exceeded');
        });

        const { result } = renderHook(() => useSidebarCollapsed());

        expect(() => act(() => result.current.toggle())).not.toThrow();
        expect(result.current.collapsed).toBe(true);
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
