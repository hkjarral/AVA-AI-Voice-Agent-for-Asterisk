/**
 * Pure hysteresis reducer for the top-bar Asterisk connection pill (I1).
 *
 * Previously the pill derived its colour from a single 5s poll
 * (`setAriConnected(... ?? false)` in Dashboard.tsx), so one transient bad
 * read — an Asterisk `http reload`, httpx jitter, or event-loop starvation —
 * painted it red until the next poll recovered. This reducer smooths that:
 *
 *  - A `true` sample → connected immediately, streak cleared.
 *  - A `false` sample → only flips to red after THREE consecutive falses;
 *    until then the last confirmed state is held (last-good retention).
 *  - An `'unknown'` sample (rejected request, or a response missing
 *    `data.live` / `ari_reachable`) → hold the previous state and do NOT
 *    advance the failure streak. Missing data is "we don't know", not "down".
 *
 * Mirrors the 2-strike `debouncedBool` pattern in SystemTopology.tsx, with a
 * 3-strike threshold for the pill as specified in the dashboard UI/UX audit.
 */

/** What a single poll told us about reachability. */
export type ConnectionSample = boolean | 'unknown';

export interface ConnectionState {
    /** null = unknown/checking, true = connected, false = disconnected. */
    display: boolean | null;
    /** Consecutive explicit `false` reads (unknowns do not count). */
    failStreak: number;
}

/** Consecutive explicit failures required before showing red. */
export const FAIL_THRESHOLD = 3;

export const INITIAL_CONNECTION_STATE: ConnectionState = {
    display: null,
    failStreak: 0,
};

export const reduceConnection = (
    prev: ConnectionState,
    sample: ConnectionSample,
): ConnectionState => {
    // Missing data: hold previous display, leave the streak untouched.
    if (sample === 'unknown') {
        return prev;
    }

    if (sample === true) {
        return { display: true, failStreak: 0 };
    }

    // sample === false
    const failStreak = prev.failStreak + 1;
    if (failStreak >= FAIL_THRESHOLD) {
        return { display: false, failStreak };
    }
    // Hold last-good through the first two failures: stay connected if we were,
    // otherwise stay unknown (warming up).
    return {
        display: prev.display === true ? true : null,
        failStreak,
    };
};
