export type SttStreamingMode = 'buffered' | 'optional' | 'required';
type SttOptions = Record<string, unknown>;
type ProviderConfigMap = Record<string, { variant?: unknown } | undefined>;
type NormalizeSttOptionsConfig = {
    preserveStreamingChoice?: boolean;
};

export const PIPELINE_STT_STREAM_FORMAT = 'pcm16_16k';
export const PIPELINE_STT_SAMPLE_RATE_HZ = 16000;

export function getSttStreamingMode(
    sttKey: string,
    providers: ProviderConfigMap = {}
): SttStreamingMode {
    const key = String(sttKey || '')
        .trim()
        .toLowerCase();
    if (key === 'deepgram_flux_stt') return 'required';
    if (key === 'local_stt' || key === 'deepgram_stt' || key === 'azure_stt_realtime') {
        return 'optional';
    }
    if (key === 'azure_stt') {
        const variant = String(providers?.azure_stt?.variant || 'realtime')
            .trim()
            .toLowerCase();
        return variant === 'fast' ? 'buffered' : 'optional';
    }
    return 'buffered';
}

function asSttOptions(options: unknown): SttOptions {
    return options && typeof options === 'object' && !Array.isArray(options)
        ? (options as SttOptions)
        : {};
}

function portableSttOptions(options: unknown): SttOptions {
    const source = asSttOptions(options);
    const portable: SttOptions = {};
    for (const key of [
        'language',
        'prompt',
        'request_timeout_sec',
        'timeout_sec',
        'response_format',
        'temperature',
        'timestamp_granularities',
        'vad_silence_ms',
        'vad_silence_timeout_ms',
        'vad_initial_silence_timeout_ms',
        'eot_threshold',
        'eager_eot_threshold',
        'eot_timeout_ms',
    ]) {
        if (source[key] != null) portable[key] = source[key];
    }
    return portable;
}

export function normalizeSttOptions(
    sttKey: string,
    options: unknown,
    providers: ProviderConfigMap = {},
    config: NormalizeSttOptionsConfig = {}
): SttOptions {
    const source = asSttOptions(options);
    const normalized = portableSttOptions(source);
    const mode = getSttStreamingMode(sttKey, providers);
    const preserveStreamingChoice = config.preserveStreamingChoice !== false;

    if (mode === 'buffered') {
        normalized.streaming = false;
        normalized.chunk_ms =
            typeof source.chunk_ms === 'number' && Number.isFinite(source.chunk_ms)
                ? source.chunk_ms
                : 4000;
        return normalized;
    }

    const streaming = mode === 'required' ? true : !preserveStreamingChoice || source.streaming !== false;
    normalized.streaming = streaming;
    if (!streaming) {
        normalized.chunk_ms =
            typeof source.chunk_ms === 'number' && Number.isFinite(source.chunk_ms)
                ? source.chunk_ms
                : 4000;
        return normalized;
    }

    const defaultChunkMs = String(sttKey || '').toLowerCase() === 'deepgram_flux_stt' ? 80 : 160;
    const configuredChunkMs = Number(source.chunk_ms);
    normalized.chunk_ms =
        Number.isFinite(configuredChunkMs) && configuredChunkMs >= 80 && configuredChunkMs <= 1000
            ? configuredChunkMs
            : defaultChunkMs;
    normalized.stream_format = PIPELINE_STT_STREAM_FORMAT;
    normalized.sample_rate = PIPELINE_STT_SAMPLE_RATE_HZ;
    normalized.encoding = 'linear16';
    normalized.channels = 1;
    if (String(sttKey || '').toLowerCase() === 'local_stt') normalized.mode = 'stt';
    return normalized;
}
