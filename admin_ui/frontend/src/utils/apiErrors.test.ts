import { describe, expect, it } from 'vitest';
import { apiErrorMessage, describeApiError } from './apiErrors';

describe('API error text', () => {
    it('preserves backend validation details as text', () => {
        const error = { isAxiosError: true, message: 'Request failed', response: {
            status: 409, statusText: 'Conflict', data: { detail: 'Mapping changed; reload before saving' },
        } };
        expect(apiErrorMessage(error, 'Unable to save')).toBe('Mapping changed; reload before saving');
        expect(describeApiError(error).status).toBe(409);
    });
    it('formats structured validation details rather than returning an object', () => {
        const error = { isAxiosError: true, response: { status: 422, data: { detail: [{ msg: 'Invalid host' }] } } };
        expect(apiErrorMessage(error, 'Unable to save')).toBe('[{"msg":"Invalid host"}]');
    });
    it('keeps network errors and an empty-error fallback readable', () => {
        expect(apiErrorMessage({ isAxiosError: true, message: 'Network unavailable' }, 'Unable to load')).toBe('Network unavailable');
        expect(apiErrorMessage(new Error(''), 'Unable to load')).toBe('Unable to load');
    });
});
