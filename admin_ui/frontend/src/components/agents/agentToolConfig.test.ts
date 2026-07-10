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

    it('drops invalid known overrides before persistence', () => {
        const state = parseAgentConfig({
            provider: 'openai_realtime',
            extra_json: JSON.stringify({
                no_input: {
                    enabled: 'false',
                    initial_timeout_sec: null,
                    grace_timeout_sec: 5000,
                    max_check_ins: 1.5,
                    final_message: '   ',
                    future_option: 'preserved',
                },
            }),
        });

        state.noInput.initial_timeout_sec = Number.NaN;
        const serialized = serializeAgentConfig(state);
        const extra = JSON.parse(serialized.extra_json || '{}');
        expect(extra.no_input).toEqual({ future_option: 'preserved' });
    });

    it('drops prototype-pollution keys from unknown no_input passthrough', () => {
        const state = parseAgentConfig({
            provider: 'openai_realtime',
            extra_json: '{"no_input":{"__proto__":{"polluted":true},"constructor":{"polluted":true},"prototype":{"polluted":true},"future_option":"preserved"}}',
        });

        const serialized = serializeAgentConfig(state);
        const extra = JSON.parse(serialized.extra_json || '{}');
        expect(extra.no_input).toEqual({ future_option: 'preserved' });
        expect((Object.prototype as { polluted?: boolean }).polluted).toBeUndefined();
    });
});
