import { MILESTONES, MILESTONE_STEP, TIME_FALLBACK_MS } from '../config/donation';

export interface ReminderState {
  firstSeenAt: number;
  snoozeUntil: number;
  dismissedForever: boolean;
  lastMilestoneShown: number;
  timeFallbackUsed: boolean;
  shownThisSession: boolean;
}

/** Smallest ladder value strictly greater than `last` (fixed list, then +STEP tail). */
export function nextMilestone(last: number): number {
  for (const m of MILESTONES) {
    if (m > last) return m;
  }
  return Math.floor(last / MILESTONE_STEP) * MILESTONE_STEP + MILESTONE_STEP;
}

/** Highest ladder value <= count (fixed list or +STEP tail); 0 if none reached. */
export function highestLadderValue(count: number): number {
  let highest = 0;
  for (const m of MILESTONES) {
    if (m <= count) highest = m;
  }
  const lastFixed = MILESTONES[MILESTONES.length - 1];
  if (count > lastFixed) {
    highest = Math.max(highest, Math.floor(count / MILESTONE_STEP) * MILESTONE_STEP);
  }
  return highest;
}

/** Pure eligibility decision. `now` and `callCount` injected for testability. */
export function isEligible(
  state: ReminderState,
  callCount: number | undefined,
  now: number,
): boolean {
  if (state.dismissedForever) return false;
  if (state.shownThisSession) return false;
  if (now < state.snoozeUntil) return false;
  const milestoneHit =
    callCount !== undefined && callCount >= nextMilestone(state.lastMilestoneShown);
  const timeFallback =
    !state.timeFallbackUsed && now - state.firstSeenAt >= TIME_FALLBACK_MS;
  return milestoneHit || timeFallback;
}
