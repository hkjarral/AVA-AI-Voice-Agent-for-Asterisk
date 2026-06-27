import { describe, it, expect } from 'vitest';
import { isEligible, ReminderState } from './donationReminder';
import { FIRST_SHOW_DELAY_MS } from '../config/donation';

const base: ReminderState = {
  firstSeenAt: 0,
  snoozeUntil: 0,
  dismissedForever: false,
  shownThisSession: false,
};

describe('isEligible', () => {
  it('false when dismissed forever', () =>
    expect(isEligible({ ...base, dismissedForever: true }, 100, FIRST_SHOW_DELAY_MS)).toBe(false));
  it('false when already shown this session', () =>
    expect(isEligible({ ...base, shownThisSession: true }, 100, FIRST_SHOW_DELAY_MS)).toBe(false));
  it('false while snoozed', () =>
    expect(isEligible({ ...base, snoozeUntil: 100 }, 100, 50)).toBe(false));
  it('shows once the install has at least one call', () =>
    expect(isEligible(base, 1, 0)).toBe(true));
  it('does not show a fresh zero-call install before the delay', () =>
    expect(isEligible(base, 0, FIRST_SHOW_DELAY_MS - 1)).toBe(false));
  it('shows a zero-call install once 3 days have passed', () =>
    expect(isEligible(base, 0, FIRST_SHOW_DELAY_MS)).toBe(true));
  it('shows when call count is unknown but the install has aged in', () =>
    expect(isEligible(base, undefined, FIRST_SHOW_DELAY_MS)).toBe(true));
  it('does not show when call count is unknown and not yet aged in', () =>
    expect(isEligible(base, undefined, FIRST_SHOW_DELAY_MS - 1)).toBe(false));
  it('re-shows after a snooze window elapses', () =>
    expect(isEligible({ ...base, snoozeUntil: 1000 }, 5, 1000)).toBe(true));
});
