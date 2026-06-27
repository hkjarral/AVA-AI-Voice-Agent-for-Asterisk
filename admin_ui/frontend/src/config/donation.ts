export const KOFI_URL = 'https://ko-fi.com/asteriskaivoiceagent';
export const SPONSORS_URL = 'https://github.com/sponsors/hkjarral';

const DAY_MS = 24 * 60 * 60 * 1000;
// Don't nag a brand-new install before it has delivered value: first show after
// the first handled call OR 3 days, whichever comes first.
export const FIRST_SHOW_DELAY_MS = 3 * DAY_MS;
// Weekly recurring cadence — "Maybe later" and donate-link clicks snooze a week.
export const SNOOZE_LATER_MS = 7 * DAY_MS;
// "I already donated" — a generous 3-month break for someone who gave.
export const SNOOZE_DONATED_MS = 90 * DAY_MS;
// "Keep reminders" (the soft path on the dismiss confirm) — snooze ~1 month.
export const SNOOZE_MONTH_MS = 30 * DAY_MS;

export const STORAGE_KEYS = {
  firstSeenAt: 'aava.donation.firstSeenAt',
  snoozeUntil: 'aava.donation.snoozeUntil',
  dismissedForever: 'aava.donation.dismissedForever',
} as const;

export const SESSION_KEY = 'aava.donation.shownThisSession';
