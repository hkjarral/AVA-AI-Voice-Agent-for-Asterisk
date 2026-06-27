// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import axios from 'axios';
import { useDonationReminder } from './useDonationReminder';
import { STORAGE_KEYS, SESSION_KEY } from '../config/donation';

vi.mock('axios');
const mockGet = axios.get as unknown as ReturnType<typeof vi.fn>;

describe('useDonationReminder', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    vi.clearAllMocks();
  });

  it('shows on milestone hit and sets shownThisSession', async () => {
    mockGet.mockResolvedValue({ data: { total_calls: 10 } });
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.show).toBe(true));
    expect(sessionStorage.getItem(SESSION_KEY)).toBe('true');
  });

  it('does not show below the first milestone', async () => {
    mockGet.mockResolvedValue({ data: { total_calls: 9 } });
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.callCount).toBe(9));
    expect(result.current.show).toBe(false);
  });

  it('Maybe later snoozes into the future and advances the milestone', async () => {
    mockGet.mockResolvedValue({ data: { total_calls: 25 } });
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.show).toBe(true));
    act(() => result.current.onLater());
    expect(result.current.show).toBe(false);
    expect(localStorage.getItem(STORAGE_KEYS.lastMilestoneShown)).toBe('25');
    expect(Number(localStorage.getItem(STORAGE_KEYS.snoozeUntil))).toBeGreaterThan(Date.now());
    expect(localStorage.getItem(STORAGE_KEYS.timeFallbackUsed)).toBe('true');
  });

  it("Don't show again sets the permanent flag", async () => {
    mockGet.mockResolvedValue({ data: { total_calls: 100 } });
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.show).toBe(true));
    act(() => result.current.onDismiss());
    expect(localStorage.getItem(STORAGE_KEYS.dismissedForever)).toBe('true');
  });

  it('stays eligible via time fallback when the stats fetch fails', async () => {
    mockGet.mockRejectedValue(new Error('network'));
    localStorage.setItem(STORAGE_KEYS.firstSeenAt, String(Date.now() - 31 * 24 * 60 * 60 * 1000));
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.show).toBe(true));
    expect(result.current.callCount).toBeUndefined();
  });
});
