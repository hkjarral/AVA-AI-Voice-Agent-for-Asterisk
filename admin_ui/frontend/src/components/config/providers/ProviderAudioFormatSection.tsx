import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { FormInput, FormSelect } from '../../ui/FormComponents';

/**
 * Editable provider audio-format fields with server-backed defaults.
 *
 * Displays the canonical baseline (Providers → audio contract) for every field
 * the provider kind supports, even when the value is not present in the YAML
 * config. Values equal to the baseline are removed from the config on change
 * (patched as `undefined`), so shipped defaults never accumulate in
 * `ai-agent.yaml` — only real overrides are stored.
 */

export type ProviderAudioBaseline = Record<string, string | number>;

type BaselineMap = Record<string, ProviderAudioBaseline>;

let cachedBaselines: BaselineMap | null = null;
let inflightBaselines: Promise<BaselineMap | null> | null = null;

export const fetchProviderAudioBaselines = async (): Promise<BaselineMap | null> => {
    if (cachedBaselines) return cachedBaselines;
    if (!inflightBaselines) {
        inflightBaselines = axios
            .get<{ provider_baselines?: BaselineMap }>('/api/config/providers/audio/baselines')
            .then((response) => {
                const baselines = response.data?.provider_baselines;
                if (baselines && typeof baselines === 'object') {
                    cachedBaselines = baselines;
                }
                return cachedBaselines;
            })
            .catch(() => null)
            .finally(() => {
                inflightBaselines = null;
            });
    }
    return inflightBaselines;
};

// G.711 codecs are fixed at 8 kHz; any other rate breaks the stream.
const G711_TOKENS = new Set(['ulaw', 'mulaw', 'mu-law', 'alaw', 'a-law', 'g711_ulaw', 'g711_alaw']);

const g711FixedRate = (encoding: unknown): number | undefined =>
    G711_TOKENS.has(String(encoding || '').toLowerCase()) ? 8000 : undefined;

const ENCODING_LABELS: Record<string, string> = {
    ulaw: 'μ-law — G.711, 8 kHz',
    mulaw: 'μ-law — G.711, 8 kHz',
    'mu-law': 'μ-law — G.711, 8 kHz',
    alaw: 'A-law — G.711, 8 kHz',
    'a-law': 'A-law — G.711, 8 kHz',
    pcm16: 'PCM16',
    linear16: 'Linear16',
    slin: 'SLIN — PCM16, 8 kHz',
    slin16: 'SLIN16 — PCM16, 16 kHz',
};

const encodingOptions = (baselineValue?: unknown, currentValue?: unknown) => {
    // Keep the kind's own μ-law spelling ("ulaw" vs "mulaw") so an unchanged
    // selection round-trips to the exact baseline token and stays out of YAML.
    const baseline = String(baselineValue || '').toLowerCase();
    const muToken = baseline === 'mulaw' || baseline === 'mu-law' ? baseline : 'ulaw';
    const values: string[] = [muToken, 'alaw', 'pcm16', 'linear16'];
    [baselineValue, currentValue].forEach((value) => {
        const token = String(value || '');
        if (token && !values.includes(token)) values.push(token);
    });
    return values.map((value) => ({
        value,
        label: ENCODING_LABELS[value.toLowerCase()] || value,
    }));
};

type FieldPair = {
    encodingField: string;
    rateField: string;
    encodingLabel: string;
    rateLabel: string;
    encodingTooltip?: string;
    rateTooltip?: string;
    /**
     * Wire-facing pair: the engine derives these values from the Agent's audio
     * profile at call setup, so the stored value is only a legacy fallback.
     * Rendered with a note steering edits to the Audio Profile instead.
     */
    wireFacing?: boolean;
};

interface ProviderAudioFormatSectionProps {
    /** Provider kind key in the canonical audio baseline registry. */
    kind: string;
    /** Current provider config (YAML-backed form state). */
    config: Record<string, unknown>;
    /**
     * Patch callback: keys set to `undefined` must be REMOVED from the form
     * state (ProvidersPage's updateForm implements this contract).
     */
    onPatch: (patch: Record<string, unknown>) => void;
    /**
     * Deterministic fallback shown until the server baselines load (and if the
     * request fails). Keep it equal to the canonical registry values.
     */
    fallbackBaseline: ProviderAudioBaseline;
    pairs: FieldPair[];
    title?: string;
}

const ProviderAudioFormatSection: React.FC<ProviderAudioFormatSectionProps> = ({
    kind,
    config,
    onPatch,
    fallbackBaseline,
    pairs,
    title = 'Audio Format',
}) => {
    const [serverBaseline, setServerBaseline] = useState<ProviderAudioBaseline | null>(
        () => cachedBaselines?.[kind] ?? null,
    );

    useEffect(() => {
        let mounted = true;
        fetchProviderAudioBaselines().then((baselines) => {
            if (mounted && baselines?.[kind]) {
                setServerBaseline(baselines[kind]);
            }
        });
        return () => {
            mounted = false;
        };
    }, [kind]);

    const baseline: ProviderAudioBaseline = { ...fallbackBaseline, ...(serverBaseline || {}) };

    const effectiveValue = (field: string): unknown =>
        config[field] !== undefined && config[field] !== null && config[field] !== ''
            ? config[field]
            : baseline[field];

    const patchField = (field: string, value: string | number) => {
        // Values equal to the shipped baseline live in code, not in YAML.
        onPatch({ [field]: value === baseline[field] ? undefined : value });
    };

    const handleEncodingChange = (pair: FieldPair, encoding: string) => {
        const patch: Record<string, unknown> = {
            [pair.encodingField]: encoding === baseline[pair.encodingField] ? undefined : encoding,
        };
        const locked = g711FixedRate(encoding);
        if (locked !== undefined) {
            patch[pair.rateField] = locked === baseline[pair.rateField] ? undefined : locked;
        }
        onPatch(patch);
    };

    return (
        <div>
            <h4 className="font-semibold mb-1">{title}</h4>
            <p className="text-xs text-muted-foreground mb-3">
                Values matching the provider default are shown here but not written to the config —
                only overrides are stored. G.711 codecs (μ-law/A-law) lock their rate to 8000 Hz.
            </p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {pairs.map((pair) => {
                    const encoding = String(effectiveValue(pair.encodingField) ?? '');
                    const lockedRate = g711FixedRate(encoding);
                    const effectiveRate = effectiveValue(pair.rateField);
                    const rateValue = lockedRate
                        ?? (typeof effectiveRate === 'number' || typeof effectiveRate === 'string'
                            ? effectiveRate
                            : 8000);
                    const encodingOverridden = config[pair.encodingField] !== undefined
                        && config[pair.encodingField] !== baseline[pair.encodingField];
                    const rateOverridden = config[pair.rateField] !== undefined
                        && config[pair.rateField] !== baseline[pair.rateField];
                    return (
                        <React.Fragment key={pair.encodingField}>
                            <div>
                                <FormSelect
                                    label={pair.encodingLabel}
                                    value={encoding}
                                    onChange={(e) => handleEncodingChange(pair, e.target.value)}
                                    options={encodingOptions(baseline[pair.encodingField], config[pair.encodingField])}
                                    tooltip={pair.encodingTooltip}
                                />
                                {pair.wireFacing && (
                                    <p className="text-xs text-muted-foreground -mt-3 mb-2">
                                        Wire-facing — the Agent's audio profile decides this at call
                                        setup; the value here is only a fallback.
                                    </p>
                                )}
                                {encodingOverridden && !pair.wireFacing && (
                                    <p className="text-xs text-muted-foreground -mt-3 mb-2">
                                        Overrides default: {String(baseline[pair.encodingField] ?? '—')}
                                    </p>
                                )}
                            </div>
                            <div>
                                <FormInput
                                    label={pair.rateLabel}
                                    type="number"
                                    disabled={lockedRate !== undefined}
                                    value={rateValue}
                                    onChange={(e) => {
                                        const parsed = parseInt(e.target.value, 10);
                                        if (!Number.isNaN(parsed)) {
                                            patchField(pair.rateField, parsed);
                                        }
                                    }}
                                    tooltip={pair.rateTooltip}
                                />
                                {rateOverridden && lockedRate === undefined && (
                                    <p className="text-xs text-muted-foreground -mt-3 mb-2">
                                        Overrides default: {String(baseline[pair.rateField] ?? '—')} Hz
                                    </p>
                                )}
                            </div>
                        </React.Fragment>
                    );
                })}
            </div>
        </div>
    );
};

export default ProviderAudioFormatSection;
