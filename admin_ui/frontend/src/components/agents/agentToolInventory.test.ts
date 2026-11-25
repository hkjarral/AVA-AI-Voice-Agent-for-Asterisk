import { describe, expect, it } from 'vitest';

import { transferDestinationsFromConfig, inactivityFallbackDestinations } from './agentToolInventory';


describe('Agent transfer destination inventory', () => {
    it('limits inactivity fallback to supported destinations within the agent scope', () => {
        const destinations = transferDestinationsFromConfig({
            reception: { type: 'queue', target: '600' },
            internal: { type: 'extension', target: '100' },
            dialer: { type: 'vicidial_ingroup', target: 'sales' },
        });
        expect(inactivityFallbackDestinations(destinations, 'selected', ['reception']).map(d => d.key)).toEqual(['reception']);
        expect(inactivityFallbackDestinations(destinations, 'none', [])).toEqual([]);
        expect(inactivityFallbackDestinations(destinations, 'future-policy', [])).toEqual([]);
        expect(inactivityFallbackDestinations(destinations, 'inherit', []).map(d => d.key)).toEqual(['reception', 'internal']);
    });
    it('exposes only executable object destinations', () => {
        expect(transferDestinationsFromConfig({
            sales_queue: null,
            support_queue: {
                type: 'queue',
                target: 'support',
                description: 'Support queue',
                attended_allowed: true,
            },
            broken_array: [],
            broken_scalar: '1001',
        })).toEqual([{
            key: 'support_queue',
            type: 'queue',
            target: 'support',
            description: 'Support queue',
            attendedAllowed: true,
            liveAgent: false,
        }]);
    });
});
