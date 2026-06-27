import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import {
  STORAGE_KEYS,
  SESSION_KEY,
  SNOOZE_LATER_MS,
  SNOOZE_DONATE_MS,
} from '../config/donation';
import { ReminderState, isEligible, highestLadderValue } from '../utils/donationReminder';

export interface UseDonationReminder {
  show: boolean;
  callCount?: number;
  onLater: () => void;
  onDismiss: () => void;
  onDonate: () => void;
}

/** Reads all state; returns null if storage is unavailable (fail closed). */
function readState(): ReminderState | null {
  try {
    const now = Date.now();
    let firstSeenAt = Number(localStorage.getItem(STORAGE_KEYS.firstSeenAt));
    if (!firstSeenAt || Number.isNaN(firstSeenAt)) {
      firstSeenAt = now;
      localStorage.setItem(STORAGE_KEYS.firstSeenAt, String(now));
    }
    const num = (k: string) => {
      const v = Number(localStorage.getItem(k));
      return Number.isNaN(v) ? 0 : v;
    };
    return {
      firstSeenAt,
      snoozeUntil: num(STORAGE_KEYS.snoozeUntil),
      dismissedForever: localStorage.getItem(STORAGE_KEYS.dismissedForever) === 'true',
      lastMilestoneShown: num(STORAGE_KEYS.lastMilestoneShown),
      timeFallbackUsed: localStorage.getItem(STORAGE_KEYS.timeFallbackUsed) === 'true',
      shownThisSession: sessionStorage.getItem(SESSION_KEY) === 'true',
    };
  } catch {
    return null;
  }
}

export function useDonationReminder(): UseDonationReminder {
  const [callCount, setCallCount] = useState<number | undefined>(undefined);
  const [countResolved, setCountResolved] = useState(false);
  const [show, setShow] = useState(false);
  const stateRef = useRef<ReminderState | null | undefined>(undefined);
  if (stateRef.current === undefined) {
    stateRef.current = readState();
  }

  // One-shot call-count fetch (NOT the dashboard poll).
  useEffect(() => {
    let active = true;
    axios
      .get('/api/calls/stats')
      .then((r) => {
        if (active) setCallCount(r.data?.total_calls);
      })
      .catch(() => {
        /* leave undefined → time fallback still applies */
      })
      .finally(() => {
        if (active) setCountResolved(true);
      });
    return () => {
      active = false;
    };
  }, []);

  // Decide once the count has resolved. Side effect lives here, not in render.
  useEffect(() => {
    if (!countResolved) return;
    const state = stateRef.current;
    if (!state) return; // storage broken → never show
    if (isEligible(state, callCount, Date.now())) {
      setShow(true);
      try {
        sessionStorage.setItem(SESSION_KEY, 'true');
      } catch {
        /* ignore */
      }
    }
  }, [countResolved, callCount]);

  const advanceAndSnooze = (snoozeMs: number) => {
    try {
      const now = Date.now();
      const reached = highestLadderValue(callCount ?? 0);
      const prev = Number(localStorage.getItem(STORAGE_KEYS.lastMilestoneShown)) || 0;
      localStorage.setItem(STORAGE_KEYS.lastMilestoneShown, String(Math.max(prev, reached)));
      localStorage.setItem(STORAGE_KEYS.snoozeUntil, String(now + snoozeMs));
      localStorage.setItem(STORAGE_KEYS.timeFallbackUsed, 'true');
    } catch {
      /* ignore */
    }
    setShow(false);
  };

  return {
    show,
    callCount,
    onLater: () => advanceAndSnooze(SNOOZE_LATER_MS),
    onDonate: () => advanceAndSnooze(SNOOZE_DONATE_MS),
    onDismiss: () => {
      try {
        localStorage.setItem(STORAGE_KEYS.dismissedForever, 'true');
      } catch {
        /* ignore */
      }
      setShow(false);
    },
  };
}
