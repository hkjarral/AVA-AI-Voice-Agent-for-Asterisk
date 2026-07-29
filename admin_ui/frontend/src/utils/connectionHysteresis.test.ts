import { describe, it, expect } from 'vitest';
import {
    INITIAL_CONNECTION_STATE,
    reduceConnection,
    type ConnectionState,
    type ConnectionSample,
} from './connectionHysteresis';

/**
 * I1 — Asterisk pill hysteresis + last-good.
 *
 * The top-bar Asterisk pill used to flip red on a single bad 5s poll
 * (Dashboard.tsx:142, `?? false`). This pure reducer smooths that: it requires
 * three consecutive explicit `false` reads before showing red, holds last-good
 * meanwhile, and treats a missing/unknown sample (rejected request or absent
 * `data.live`) as "hold previous state" — never as `false`.
 *
 * Mirrors the 2-strike debounce already in SystemTopology.tsx (debouncedBool),
 * but with a 3-strike threshold as specified for the top-bar pill.
 */
describe('reduceConnection', () => {
    const start: ConnectionState = INITIAL_CONNECTION_STATE;

    const apply = (samples: ConnectionSample[], from: ConnectionState = start): ConnectionState =>
        samples.reduce((acc, s) => reduceConnection(acc, s), from);

    it('starts unknown (null) before any sample', () => {
        expect(start.display).toBeNull();
    });

    it('a single true sample shows connected immediately', () => {
        const next = reduceConnection(start, true);
        expect(next.display).toBe(true);
    });

    it('any true read clears the failure streak and shows connected', () => {
        const next = apply([false, false, true]);
        expect(next.display).toBe(true);
        expect(next.failStreak).toBe(0);
    });

    it('holds last-good (true) through one and two consecutive false reads', () => {
        const afterGood = reduceConnection(start, true);
        const afterOne = reduceConnection(afterGood, false);
        expect(afterOne.display).toBe(true);
        const afterTwo = reduceConnection(afterOne, false);
        expect(afterTwo.display).toBe(true);
    });

    it('flips to red only on the third consecutive false read', () => {
        const next = apply([true, false, false, false]);
        expect(next.display).toBe(false);
    });

    it('from unknown, three false reads still go red', () => {
        const next = apply([false, false, false]);
        expect(next.display).toBe(false);
    });

    it('from unknown, fewer than three false reads stay unknown (null)', () => {
        expect(apply([false]).display).toBeNull();
        expect(apply([false, false]).display).toBeNull();
    });

    it('treats an "unknown" sample (rejected request / missing data.live) as hold-previous, not false', () => {
        // Build up to connected, then feed unknowns — must stay connected and
        // must NOT advance the failure streak toward red.
        const connected = reduceConnection(start, true);
        const afterUnknowns = apply(['unknown', 'unknown', 'unknown'], connected);
        expect(afterUnknowns.display).toBe(true);
        expect(afterUnknowns.failStreak).toBe(0);
    });

    it('unknown samples do not count toward the 3-strike red threshold', () => {
        // false, unknown, false, unknown, false => only 3 falses but interleaved
        // unknowns must not reset the streak; the third false trips red.
        const next = apply([true, false, 'unknown', false, 'unknown', false]);
        expect(next.display).toBe(false);
    });

    it('a single false after going red keeps it red (streak stays saturated)', () => {
        const red = apply([false, false, false]);
        expect(red.display).toBe(false);
        expect(reduceConnection(red, false).display).toBe(false);
    });
});
