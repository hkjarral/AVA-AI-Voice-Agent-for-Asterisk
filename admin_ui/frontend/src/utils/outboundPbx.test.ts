import { describe, expect, it } from 'vitest';

import { getOutboundPbxTypeOptions, normalizeOutboundPbxType } from './outboundPbx';

describe('getOutboundPbxTypeOptions', () => {
    it('does not offer the deprecated VICIdial mode to new configurations', () => {
        expect(getOutboundPbxTypeOptions()).toEqual([
            { value: 'freepbx', label: 'FreePBX' },
            { value: 'generic', label: 'Generic Asterisk' },
        ]);
    });

    it('keeps the legacy value visible long enough to migrate an existing configuration', () => {
        expect(getOutboundPbxTypeOptions(' VICIDIAL ')).toContainEqual({
            value: 'vicidial',
            label: 'VICIdial (legacy — migrate to Remote Agents)',
        });
    });

    it('normalizes legacy casing and whitespace for controlled values and saves', () => {
        expect(normalizeOutboundPbxType(' VICIDIAL ')).toBe('vicidial');
        expect(normalizeOutboundPbxType(' Generic ')).toBe('generic');
        expect(normalizeOutboundPbxType()).toBe('freepbx');
    });
});
