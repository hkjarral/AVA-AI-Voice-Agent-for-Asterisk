import HelpTooltip from '../../ui/HelpTooltip';

interface RecoveryConfig {
    session_resumption_enabled?: boolean;
    context_window_compression_enabled?: boolean;
    session_recovery_timeout_sec?: number;
    session_recovery_max_attempts?: number;
    session_recovery_audio_buffer_ms?: number;
}

interface Props {
    config: RecoveryConfig;
    onChange: (field: string, value: boolean | number) => void;
}

export default function GoogleSessionRecoveryFields({ config, onChange }: Props) {
    return (
        <fieldset className="space-y-3 rounded-md border border-input p-3">
            <legend className="px-1 text-sm font-semibold">Long calls and connection recovery</legend>
            <div className="flex items-center gap-2">
                <input id="session_resumption_enabled" type="checkbox"
                    checked={config.session_resumption_enabled ?? false}
                    onChange={(event) => onChange('session_resumption_enabled', event.target.checked)} />
                <label htmlFor="session_resumption_enabled" className="text-sm">Keep long calls connected</label>
                <HelpTooltip content="Resume the same conversation when Google replaces its connection or a recoverable disconnect occurs. Requires Google to retain session data; leave off for deployments requiring zero data retention. Recovery requires a safe checkpoint and may fail during speech or tool execution." />
            </div>
            <div className="flex items-center gap-2">
                <input id="context_window_compression_enabled" type="checkbox"
                    checked={config.context_window_compression_enabled ?? false}
                    onChange={(event) => onChange('context_window_compression_enabled', event.target.checked)} />
                <label htmlFor="context_window_compression_enabled" className="text-sm">Compress long conversation history</label>
                <HelpTooltip content="Allow conversations to continue beyond the model's context limit. Google can discard older turns, so the model may forget earlier details. This setting is independent of connection recovery." />
            </div>
            {config.session_resumption_enabled && (
                <div className="grid gap-3 sm:grid-cols-3">
                    <div>
                        <label htmlFor="session_recovery_timeout_sec" className="text-xs">Recovery time limit (seconds)</label>
                        <input id="session_recovery_timeout_sec" type="number" min={0.1} max={30} step={0.1}
                            className="mt-1 w-full rounded-md border border-input bg-background p-2"
                            value={config.session_recovery_timeout_sec ?? 10}
                            onChange={(event) => onChange('session_recovery_timeout_sec', event.target.value ? Number(event.target.value) : 10)} />
                    </div>
                    <div>
                        <label htmlFor="session_recovery_max_attempts" className="text-xs">Maximum attempts</label>
                        <input id="session_recovery_max_attempts" type="number" min={1} max={3}
                            className="mt-1 w-full rounded-md border border-input bg-background p-2"
                            value={config.session_recovery_max_attempts ?? 2}
                            onChange={(event) => onChange('session_recovery_max_attempts', event.target.value ? Number(event.target.value) : 2)} />
                    </div>
                    <div>
                        <label htmlFor="session_recovery_audio_buffer_ms" className="text-xs">Caller audio buffer (milliseconds)</label>
                        <input id="session_recovery_audio_buffer_ms" type="number" min={0} max={2000} step={20}
                            className="mt-1 w-full rounded-md border border-input bg-background p-2"
                            value={config.session_recovery_audio_buffer_ms ?? 1000}
                            onChange={(event) => onChange('session_recovery_audio_buffer_ms', event.target.value ? Number(event.target.value) : 1000)} />
                    </div>
                </div>
            )}
            <p className="text-xs text-muted-foreground">
                Both options are off by default. Save, then restart the AI Engine. A short pause or lost speech
                is possible during recovery; older audio beyond the buffer is dropped.
            </p>
        </fieldset>
    );
}
