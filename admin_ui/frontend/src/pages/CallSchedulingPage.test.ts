import { describe, expect, it } from 'vitest';

import { buildCampaignEditPayload } from '../utils/outboundCampaign';

describe('buildCampaignEditPayload', () => {
    it('preserves legacy routing when the Agent selector is unchanged', () => {
        const payload = buildCampaignEditPayload(
            { name: 'Renamed campaign', default_context: 'legacy_sales' },
            'legacy_sales'
        );

        expect(payload).not.toHaveProperty('agent_routing_method');
    });

    it('preserves legacy routing for a partial edit without an Agent selector', () => {
        const payload = buildCampaignEditPayload(
            { name: 'Renamed campaign' },
            'legacy_sales'
        );

        expect(payload).not.toHaveProperty('agent_routing_method');
    });

    it('marks a changed Agent selector as canonical AI_AGENT routing', () => {
        const payload = buildCampaignEditPayload(
            { name: 'Campaign', default_context: 'sales' },
            'legacy_sales'
        );

        expect(payload.agent_routing_method).toBe('ai_agent');
    });
});
