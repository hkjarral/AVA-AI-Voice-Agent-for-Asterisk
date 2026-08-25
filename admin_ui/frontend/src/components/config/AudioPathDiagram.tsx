/**
 * AudioPathDiagram — the actual on-the-wire audio path for one Agent.
 *
 * Three nodes, two hops, both directions labeled with the exact format a call
 * will use, plus who decided each value:
 *
 *   [ Asterisk ] ══ transport wire ══ [ AI Engine ] ══ provider API ══ [ Provider ]
 *
 * The wire hop comes from the audio profile (the single edit point for the
 * Asterisk leg); the provider hop comes from the provider card intersected
 * with the adapter's capabilities (or the profile's provider_pref for
 * pipeline Agents). Agents only *select* a profile — they carry no audio
 * format settings of their own.
 */

export type AudioPathWireLeg = {
    encoding: string;
    sample_rate_hz: number;
    carrier?: boolean;
    declared_encoding?: string;
    declared?: boolean;
};

export type AudioPathChain = {
    agent: string;
    profile: string;
    profile_source?: 'agent' | 'default';
    provider?: string | null;
    provider_kind?: string | null;
    pipeline?: string | null;
    boundary_source: string;
    audio_transport: string;
    wire_out: AudioPathWireLeg;
    wire_in: AudioPathWireLeg;
    provider_boundary: {
        input_encoding: string;
        input_sample_rate_hz: number;
        output_encoding: string;
        output_sample_rate_hz: number;
    };
    internal_rate_hz: number;
    output_resampler?: string;
};

const legFormat = (leg: AudioPathWireLeg): string => `${leg.encoding}@${leg.sample_rate_hz}`;

const Node = ({ title, subtitle }: { title: string; subtitle: string }) => (
    <div className="shrink-0 rounded-lg border border-border bg-secondary/40 px-3 py-2 text-center min-w-[7.5rem]">
        <div className="text-sm font-semibold leading-tight">{title}</div>
        <div className="text-[11px] text-muted-foreground mt-0.5 leading-tight whitespace-pre-line">{subtitle}</div>
    </div>
);

const Hop = ({
    label,
    forward,
    backward,
    backwardNote,
    source,
}: {
    label: string;
    forward: string;
    backward: string;
    backwardNote?: string;
    source: string;
}) => (
    <div className="flex-1 min-w-[11rem] px-2 self-stretch flex flex-col justify-center">
        <div className="text-[11px] font-mono text-foreground/90 text-center">▶ {forward}</div>
        <div className="relative my-1">
            <div className="border-t border-border" />
            <span className="absolute left-1/2 -translate-x-1/2 -top-2 bg-card px-1.5 text-[10px] uppercase tracking-wider text-muted-foreground whitespace-nowrap">
                {label}
            </span>
        </div>
        <div className="text-[11px] font-mono text-foreground/90 text-center">
            ◀ {backward}
            {backwardNote && <span className="font-sans text-muted-foreground"> · {backwardNote}</span>}
        </div>
        <div className="text-[10px] text-muted-foreground text-center mt-1">{source}</div>
    </div>
);

const AudioPathDiagram: React.FC<{ chain: AudioPathChain }> = ({ chain }) => {
    const transportLabel =
        chain.audio_transport === 'externalmedia' ? 'RTP · ExternalMedia' : 'AudioSocket · TCP';
    const carrierOf =
        chain.wire_out.carrier &&
        chain.wire_out.declared_encoding &&
        chain.wire_out.declared_encoding !== chain.wire_out.encoding
            ? chain.wire_out.declared_encoding
            : null;

    const asteriskSubtitle = carrierOf
        ? `trunk: ${carrierOf}\n(transcodes ⇄ slin)`
        : 'trunk: negotiated\nby Asterisk';

    const boundarySourceLabel =
        chain.boundary_source === 'provider-wideband-capability'
            ? 'wideband route · adapter capabilities'
            : chain.boundary_source === 'provider'
                ? 'provider card ∩ capabilities'
                : 'profile provider_pref (pipeline)';

    const providerTitle = chain.provider || chain.pipeline || '—';
    const providerSubtitle = chain.pipeline && !chain.provider
        ? 'pipeline'
        : chain.provider_kind || 'provider';

    const wireSource = `profile ${chain.profile} · ${
        chain.profile_source === 'agent' ? 'set on Agent' : 'profiles.default'
    }`;

    return (
        <div className="overflow-x-auto">
            <div className="flex items-stretch min-w-[42rem] py-1">
                <div className="shrink-0 self-center pr-2 text-[11px] text-muted-foreground text-center leading-tight">
                    Caller<br />⇄
                </div>
                <Node title="Asterisk" subtitle={asteriskSubtitle} />
                <Hop
                    label={transportLabel}
                    forward={legFormat(chain.wire_in)}
                    backward={legFormat(chain.wire_out)}
                    backwardNote={carrierOf ? `carrier of ${carrierOf}` : undefined}
                    source={wireSource}
                />
                <Node
                    title="AI Engine"
                    subtitle={`${chain.internal_rate_hz} Hz internal\n${chain.output_resampler || 'linear'} resampler`}
                />
                <Hop
                    label="provider API"
                    forward={`${chain.provider_boundary.input_encoding}@${chain.provider_boundary.input_sample_rate_hz}`}
                    backward={`${chain.provider_boundary.output_encoding}@${chain.provider_boundary.output_sample_rate_hz}`}
                    source={boundarySourceLabel}
                />
                <Node title={providerTitle} subtitle={providerSubtitle} />
            </div>
            <div className="text-[10px] text-muted-foreground mt-0.5">
                ▶ caller → AI &nbsp;·&nbsp; ◀ AI → caller
            </div>
        </div>
    );
};

export default AudioPathDiagram;
