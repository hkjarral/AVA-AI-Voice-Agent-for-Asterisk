import { useEffect, useState } from 'react';
import axios from 'axios';
import { AlertCircle, AlertTriangle, ChevronDown, ChevronRight, Info } from 'lucide-react';
import AudioPathDiagram, { AudioPathChain } from './AudioPathDiagram';

/**
 * Audio alignment panel — the "one truth" view of the media path.
 *
 * Shows, per Agent, the exact audio path a call will use (Asterisk wire →
 * engine → provider API, both directions, with the owner of every value) and
 * flags stored settings that the per-call resolution overrides or
 * renegotiates. The audio profile is the single edit point for the Asterisk
 * wire; provider cards own only the provider-native boundary; Agents merely
 * select a profile.
 */

type AlignmentFinding = {
    severity: 'error' | 'warning' | 'info';
    code: string;
    title: string;
    detail: string;
    scope: { agent?: string; profile?: string; provider?: string };
    agents?: string[];
};

type AlignmentReport = {
    audio_transport: string;
    default_profile: string;
    chains: AudioPathChain[];
    findings: AlignmentFinding[];
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

const VISIBLE_CHAINS = 3;

interface AudioAlignmentPanelProps {
    /** Bump to re-fetch after a config save/reset. */
    refreshKey?: number;
}

const AudioAlignmentPanel: React.FC<AudioAlignmentPanelProps> = ({ refreshKey = 0 }) => {
    const [report, setReport] = useState<AlignmentReport | null>(null);
    const [showAllChains, setShowAllChains] = useState(false);
    const [showFindings, setShowFindings] = useState(true);

    useEffect(() => {
        let mounted = true;
        axios
            .get<AlignmentReport>('/api/config/audio/alignment')
            .then((response) => {
                if (mounted && response.data && Array.isArray(response.data.findings)) {
                    setReport(response.data);
                }
            })
            .catch((err) => {
                // Advisory panel: render nothing, but leave a diagnosable trace —
                // a 404 means the Admin backend predates the endpoint, a 503
                // means the shared src/ tree is missing from the deploy.
                console.warn(
                    '[audio-alignment] panel hidden — endpoint unavailable',
                    err?.response?.status ?? err?.message,
                    err?.response?.data?.detail,
                );
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

    const chains = showAllChains ? report.chains : report.chains.slice(0, VISIBLE_CHAINS);
    const hiddenChains = report.chains.length - chains.length;

    return (
        <div className="rounded-lg border border-border bg-card/40 p-4 space-y-3">
            <div className="flex items-center justify-between gap-3 flex-wrap">
                <div>
                    <div className="text-sm font-semibold">Audio path — what actually goes on the wire</div>
                    <p className="text-xs text-muted-foreground mt-0.5">
                        Resolved per call. The audio profile owns the Asterisk wire; the provider card
                        owns only the provider API boundary; Agents just select a profile.
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

            {chains.length > 0 && (
                <div className="space-y-2">
                    {chains.map((chain) => (
                        <div
                            key={`${chain.agent}-${chain.profile}`}
                            className="rounded-md border border-border/70 bg-card px-3 py-2"
                        >
                            <div className="text-xs font-medium mb-1">
                                {chain.agent}
                                <span className="text-muted-foreground font-normal">
                                    {' '}· profile <code>{chain.profile}</code>
                                    {chain.profile_source === 'agent'
                                        ? ' (set on Agent)'
                                        : ' (profiles.default)'}
                                </span>
                            </div>
                            <AudioPathDiagram chain={chain} />
                        </div>
                    ))}
                    {hiddenChains > 0 && (
                        <button
                            type="button"
                            onClick={() => setShowAllChains(true)}
                            className="text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
                        >
                            Show {hiddenChains} more Agent{hiddenChains === 1 ? '' : 's'}…
                        </button>
                    )}
                </div>
            )}

            {report.findings.length > 0 && (
                <div>
                    <button
                        type="button"
                        onClick={() => setShowFindings((current) => !current)}
                        className="flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
                    >
                        {showFindings ? (
                            <ChevronDown className="w-3.5 h-3.5" />
                        ) : (
                            <ChevronRight className="w-3.5 h-3.5" />
                        )}
                        Findings ({report.findings.length})
                    </button>
                    {showFindings && (
                        <div className="space-y-2 mt-2">
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
                </div>
            )}
        </div>
    );
};

export default AudioAlignmentPanel;
