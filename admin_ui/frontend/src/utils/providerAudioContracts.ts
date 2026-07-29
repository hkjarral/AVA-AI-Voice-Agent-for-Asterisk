/** Enforce fields fixed by the OpenAI Realtime GA wire contract. */
export const enforceOpenAIRealtimeGaAudioContract = <T extends Record<string, unknown>>(
    provider: T,
): T => {
    if (String(provider.api_version || 'ga').toLowerCase() !== 'ga') return provider;
    return {
        ...provider,
        output_encoding: 'linear16',
        output_sample_rate_hz: 24000,
    };
};
