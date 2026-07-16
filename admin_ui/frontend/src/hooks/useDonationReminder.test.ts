// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import axios from 'axios';
import { useDonationReminder } from './useDonationReminder';
import { STORAGE_KEYS, SESSION_KEY } from '../config/donation';

vi.mock('axios');
const mockGet = axios.get as unknown as ReturnType<typeof vi.fn>;
const DAY = 24 * 60 * 60 * 1000;

describe('useDonationReminder', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    vi.clearAllMocks();
  });

  it('shows at ten completed calls and records the milestone and session', async () => {
    mockGet.mockResolvedValue({ data: { total_calls: 15, outcomes: { completed: 10 } } });
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.show).toBe(true));
    expect(result.current.callCount).toBe(10);
    expect(sessionStorage.getItem(SESSION_KEY)).toBe('true');
    expect(localStorage.getItem(STORAGE_KEYS.lastMilestoneShown)).toBe('10');
  });

  it('does not show before ten completed calls even if total calls are higher', async () => {
    mockGet.mockResolvedValue({ data: { total_calls: 50, outcomes: { completed: 9 } } });
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.callCount).toBe(9));
    expect(result.current.show).toBe(false);
  });

  it('does not replay a milestone that was already shown', async () => {
    localStorage.setItem(STORAGE_KEYS.lastMilestoneShown, '25');
    mockGet.mockResolvedValue({ data: { outcomes: { completed: 49 } } });
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.callCount).toBe(49));
    expect(result.current.show).toBe(false);
  });

  it('records only the highest milestone after a count jump', async () => {
    localStorage.setItem(STORAGE_KEYS.lastMilestoneShown, '10');
    mockGet.mockResolvedValue({ data: { outcomes: { completed: 320 } } });
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.show).toBe(true));
    expect(localStorage.getItem(STORAGE_KEYS.lastMilestoneShown)).toBe('300');
  });

  it('Maybe later snoozes ~2 weeks', async () => {
    mockGet.mockResolvedValue({ data: { outcomes: { completed: 10 } } });
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.show).toBe(true));
    act(() => result.current.onLater());
    expect(result.current.show).toBe(false);
    const snooze = Number(localStorage.getItem(STORAGE_KEYS.snoozeUntil));
    expect(snooze).toBeGreaterThan(Date.now() + 13 * DAY);
    expect(snooze).toBeLessThan(Date.now() + 15 * DAY);
  });

  it('donate-link click snoozes ~1 month', async () => {
    mockGet.mockResolvedValue({ data: { outcomes: { completed: 10 } } });
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.show).toBe(true));
    act(() => result.current.onDonate());
    const snooze = Number(localStorage.getItem(STORAGE_KEYS.snoozeUntil));
    expect(snooze).toBeGreaterThan(Date.now() + 29 * DAY);
    expect(snooze).toBeLessThan(Date.now() + 31 * DAY);
    expect(result.current.show).toBe(false);
  });

  it('I already support AVA snoozes ~6 months', async () => {
    mockGet.mockResolvedValue({ data: { outcomes: { completed: 10 } } });
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.show).toBe(true));
    act(() => result.current.onAlreadyDonated());
    const snooze = Number(localStorage.getItem(STORAGE_KEYS.snoozeUntil));
    expect(snooze).toBeGreaterThan(Date.now() + 170 * DAY);
    expect(snooze).toBeLessThan(Date.now() + 190 * DAY);
  });

  it("Don't show again sets the permanent flag", async () => {
    mockGet.mockResolvedValue({ data: { outcomes: { completed: 10 } } });
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.show).toBe(true));
    act(() => result.current.onDismiss());
    expect(localStorage.getItem(STORAGE_KEYS.dismissedForever)).toBe('true');
  });

  it('fails closed when the stats fetch fails', async () => {
    mockGet.mockRejectedValue(new Error('network'));
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith('/api/calls/stats'));
    expect(result.current.callCount).toBeUndefined();
    expect(result.current.show).toBe(false);
  });

  it('Keep reminders snoozes ~1 month', async () => {
    mockGet.mockResolvedValue({ data: { outcomes: { completed: 10 } } });
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.show).toBe(true));
    act(() => result.current.onKeepReminders());
    const snooze = Number(localStorage.getItem(STORAGE_KEYS.snoozeUntil));
    expect(snooze).toBeGreaterThan(Date.now() + 25 * DAY);
    expect(snooze).toBeLessThan(Date.now() + 35 * DAY);
  });
});
