import { describe, it, expect } from 'vitest';
import { isEligible, milestoneForCallCount, ReminderState } from './donationReminder';

const base: ReminderState = {
  snoozeUntil: 0,
  dismissedForever: false,
  shownThisSession: false,
  lastMilestoneShown: 0,
};

describe('milestoneForCallCount', () => {
  it.each([
    [undefined, 0],
    [0, 0],
    [9, 0],
    [10, 10],
    [24, 10],
    [25, 25],
    [749, 500],
    [750, 750],
    [9999, 7500],
    [10000, 10000],
    [14999, 10000],
    [15000, 15000],
    [23890, 20000],
  ])('maps %s completed calls to milestone %s', (calls, expected) => {
    expect(milestoneForCallCount(calls)).toBe(expected);
  });
});

describe('isEligible', () => {
  it('false when dismissed forever', () =>
    expect(isEligible({ ...base, dismissedForever: true }, 100, 0)).toBe(false));
  it('false when already shown this session', () =>
    expect(isEligible({ ...base, shownThisSession: true }, 100, 0)).toBe(false));
  it('false while snoozed', () =>
    expect(isEligible({ ...base, snoozeUntil: 100 }, 100, 50)).toBe(false));
  it('does not show before ten completed calls', () => expect(isEligible(base, 9, 0)).toBe(false));
  it('shows when a new milestone is reached', () => expect(isEligible(base, 10, 0)).toBe(true));
  it('does not replay a milestone that was already shown', () =>
    expect(isEligible({ ...base, lastMilestoneShown: 10 }, 24, 0)).toBe(false));
  it('shows only the highest milestone after several are crossed', () =>
    expect(isEligible({ ...base, lastMilestoneShown: 10 }, 300, 0)).toBe(true));
  it('fails closed when the completed-call count is unknown', () =>
    expect(isEligible(base, undefined, 0)).toBe(false));
  it('shows a newer milestone after the snooze window elapses', () =>
    expect(isEligible({ ...base, snoozeUntil: 1000, lastMilestoneShown: 10 }, 25, 1000)).toBe(
      true,
    ));
});
