import { describe, it, expect } from 'vitest';
import { parseToolReferences, buildInCallStatusMap, canonicalToolName, type InCallEnablement } from './promptTools';

const known = ['book_appointment', 'get_weather', 'transfer', 'lookup'];

describe('parseToolReferences', () => {
    it('matches a known tool name followed by the "tool" keyword', () => {
        const refs = parseToolReferences('Use the book_appointment tool to schedule.', known);
        expect(refs).toEqual([{ name: 'book_appointment', start: 8, end: 24 }]);
    });

    it('matches a known tool name preceded by the keyword', () => {
        const refs = parseToolReferences('Call the tool get_weather first.', known);
        expect(refs.map((r) => r.name)).toEqual(['get_weather']);
        const r = refs[0];
        expect('Call the tool get_weather first.'.slice(r.start, r.end)).toBe('get_weather');
    });

    it('matches "function" and plural keywords', () => {
        expect(parseToolReferences('the get_weather function', known).map((r) => r.name)).toEqual(['get_weather']);
        expect(parseToolReferences('the lookup tools available', known).map((r) => r.name)).toEqual(['lookup']);
        expect(parseToolReferences('available functions: lookup function', known).map((r) => r.name)).toEqual(['lookup']);
    });

    it('does NOT match a tool name without an adjacent keyword', () => {
        expect(parseToolReferences('Use book_appointment to schedule.', known)).toEqual([]);
    });

    it('does NOT match a common-word tool name in ordinary prose', () => {
        expect(parseToolReferences('Please transfer the call to a human.', known)).toEqual([]);
        expect(parseToolReferences('Use the transfer tool when asked.', known).map((r) => r.name)).toEqual(['transfer']);
    });

    it('respects word boundaries (toolkit / functional do not count as keywords)', () => {
        expect(parseToolReferences('the book_appointment toolkit', known)).toEqual([]);
    });

    it('only matches names in the known set', () => {
        expect(parseToolReferences('use the magic tool', known)).toEqual([]);
    });

    it('is case-insensitive on the keyword', () => {
        expect(parseToolReferences('the get_weather Tool', known).map((r) => r.name)).toEqual(['get_weather']);
    });

    it('detects a backticked or quoted tool name next to the keyword', () => {
        expect(parseToolReferences('use the `book_appointment` tool', known).map((r) => r.name)).toEqual(['book_appointment']);
        expect(parseToolReferences('call the "get_weather" function', known).map((r) => r.name)).toEqual(['get_weather']);
        expect(parseToolReferences("the tool 'lookup'", known).map((r) => r.name)).toEqual(['lookup']);
        // the colored span is the bare name, not the quotes
        const r = parseToolReferences('use the `book_appointment` tool', known)[0];
        expect('use the `book_appointment` tool'.slice(r.start, r.end)).toBe('book_appointment');
    });

    it('finds multiple references in order', () => {
        const refs = parseToolReferences('use book_appointment tool and the lookup function', known);
        expect(refs.map((r) => r.name)).toEqual(['book_appointment', 'lookup']);
    });
});

describe('canonicalToolName', () => {
    it('maps the legacy transfer alias to blind_transfer', () => {
        expect(canonicalToolName('transfer')).toBe('blind_transfer');
    });
    it('is identity for non-aliased names', () => {
        expect(canonicalToolName('book_appointment')).toBe('book_appointment');
    });
});

describe('buildInCallStatusMap', () => {
    const catalog = [
        { name: 'book_appointment', phase: 'in_call', is_global: false },
        { name: 'get_weather', phase: 'in_call', is_global: true },
        { name: 'send_receipt', phase: 'post_call', is_global: false },
        { name: 'lookup_caller', phase: 'in_call', is_global: true },
        { name: 'do_thing', phase: 'in_call', is_global: false },
    ];
    const enablement: InCallEnablement = {
        explicitlyAdded: (n) => ['book_appointment'].includes(n),
        globalDisabledHere: (n) => ['lookup_caller'].includes(n),
        globallyDisabled: (n) => ['do_thing'].includes(n),
    };

    it('marks an explicitly-added in-call tool as context', () => {
        expect(buildInCallStatusMap(catalog, enablement).book_appointment).toBe('context');
    });
    it('marks an active global in-call tool as global', () => {
        expect(buildInCallStatusMap(catalog, enablement).get_weather).toBe('global');
    });
    it('marks a global tool disabled for this entity as unavailable', () => {
        expect(buildInCallStatusMap(catalog, enablement).lookup_caller).toBe('unavailable');
    });
    it('marks a globally-disabled tool as unavailable', () => {
        expect(buildInCallStatusMap(catalog, enablement).do_thing).toBe('unavailable');
    });
    it('marks a non-in-call tool as unavailable (wrong phase for the in-call prompt)', () => {
        expect(buildInCallStatusMap(catalog, enablement).send_receipt).toBe('unavailable');
    });
});
