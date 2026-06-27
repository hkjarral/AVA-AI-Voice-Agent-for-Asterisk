import { FIRST_SHOW_DELAY_MS } from '../config/donation';

export interface ReminderState {
  firstSeenAt: number;
  snoozeUntil: number;
  dismissedForever: boolean;
  shownThisSession: boolean;
}

/**
 * Pure eligibility decision. `now` and `callCount` injected for testability.
 * Weekly cadence is governed by `snoozeUntil`; this only gates the FIRST show
 * (don't nag a brand-new install before it has handled a call or existed 3 days).
 */
export function isEligible(
  state: ReminderState,
  callCount: number | undefined,
  now: number,
): boolean {
  if (state.dismissedForever) return false;
  if (state.shownThisSession) return false;
  if (now < state.snoozeUntil) return false;
  const hasUsage = callCount !== undefined && callCount >= 1;
  const agedIn = now - state.firstSeenAt >= FIRST_SHOW_DELAY_MS;
  return hasUsage || agedIn;
}
