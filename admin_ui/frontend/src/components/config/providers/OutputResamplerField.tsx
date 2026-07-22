import React from 'react';
import HelpTooltip from '../../ui/HelpTooltip';

interface OutputResamplerFieldProps {
    value?: string;
    onChange: (value: 'inherit' | 'linear' | 'bandlimited') => void;
    sourceRate?: number;
    targetRate?: number;
}

const OutputResamplerField: React.FC<OutputResamplerFieldProps> = ({
    value,
    onChange,
    sourceRate,
    targetRate,
}) => {
    const downsampling = Number(sourceRate || 0) > Number(targetRate || 0);

    return (
        <div className="space-y-2">
            <div className="flex items-center gap-1.5">
                <label className="text-sm font-medium">Output Downsampling</label>
                <HelpTooltip
                    content={
                        <>
                            Controls conversion from provider-native audio to the lower Asterisk target rate.
                            <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                <li><strong>Inherit</strong> uses the Agent's Audio Profile.</li>
                                <li><strong>Compatibility</strong> preserves the existing linear converter.</li>
                                <li><strong>Alias-safe</strong> removes frequencies that cannot fit in 8 kHz telephony before downsampling, reducing “shh/sss” artifacts.</li>
                                <li>It has no effect when source and target rates already match.</li>
                            </ul>
                        </>
                    }
                />
            </div>
            <select
                className="w-full p-2 rounded border border-input bg-background"
                value={value || 'inherit'}
                onChange={(event) => onChange(event.target.value as 'inherit' | 'linear' | 'bandlimited')}
            >
                <option value="inherit">Inherit from Audio Profile (recommended)</option>
                <option value="linear">Compatibility (current behavior)</option>
                <option value="bandlimited">Alias-safe (recommended for 16/24 kHz → 8 kHz)</option>
            </select>
            <p className="text-xs text-muted-foreground">
                {downsampling
                    ? `Active conversion path: ${sourceRate} Hz → ${targetRate} Hz.`
                    : 'No rate reduction is configured, so this setting is currently a no-op.'}
            </p>
        </div>
    );
};

export default OutputResamplerField;
