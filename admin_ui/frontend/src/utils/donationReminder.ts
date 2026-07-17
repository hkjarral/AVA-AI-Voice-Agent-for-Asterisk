import { DONATION_MILESTONES, MILESTONE_TAIL_STEP } from '../config/donation';

export interface ReminderState {
  snoozeUntil: number;
  dismissedForever: boolean;
  shownThisSession: boolean;
  lastMilestoneShown: number;
}

/**
 * Return the highest donation milestone reached by a completed-call count.
 * After the explicit early milestones, milestones continue every 5,000 calls.
 */
export function milestoneForCallCount(callCount: number | undefined): number {
  if (callCount === undefined || !Number.isFinite(callCount) || callCount < 10) return 0;

  const lastExplicit = DONATION_MILESTONES[DONATION_MILESTONES.length - 1];
  if (callCount >= lastExplicit) {
    return (
      lastExplicit +
      Math.floor((callCount - lastExplicit) / MILESTONE_TAIL_STEP) * MILESTONE_TAIL_STEP
    );
  }

  for (let i = DONATION_MILESTONES.length - 1; i >= 0; i -= 1) {
    if (callCount >= DONATION_MILESTONES[i]) return DONATION_MILESTONES[i];
  }
  return 0;
}

/** Pure eligibility decision; `now` and `callCount` are injected for testability. */
export function isEligible(
  state: ReminderState,
  callCount: number | undefined,
  now: number,
): boolean {
  if (state.dismissedForever) return false;
  if (state.shownThisSession) return false;
  if (now < state.snoozeUntil) return false;
  return milestoneForCallCount(callCount) > state.lastMilestoneShown;
}
