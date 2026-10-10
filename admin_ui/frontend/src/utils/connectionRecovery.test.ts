import { describe, expect, it } from 'vitest';
import { connectionRecoveryError } from './connectionRecovery';

describe('connection recovery validation', () => {
    it.each([{}, { connect_max_retries: 0 }, { connect_timeout_sec: 10, connect_max_retries: 1, connect_total_timeout_sec: null }])('accepts legacy and valid opt-in settings', config => {
        expect(connectionRecoveryError(config)).toBeNull();
    });
    it.each([
        { connect_timeout_sec: 0 }, { connect_timeout_sec: Infinity },
        { connect_max_retries: 4 }, { connect_max_retries: 0.5 },
        { connect_max_retries: false }, { connect_total_timeout_sec: -1 },
    ])('rejects invalid settings before save', config => {
        expect(connectionRecoveryError(config)).not.toBeNull();
    });
});
