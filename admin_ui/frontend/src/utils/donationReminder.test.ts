import { describe, it, expect } from 'vitest';
import { nextMilestone, highestLadderValue, isEligible, ReminderState } from './donationReminder';
import { TIME_FALLBACK_MS } from '../config/donation';

const base: ReminderState = {
  firstSeenAt: 0,
  snoozeUntil: 0,
  dismissedForever: false,
  lastMilestoneShown: 0,
  timeFallbackUsed: false,
  shownThisSession: false,
};

describe('nextMilestone', () => {
  it('starts at 10', () => expect(nextMilestone(0)).toBe(10));
  it('walks the fixed ladder', () => {
    expect(nextMilestone(10)).toBe(25);
    expect(nextMilestone(50)).toBe(100);
    expect(nextMilestone(500)).toBe(1000);
  });
  it('repeats every 1000 after the last fixed milestone', () => {
    expect(nextMilestone(1000)).toBe(2000);
    expect(nextMilestone(2000)).toBe(3000);
  });
});

describe('highestLadderValue', () => {
  it('is 0 below the first milestone', () => expect(highestLadderValue(9)).toBe(0));
  it('picks the highest fixed milestone reached', () => {
    expect(highestLadderValue(10)).toBe(10);
    expect(highestLadderValue(99)).toBe(50);
    expect(highestLadderValue(700)).toBe(500);
  });
  it('handles the +1000 tail', () => {
    expect(highestLadderValue(1500)).toBe(1000);
    expect(highestLadderValue(2500)).toBe(2000);
  });
  it('returns the last fixed milestone exactly at the boundary', () =>
    expect(highestLadderValue(1000)).toBe(1000));
});

describe('isEligible', () => {
  it('false when dismissed forever', () =>
    expect(isEligible({ ...base, dismissedForever: true }, 1000, 0)).toBe(false));
  it('false when already shown this session', () =>
    expect(isEligible({ ...base, shownThisSession: true }, 1000, 0)).toBe(false));
  it('false while snoozed', () =>
    expect(isEligible({ ...base, snoozeUntil: 100 }, 1000, 50)).toBe(false));
  it('true on milestone hit', () => expect(isEligible(base, 10, 0)).toBe(true));
  it('false below next milestone', () => expect(isEligible(base, 9, 0)).toBe(false));
  it('false on milestone path when count is undefined', () =>
    expect(isEligible(base, undefined, 0)).toBe(false));
  it('true via time fallback after 30 days with low usage', () =>
    expect(isEligible(base, 0, TIME_FALLBACK_MS)).toBe(true));
  it('true via time fallback even when count undefined', () =>
    expect(isEligible(base, undefined, TIME_FALLBACK_MS)).toBe(true));
  it('false via time fallback once already used', () =>
    expect(isEligible({ ...base, timeFallbackUsed: true }, 0, TIME_FALLBACK_MS)).toBe(false));
});
