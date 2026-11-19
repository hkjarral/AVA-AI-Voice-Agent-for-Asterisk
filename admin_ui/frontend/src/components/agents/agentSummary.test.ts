import { expect, it } from 'vitest';
import { agentEngine, configuredTools } from './agentSummary';

it('summarizes all phases and inherited tools without disabled globals or duplicates', () => {
    expect(configuredTools({ tools_json: '["transfer","hangup_call"]', extra_json: JSON.stringify({
        pre_call_tools: ['lookup'], post_call_tools: ['summary'],
        in_call_http_tools: { lookup: { url: 'secret-not-displayed' } }, disable_global_in_call_tools: ['hangup_call'],
    }) }, [{ name: 'hangup_call', is_global: true }, { name: 'global_lookup', is_global: true, phase: 'pre_call' }]))
        .toEqual(['lookup', 'blind_transfer', 'summary', 'global_lookup']);
});
it('displays pipeline engines and tolerates malformed legacy data', () => {
    expect(agentEngine({ extra_json: '{"pipeline":"local"}' })).toBe('local · Pipeline');
    expect(configuredTools({ tools_json: 'bad', extra_json: 'bad' }, [])).toEqual([]);
});
