import { describe, expect, it } from 'vitest';

import { transferDestinationsFromConfig } from './agentToolInventory';


describe('Agent transfer destination inventory', () => {
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
