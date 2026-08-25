import { useEffect, useState } from 'react';
import axios from 'axios';
import { AlertCircle, AlertTriangle, ChevronDown, ChevronRight, Info } from 'lucide-react';

/**
 * Audio alignment panel — the "one truth" view of the media path.
 *
 * The audio profile is the single edit point for the Asterisk wire contract;
 * provider cards own only the provider-native API boundary. This panel shows
 * the effective per-Agent chain the engine will resolve at call setup and
 * flags values that the resolution overrides or renegotiates (for example a
 * companded profile riding the AudioSocket slin carrier, or a provider rate
 * outside the adapter's supported set).
 */

type AlignmentFinding = {
    severity: 'error' | 'warning' | 'info';
    code: string;
    title: string;
    detail: string;
    scope: { agent?: string; profile?: string; provider?: string };
    agents?: string[];
};

type WireLeg = {
    encoding: string;
    sample_rate_hz: number;
    carrier?: boolean;
    declared_encoding?: string;
    declared?: boolean;
};

type AlignmentChain = {
    agent: string;
    profile: string;
    provider?: string | null;
    pipeline?: string | null;
    boundary_source: string;
    wire_out: WireLeg;
    wire_in: WireLeg;
    provider_boundary: {
        input_encoding: string;
        input_sample_rate_hz: number;
        output_encoding: string;
        output_sample_rate_hz: number;
    };
    internal_rate_hz: number;
};

type AlignmentReport = {
    audio_transport: string;
    default_profile: string;
    chains: AlignmentChain[];
    findings: AlignmentFinding[];
};

const legLabel = (leg: WireLeg): string => {
    const base = `${leg.encoding}@${leg.sample_rate_hz}`;
    if (leg.carrier && leg.declared_encoding && leg.declared_encoding !== leg.encoding) {
        return `${base} (carrier of ${leg.declared_encoding})`;
    }
    return base;
};

const severityStyles: Record<AlignmentFinding['severity'], { box: string; icon: JSX.Element }> = {
    error: {
        box: 'border-red-500/30 bg-red-500/10 text-red-800 dark:text-red-300',
        icon: <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />,
    },
    warning: {
        box: 'border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-300',
        icon: <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />,
    },
    info: {
        box: 'border-blue-500/30 bg-blue-500/10 text-blue-900 dark:text-blue-200',
        icon: <Info className="mt-0.5 h-4 w-4 shrink-0" />,
    },
};

interface AudioAlignmentPanelProps {
    /** Bump to re-fetch after a config save/reset. */
    refreshKey?: number;
}

const AudioAlignmentPanel: React.FC<AudioAlignmentPanelProps> = ({ refreshKey = 0 }) => {
    const [report, setReport] = useState<AlignmentReport | null>(null);
    const [showChains, setShowChains] = useState(false);

    useEffect(() => {
        let mounted = true;
        axios
            .get<AlignmentReport>('/api/config/audio/alignment')
            .then((response) => {
                if (mounted && response.data && Array.isArray(response.data.findings)) {
                    setReport(response.data);
                }
            })
            .catch(() => {
                // Advisory panel: stay silent when the endpoint is unavailable.
                if (mounted) setReport(null);
            });
        return () => {
            mounted = false;
        };
    }, [refreshKey]);

    if (!report || (report.findings.length === 0 && report.chains.length === 0)) {
        return null;
    }

    const counts = report.findings.reduce(
        (acc, finding) => {
            acc[finding.severity] += 1;
            return acc;
        },
        { error: 0, warning: 0, info: 0 } as Record<AlignmentFinding['severity'], number>,
    );

    return (
        <div className="rounded-lg border border-border bg-card/40 p-4 space-y-3">
            <div className="flex items-center justify-between gap-3 flex-wrap">
                <div>
                    <div className="text-sm font-semibold">Audio alignment</div>
                    <p className="text-xs text-muted-foreground mt-0.5">
                        The audio profile is the single edit point for the Asterisk wire; provider
                        cards own only the provider-native boundary. Resolved per call — transport:{' '}
                        <code>{report.audio_transport}</code>.
                    </p>
                </div>
                <div className="flex items-center gap-2 text-xs">
                    {counts.error > 0 && (
                        <span className="px-2 py-0.5 rounded-full bg-red-500/15 text-red-700 dark:text-red-300 font-semibold">
                            {counts.error} error{counts.error === 1 ? '' : 's'}
                        </span>
                    )}
                    {counts.warning > 0 && (
                        <span className="px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-300 font-semibold">
                            {counts.warning} warning{counts.warning === 1 ? '' : 's'}
                        </span>
                    )}
                    {counts.error === 0 && counts.warning === 0 && (
                        <span className="px-2 py-0.5 rounded-full bg-green-500/15 text-green-700 dark:text-green-300 font-semibold">
                            aligned
                        </span>
                    )}
                </div>
            </div>

            {report.findings.length > 0 && (
                <div className="space-y-2">
                    {report.findings.map((finding, index) => {
                        const style = severityStyles[finding.severity] || severityStyles.info;
                        const agents = finding.agents?.filter(Boolean) || [];
                        return (
                            <div
                                key={`${finding.code}-${index}`}
                                className={`flex items-start gap-2 rounded-md border p-3 text-sm ${style.box}`}
                            >
                                {style.icon}
                                <div className="min-w-0">
                                    <div className="font-medium break-words">{finding.title}</div>
                                    <p className="text-xs mt-1 opacity-90 break-words">{finding.detail}</p>
                                    {agents.length > 0 && (
                                        <div className="flex flex-wrap gap-1 mt-1.5">
                                            {agents.map((agent) => (
                                                <span
                                                    key={agent}
                                                    className="px-1.5 py-0.5 rounded bg-background/60 text-[11px] font-medium"
                                                >
                                                    {agent}
                                                </span>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}

            {report.chains.length > 0 && (
                <div>
                    <button
                        type="button"
                        onClick={() => setShowChains((current) => !current)}
                        className="flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
                    >
                        {showChains ? (
                            <ChevronDown className="w-3.5 h-3.5" />
                        ) : (
                            <ChevronRight className="w-3.5 h-3.5" />
                        )}
                        Effective audio chains ({report.chains.length})
                    </button>
                    {showChains && (
                        <div className="mt-2 overflow-x-auto">
                            <table className="w-full text-xs">
                                <thead>
                                    <tr className="text-left text-muted-foreground border-b border-border">
                                        <th className="py-1.5 pr-3 font-medium">Agent</th>
                                        <th className="py-1.5 pr-3 font-medium">Profile</th>
                                        <th className="py-1.5 pr-3 font-medium">Asterisk wire (in / out)</th>
                                        <th className="py-1.5 pr-3 font-medium">Provider boundary (in / out)</th>
                                        <th className="py-1.5 font-medium">Internal</th>
                                    </tr>
                                </thead>
                                <tbody className="font-mono">
                                    {report.chains.map((chain) => (
                                        <tr key={`${chain.agent}-${chain.profile}`} className="border-b border-border/50">
                                            <td className="py-1.5 pr-3 font-sans">{chain.agent}</td>
                                            <td className="py-1.5 pr-3">{chain.profile}</td>
                                            <td className="py-1.5 pr-3">
                                                {legLabel(chain.wire_in)} / {legLabel(chain.wire_out)}
                                            </td>
                                            <td className="py-1.5 pr-3">
                                                {chain.provider_boundary.input_encoding}@
                                                {chain.provider_boundary.input_sample_rate_hz} /{' '}
                                                {chain.provider_boundary.output_encoding}@
                                                {chain.provider_boundary.output_sample_rate_hz}
                                                <span className="font-sans text-muted-foreground">
                                                    {' '}· {chain.provider || chain.pipeline || '—'}
                                                </span>
                                            </td>
                                            <td className="py-1.5">{chain.internal_rate_hz} Hz</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export default AudioAlignmentPanel;
