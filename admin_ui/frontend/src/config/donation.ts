export const KOFI_URL = 'https://ko-fi.com/asteriskaivoiceagent';
export const SPONSORS_URL = 'https://github.com/sponsors/hkjarral';

// Reminder ladder: fixed milestones, then repeat every MILESTONE_STEP.
// Invariant: the last fixed milestone must be a multiple of MILESTONE_STEP —
// the ladder continues from it in MILESTONE_STEP increments (the tail guard relies on this).
export const MILESTONES = [10, 25, 50, 100, 200, 500, 1000];
export const MILESTONE_STEP = 1000;

const DAY_MS = 24 * 60 * 60 * 1000;
export const SNOOZE_LATER_MS = 30 * DAY_MS;
export const SNOOZE_DONATE_MS = 180 * DAY_MS;
export const TIME_FALLBACK_MS = 30 * DAY_MS;

export const STORAGE_KEYS = {
  firstSeenAt: 'aava.donation.firstSeenAt',
  snoozeUntil: 'aava.donation.snoozeUntil',
  dismissedForever: 'aava.donation.dismissedForever',
  lastMilestoneShown: 'aava.donation.lastMilestoneShown',
  timeFallbackUsed: 'aava.donation.timeFallbackUsed',
} as const;

export const SESSION_KEY = 'aava.donation.shownThisSession';
