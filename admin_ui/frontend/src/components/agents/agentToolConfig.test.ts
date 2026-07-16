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

describe('per-agent connection audio configuration', () => {
    it('round-trips the caller-only media URI through extra_json', () => {
        const state = parseAgentConfig({
            provider: 'google_live',
            extra_json: JSON.stringify({
                connection_audio: 'tone:ring;tonezone=fr',
                customer_metadata: { region: 'west' },
            }),
        });

        expect(state.connectionAudio).toBe('tone:ring;tonezone=fr');

        const serialized = serializeAgentConfig(state);
        const extra = JSON.parse(serialized.extra_json || '{}');
        expect(extra.connection_audio).toBe('tone:ring;tonezone=fr');
        expect(extra.customer_metadata).toEqual({ region: 'west' });
    });

    it('omits connection_audio when ringback is disabled', () => {
        const state = parseAgentConfig({ provider: 'openai_realtime' });
        state.connectionAudio = '';

        const serialized = serializeAgentConfig(state);
        expect(serialized.extra_json).toBeNull();
    });
});

describe('per-agent transfer destination policy', () => {
    it('round-trips selected destination keys through the first-class column', () => {
        const state = parseAgentConfig({
            provider: 'openai_realtime',
            tool_configs_json: JSON.stringify({
                transfer: { destination_policy: 'selected', destination_keys: ['sales', 'support'] },
            }),
        });
        expect(state.transferDestinationPolicy).toBe('selected');
        expect(state.transferDestinationKeys).toEqual(['sales', 'support']);
        expect(JSON.parse(serializeAgentConfig(state).tool_configs_json || '{}')).toEqual({
            transfer: { destination_policy: 'selected', destination_keys: ['sales', 'support'] },
        });
    });

    it('omits inherited policy for backward compatibility', () => {
        const state = parseAgentConfig({ provider: 'deepgram' });
        expect(state.transferDestinationPolicy).toBe('inherit');
        expect(serializeAgentConfig(state).tool_configs_json).toBeNull();
    });

    it('stores selected-with-empty as an explicit fail-closed policy', () => {
        const state = parseAgentConfig({ provider: 'deepgram' });
        state.transferDestinationPolicy = 'selected';
        state.transferDestinationKeys = [];
        const stored = JSON.parse(serializeAgentConfig(state).tool_configs_json || '{}');
        expect(stored.transfer).toEqual({ destination_policy: 'selected', destination_keys: [] });
    });
});

describe('per-agent calendar and voicemail resource policies', () => {
    it('round-trips Google, Microsoft, and voicemail assignments together', () => {
        const state = parseAgentConfig({
            provider: 'openai_realtime',
            tool_configs_json: JSON.stringify({
                google_calendar: {
                    calendar_policy: 'selected',
                    calendar_keys: ['sales'],
                },
                microsoft_calendar: {
                    account_policy: 'selected',
                    account_keys: ['dispatch'],
                },
                voicemail: {
                    mailbox_policy: 'selected',
                    mailbox_key: 'support',
                },
            }),
        });

        expect(state.googleCalendarKeys).toEqual(['sales']);
        expect(state.microsoftAccountKeys).toEqual(['dispatch']);
        expect(state.voicemailMailboxKey).toBe('support');

        expect(JSON.parse(serializeAgentConfig(state).tool_configs_json || '{}')).toEqual({
            google_calendar: {
                calendar_policy: 'selected',
                calendar_keys: ['sales'],
            },
            microsoft_calendar: {
                account_policy: 'selected',
                account_keys: ['dispatch'],
            },
            voicemail: {
                mailbox_policy: 'selected',
                mailbox_key: 'support',
            },
        });
    });

    it('preserves an inherited transfer policy while storing calendar denial', () => {
        const state = parseAgentConfig({ provider: 'deepgram' });
        state.googleCalendarPolicy = 'none';
        const stored = JSON.parse(serializeAgentConfig(state).tool_configs_json || '{}');
        expect(stored).toEqual({
            google_calendar: { calendar_policy: 'none', calendar_keys: [] },
        });
    });
});
