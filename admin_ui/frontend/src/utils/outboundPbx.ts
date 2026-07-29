export interface OutboundPbxTypeOption {
    value: string;
    label: string;
}

export const normalizeOutboundPbxType = (value?: string): string =>
    (value || '').trim().toLowerCase() || 'freepbx';

export const getOutboundPbxTypeOptions = (currentValue?: string): OutboundPbxTypeOption[] => {
    const options: OutboundPbxTypeOption[] = [
        { value: 'freepbx', label: 'FreePBX' },
        { value: 'generic', label: 'Generic Asterisk' },
    ];

    if (normalizeOutboundPbxType(currentValue) === 'vicidial') {
        options.push({
            value: 'vicidial',
            label: 'VICIdial (legacy — migrate to Remote Agents)',
        });
    }

    return options;
};
