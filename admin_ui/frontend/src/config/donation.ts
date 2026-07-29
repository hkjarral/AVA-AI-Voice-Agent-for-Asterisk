export const KOFI_URL = 'https://ko-fi.com/asteriskaivoiceagent';
export const SPONSORS_URL = 'https://github.com/sponsors/hkjarral';

const DAY_MS = 24 * 60 * 60 * 1000;
export const DONATION_MILESTONES = [
  10, 25, 50, 100, 150, 200, 250, 300, 500, 750, 1000, 1500, 2000, 2500, 5000,
  7500, 10000,
] as const;
export const MILESTONE_TAIL_STEP = 5000;
// At most one reminder every two weeks, even if several milestones are crossed.
export const SNOOZE_LATER_MS = 14 * DAY_MS;
// A contribution link click receives a longer pause than "Maybe later".
export const SNOOZE_DONATE_MS = 30 * DAY_MS;
// Existing supporters receive a six-month pause.
export const SNOOZE_DONATED_MS = 180 * DAY_MS;
// "Keep reminders" (the soft path on the dismiss confirm) — snooze ~1 month.
export const SNOOZE_MONTH_MS = 30 * DAY_MS;

export const STORAGE_KEYS = {
  snoozeUntil: 'aava.donation.snoozeUntil',
  dismissedForever: 'aava.donation.dismissedForever',
  lastMilestoneShown: 'aava.donation.lastMilestoneShown',
} as const;

export const SESSION_KEY = 'aava.donation.shownThisSession';
