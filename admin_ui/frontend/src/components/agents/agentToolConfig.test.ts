import { describe, expect, it } from 'vitest';

import { parseAgentConfig, serializeAgentConfig } from './agentToolConfig';


describe('per-agent no-input configuration', () => {
    it('round-trips no_input overrides without clobbering unknown extra fields', () => {
        const state = parseAgentConfig({
            provider: 'openai_realtime',
            extra_json: JSON.stringify({
                no_input: {
                    enabled: true,
                    outbound_enabled: true,
                    initial_timeout_sec: 45,
                },
                customer_metadata: { region: 'west' },
            }),
        });

        expect(state.noInput).toEqual({
            enabled: true,
            outbound_enabled: true,
            initial_timeout_sec: 45,
        });

        const serialized = serializeAgentConfig(state);
        const extra = JSON.parse(serialized.extra_json || '{}');
        expect(extra.no_input).toEqual(state.noInput);
        expect(extra.customer_metadata).toEqual({ region: 'west' });
    });

    it('omits an empty override so the agent inherits global inbound defaults', () => {
        const state = parseAgentConfig({ provider: 'deepgram' });
        const serialized = serializeAgentConfig(state);
        expect(serialized.extra_json).toBeNull();
    });
});
