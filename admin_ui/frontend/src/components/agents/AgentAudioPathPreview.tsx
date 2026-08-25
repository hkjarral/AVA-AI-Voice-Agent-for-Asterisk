import { useEffect, useState } from 'react';
import axios from 'axios';
import { AlertCircle, AlertTriangle } from 'lucide-react';
import AudioPathDiagram, { AudioPathChain } from '../config/AudioPathDiagram';

/**
 * Live audio-path preview for the Agent editor.
 *
 * An Agent carries no audio format settings of its own — it selects an audio
 * profile (or inherits profiles.default) and an AI engine. This preview asks
 * the alignment endpoint to resolve exactly that combination and shows the
 * wire/provider formats a call will actually use, updating as the operator
 * changes the selection.
 */

type PreviewFinding = {
    severity: 'error' | 'warning' | 'info';
    title: string;
};

interface AgentAudioPathPreviewProps {
    profile?: string;
    provider?: string;
    pipeline?: string;
}

const AgentAudioPathPreview: React.FC<AgentAudioPathPreviewProps> = ({
    profile,
    provider,
    pipeline,
}) => {
    const [chain, setChain] = useState<AudioPathChain | null>(null);
    const [findings, setFindings] = useState<PreviewFinding[]>([]);

    useEffect(() => {
        let mounted = true;
        const timer = setTimeout(() => {
            const params = new URLSearchParams({ preview: 'true' });
            if (profile) params.set('profile', profile);
            if (provider) params.set('provider', provider);
            if (pipeline) params.set('pipeline', pipeline);
            axios
                .get(`/api/config/audio/alignment?${params.toString()}`)
                .then((response) => {
                    if (!mounted) return;
                    const chains = response.data?.chains;
                    setChain(Array.isArray(chains) && chains.length > 0 ? chains[0] : null);
                    const rawFindings = response.data?.findings;
                    setFindings(
                        Array.isArray(rawFindings)
                            ? rawFindings
                                .filter((f: PreviewFinding) => f.severity !== 'info')
                                .map((f: PreviewFinding) => ({ severity: f.severity, title: f.title }))
                            : [],
                    );
                })
                .catch((err) => {
                    // Advisory preview: render nothing, but leave a diagnosable trace.
                    console.warn(
                        '[audio-alignment] agent preview hidden — endpoint unavailable',
                        err?.response?.status ?? err?.message,
                    );
                    if (mounted) setChain(null);
                });
        }, 250);
        return () => {
            mounted = false;
            clearTimeout(timer);
        };
    }, [profile, provider, pipeline]);

    if (!chain) return null;

    return (
        <div className="mb-4 rounded-md border border-border/70 bg-card/50 px-3 py-2">
            <div className="text-xs font-medium mb-1">
                Resulting audio path
                <span className="text-muted-foreground font-normal">
                    {' '}· profile <code>{chain.profile}</code>
                    {chain.profile_source === 'agent' ? '' : ' (profiles.default)'}
                </span>
            </div>
            <AudioPathDiagram chain={chain} />
            {findings.length > 0 && (
                <div className="mt-1.5 space-y-1">
                    {findings.map((finding, index) => (
                        <div
                            key={index}
                            className={`flex items-start gap-1.5 text-xs ${
                                finding.severity === 'error'
                                    ? 'text-red-700 dark:text-red-300'
                                    : 'text-amber-700 dark:text-amber-300'
                            }`}
                        >
                            {finding.severity === 'error' ? (
                                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                            ) : (
                                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                            )}
                            <span className="break-words">{finding.title}</span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};

export default AgentAudioPathPreview;
