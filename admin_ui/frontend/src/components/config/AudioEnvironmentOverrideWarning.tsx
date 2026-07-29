import { useEffect, useState } from 'react';
import axios from 'axios';
import { AlertTriangle } from 'lucide-react';
import { Link } from 'react-router-dom';

const AUDIO_RESAMPLER_ENV_KEYS = [
    'AAVA_OPENAI_OUTPUT_RESAMPLER',
    'AAVA_GOOGLE_OUTPUT_RESAMPLER',
    'AAVA_GROK_OUTPUT_RESAMPLER',
    'AAVA_ELEVENLABS_OUTPUT_RESAMPLER',
] as const;

const AudioEnvironmentOverrideWarning = () => {
    const [overrides, setOverrides] = useState<Array<[string, string]>>([]);

    useEffect(() => {
        let active = true;
        axios.get('/api/config/env').then((response) => {
            if (!active) return;
            const env = response.data || {};
            const activeOverrides = AUDIO_RESAMPLER_ENV_KEYS
                .map((key) => [key, String(env[key] || '').trim()] as [string, string])
                .filter(([, value]) => value.length > 0);
            if (activeOverrides.length > 0) setOverrides(activeOverrides);
        }).catch(() => {
            // Best-effort warning. Configuration editing remains available if
            // the environment endpoint is unavailable on an older deployment.
        });
        return () => {
            active = false;
        };
    }, []);

    if (overrides.length === 0) return null;

    return (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-4 text-amber-800 dark:text-amber-300">
            <div className="flex items-start gap-2">
                <AlertTriangle className="w-5 h-5 mt-0.5 flex-shrink-0" />
                <div className="space-y-2 text-sm">
                    <div className="font-semibold">Environment audio override active</div>
                    <p>
                        These values take precedence over provider, pipeline, and profile resampler settings. Restore actions do not remove them.
                    </p>
                    <div className="flex flex-wrap gap-2 font-mono text-xs">
                        {overrides.map(([key, value]) => (
                            <code key={key} className="rounded bg-background/70 px-2 py-1">
                                {key}={value}
                            </code>
                        ))}
                    </div>
                    <p>
                        Review or remove them under <Link to="/env" className="font-medium underline underline-offset-2">System → Environment</Link>.
                    </p>
                </div>
            </div>
        </div>
    );
};

export default AudioEnvironmentOverrideWarning;
