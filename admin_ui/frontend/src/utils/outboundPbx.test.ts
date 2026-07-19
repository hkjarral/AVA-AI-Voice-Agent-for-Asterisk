import { describe, expect, it } from 'vitest';

import { getOutboundPbxTypeOptions } from './outboundPbx';

describe('getOutboundPbxTypeOptions', () => {
    it('does not offer the deprecated VICIdial mode to new configurations', () => {
        expect(getOutboundPbxTypeOptions()).toEqual([
            { value: 'freepbx', label: 'FreePBX' },
            { value: 'generic', label: 'Generic Asterisk' },
        ]);
    });

    it('keeps the legacy value visible long enough to migrate an existing configuration', () => {
        expect(getOutboundPbxTypeOptions('vicidial')).toContainEqual({
            value: 'vicidial',
            label: 'VICIdial (legacy — migrate to Remote Agents)',
        });
    });
});
