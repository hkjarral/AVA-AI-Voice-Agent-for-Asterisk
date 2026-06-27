import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import {
  STORAGE_KEYS,
  SESSION_KEY,
  SNOOZE_LATER_MS,
  SNOOZE_DONATED_MS,
} from '../config/donation';
import { ReminderState, isEligible } from '../utils/donationReminder';

export interface UseDonationReminder {
  show: boolean;
  callCount?: number;
  onLater: () => void;
  onDismiss: () => void;
  onDonate: () => void;
  onAlreadyDonated: () => void;
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
    const snooze = Number(localStorage.getItem(STORAGE_KEYS.snoozeUntil));
    return {
      firstSeenAt,
      snoozeUntil: Number.isNaN(snooze) ? 0 : snooze,
      dismissedForever: localStorage.getItem(STORAGE_KEYS.dismissedForever) === 'true',
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
        /* leave undefined → aged-in fallback still applies */
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

  const snooze = (ms: number) => {
    try {
      localStorage.setItem(STORAGE_KEYS.snoozeUntil, String(Date.now() + ms));
    } catch {
      /* ignore */
    }
    setShow(false);
  };

  return {
    show,
    callCount,
    onLater: () => snooze(SNOOZE_LATER_MS),
    onDonate: () => snooze(SNOOZE_LATER_MS),
    onAlreadyDonated: () => snooze(SNOOZE_DONATED_MS),
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
