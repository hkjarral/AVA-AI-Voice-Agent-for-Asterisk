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

  it('shows once the install has handled a call and sets shownThisSession', async () => {
    mockGet.mockResolvedValue({ data: { total_calls: 5 } });
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.show).toBe(true));
    expect(sessionStorage.getItem(SESSION_KEY)).toBe('true');
  });

  it('does not show a brand-new zero-call install', async () => {
    mockGet.mockResolvedValue({ data: { total_calls: 0 } });
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.callCount).toBe(0));
    expect(result.current.show).toBe(false);
  });

  it('Maybe later snoozes ~1 week', async () => {
    mockGet.mockResolvedValue({ data: { total_calls: 5 } });
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.show).toBe(true));
    act(() => result.current.onLater());
    expect(result.current.show).toBe(false);
    const snooze = Number(localStorage.getItem(STORAGE_KEYS.snoozeUntil));
    expect(snooze).toBeGreaterThan(Date.now() + 6 * DAY);
    expect(snooze).toBeLessThan(Date.now() + 8 * DAY);
  });

  it('donate-link click snoozes ~1 week (same as later)', async () => {
    mockGet.mockResolvedValue({ data: { total_calls: 5 } });
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.show).toBe(true));
    act(() => result.current.onDonate());
    const snooze = Number(localStorage.getItem(STORAGE_KEYS.snoozeUntil));
    expect(snooze).toBeLessThan(Date.now() + 8 * DAY);
    expect(snooze).toBeGreaterThan(Date.now() + 6 * DAY);
    expect(result.current.show).toBe(false);
  });

  it('I already donated snoozes ~3 months', async () => {
    mockGet.mockResolvedValue({ data: { total_calls: 5 } });
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.show).toBe(true));
    act(() => result.current.onAlreadyDonated());
    const snooze = Number(localStorage.getItem(STORAGE_KEYS.snoozeUntil));
    expect(snooze).toBeGreaterThan(Date.now() + 80 * DAY);
  });

  it("Don't show again sets the permanent flag", async () => {
    mockGet.mockResolvedValue({ data: { total_calls: 5 } });
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.show).toBe(true));
    act(() => result.current.onDismiss());
    expect(localStorage.getItem(STORAGE_KEYS.dismissedForever)).toBe('true');
  });

  it('stays eligible (aged-in) when the stats fetch fails', async () => {
    mockGet.mockRejectedValue(new Error('network'));
    localStorage.setItem(STORAGE_KEYS.firstSeenAt, String(Date.now() - 4 * DAY));
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.show).toBe(true));
    expect(result.current.callCount).toBeUndefined();
  });

  it('Keep reminders snoozes ~1 month', async () => {
    mockGet.mockResolvedValue({ data: { total_calls: 5 } });
    const { result } = renderHook(() => useDonationReminder());
    await waitFor(() => expect(result.current.show).toBe(true));
    act(() => result.current.onKeepReminders());
    const snooze = Number(localStorage.getItem(STORAGE_KEYS.snoozeUntil));
    expect(snooze).toBeGreaterThan(Date.now() + 25 * DAY);
    expect(snooze).toBeLessThan(Date.now() + 35 * DAY);
  });
});
