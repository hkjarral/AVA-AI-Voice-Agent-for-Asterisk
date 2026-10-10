/** Mirrors the backend policy; omission retains existing provider behavior. */
export function connectionRecoveryError(config: Record<string, unknown>): string | null {
    const limits = [
        { key: 'connect_timeout_sec', min: 0, max: 60, integer: false },
        { key: 'connect_max_retries', min: -1, max: 3, integer: true },
        { key: 'connect_total_timeout_sec', min: 0, max: 180, integer: false },
    ];
    for (const field of limits) {
        const value = config[field.key];
        if (value === undefined || (field.key === 'connect_total_timeout_sec' && value === null)) continue;
        if (typeof value !== 'number' || !Number.isFinite(value) || value <= field.min || value > field.max || (field.integer && !Number.isInteger(value))) {
            return `${field.key} must be ${field.integer ? 'an integer from 0 to 3' : `greater than 0 and at most ${field.max} seconds`}.`;
        }
    }
    return null;
}
