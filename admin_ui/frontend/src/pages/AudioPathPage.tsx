import { useState } from 'react';
import { RefreshCw } from 'lucide-react';
import AudioAlignmentPanel from '../components/config/AudioAlignmentPanel';

/**
 * Audio Path — the dedicated view of what actually goes on the wire.
 *
 * One card per Agent: Asterisk ═ transport wire ═ AI Engine ═ provider API ═
 * Provider (monolithic or pipeline), both directions labeled with the exact
 * negotiated format and the owner of each value, plus alignment findings.
 * Read-only: edit the wire contract on Audio Profiles, the provider-native
 * boundary on Providers, and the Agent's profile selection on Agents.
 */
const AudioPathPage = () => {
    const [refreshKey, setRefreshKey] = useState(0);

    return (
        <div className="space-y-6">
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Audio Path</h1>
                    <p className="text-muted-foreground mt-1">
                        The effective media chain per Agent — exactly what a call puts on each leg,
                        and which setting decides it.
                    </p>
                </div>
                <button
                    onClick={() => setRefreshKey((current) => current + 1)}
                    className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2"
                >
                    <RefreshCw className="w-4 h-4 mr-2" />
                    Refresh
                </button>
            </div>

            <AudioAlignmentPanel refreshKey={refreshKey} />

            <div className="text-xs text-muted-foreground">
                Where to edit: the Asterisk wire contract lives on the Agent's{' '}
                <a href="/profiles" className="text-primary hover:underline">Audio Profile</a>; the
                provider-native API boundary lives on the{' '}
                <a href="/providers" className="text-primary hover:underline">Provider card</a>{' '}
                (capability-checked); an{' '}
                <a href="/agents" className="text-primary hover:underline">Agent</a> only selects a
                profile and an AI engine — it carries no audio format settings of its own.
            </div>
        </div>
    );
};

export default AudioPathPage;
