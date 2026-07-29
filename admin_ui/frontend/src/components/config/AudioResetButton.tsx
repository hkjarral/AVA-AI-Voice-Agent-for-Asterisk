import { useState } from 'react';
import axios from 'axios';
import { Loader2, RotateCcw } from 'lucide-react';
import { toast } from 'sonner';

import { useConfirmDialog } from '../../hooks/useConfirmDialog';

export type AudioResetScope = 'provider' | 'profile' | 'pipeline';
export interface AudioResetResponse {
    recommended_apply_method?: 'none' | 'hot_reload' | 'restart';
    [key: string]: unknown;
}

interface AudioResetButtonProps {
    scope: AudioResetScope;
    target: string;
    onResetComplete: (response: AudioResetResponse) => void | Promise<void>;
    customProfile?: boolean;
    compact?: boolean;
    className?: string;
}

const labels: Record<AudioResetScope, string> = {
    provider: 'Restore audio defaults',
    profile: 'Restore profile baseline',
    pipeline: 'Restore audio defaults',
};

const confirmationCopy = (
    scope: AudioResetScope,
    target: string,
    customProfile: boolean | undefined,
): string => {
    if (scope === 'provider') {
        return [
            `Restore the shipped audio settings for provider "${target}"?`,
            '',
            'Encoding, sample-rate, target-format, and resampler overrides will return to the provider baseline. Credentials, models, voices, prompts, enabled state, and provider identity are preserved.',
            '',
            'Any unsaved edits currently shown in the provider editor will be discarded.',
            '',
            'The change applies to new calls after the recommended apply action. Environment resampler overrides are not changed.',
        ].join('\n');
    }
    if (scope === 'profile') {
        const baseline = customProfile === true
            ? 'This custom profile will copy the standard 8 kHz telephony baseline while keeping its current name.'
            : customProfile === false
                ? 'This built-in profile will return to its shipped baseline.'
                : 'The server will restore the canonical baseline available for this profile.';
        return [
            `Restore the audio baseline for profile "${target}"?`,
            '',
            baseline,
            'Agent assignments and other profiles are not changed.',
            '',
            'Any unsaved edits currently shown in the profile editor will be discarded.',
            '',
            'The change applies to new calls after the recommended apply action. Environment resampler overrides are not changed.',
        ].join('\n');
    }
    return [
        `Restore inherited audio behavior for pipeline "${target}"?`,
        '',
        'Pipeline-level audio format, sample-rate, and resampler overrides will be removed or restored to the shipped baseline. STT, LLM, and TTS provider selections and non-audio options are preserved.',
        '',
        'Any unsaved edits currently shown in the pipeline editor will be discarded.',
        '',
        'The change applies to new calls after the recommended apply action. Environment resampler overrides are not changed.',
    ].join('\n');
};

const errorDetail = (error: unknown): string => {
    const resetError = error as {
        response?: { data?: { detail?: unknown } };
        message?: string;
    };
    const detail = resetError?.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
        return detail
            .map((entry: unknown) => {
                if (entry && typeof entry === 'object') {
                    const item = entry as { msg?: string; message?: string };
                    return item.msg || item.message || JSON.stringify(entry);
                }
                return String(entry);
            })
            .join('; ');
    }
    if (detail && typeof detail === 'object') return JSON.stringify(detail);
    return resetError?.message || 'Unknown error';
};

const AudioResetButton = ({
    scope,
    target,
    onResetComplete,
    customProfile,
    compact = false,
    className = '',
}: AudioResetButtonProps) => {
    const { confirm } = useConfirmDialog();
    const [resetting, setResetting] = useState(false);
    const label = labels[scope];

    const handleReset = async (event: React.MouseEvent<HTMLButtonElement>) => {
        event.stopPropagation();
        const confirmed = await confirm({
            title: `${label}?`,
            description: confirmationCopy(scope, target, customProfile),
            confirmText: 'Restore',
            variant: 'default',
        });
        if (!confirmed) return;

        setResetting(true);
        try {
            const response = await axios.post(
                `/api/config/${scope}s/${encodeURIComponent(target)}/audio/reset`,
            );
            await onResetComplete(response.data || {});
            const method = (response.data || {}).recommended_apply_method;
            toast.success(label, {
                description: method === 'none'
                    ? `Audio settings for "${target}" were restored and are already active.`
                    : `Audio settings for "${target}" were restored. Review the apply banner before placing new calls.`,
            });
        } catch (error) {
            toast.error(`Failed to restore audio settings for "${target}"`, {
                description: errorDetail(error),
            });
        } finally {
            setResetting(false);
        }
    };

    if (compact) {
        return (
            <button
                type="button"
                onClick={handleReset}
                disabled={resetting}
                aria-label={`${label} for ${target}`}
                title={label}
                className={`p-2 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground disabled:opacity-50 ${className}`}
            >
                {resetting ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                    <RotateCcw className="w-4 h-4" />
                )}
            </button>
        );
    }

    return (
        <button
            type="button"
            onClick={handleReset}
            disabled={resetting}
            className={`inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2 ${className}`}
        >
            {resetting ? (
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
            ) : (
                <RotateCcw className="w-4 h-4 mr-2" />
            )}
            {resetting ? 'Restoring...' : label}
        </button>
    );
};

export default AudioResetButton;
