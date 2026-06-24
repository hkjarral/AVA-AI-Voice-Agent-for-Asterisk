import { describe, expect, it } from 'vitest';
import {
    getSttStreamingMode,
    normalizeSttOptions,
    PIPELINE_STT_SAMPLE_RATE_HZ,
    PIPELINE_STT_STREAM_FORMAT,
} from './sttAudioContract';

describe('modular STT audio contract', () => {
    it('detects Azure alias mode from the configured variant', () => {
        expect(getSttStreamingMode('azure_stt', { azure_stt: { variant: 'realtime' } })).toBe(
            'optional'
        );
        expect(getSttStreamingMode('azure_stt', { azure_stt: { variant: 'fast' } })).toBe(
            'buffered'
        );
    });

    it('replaces conflicting legacy streaming metadata with the engine format', () => {
        const options = normalizeSttOptions('azure_stt_realtime', {
            streaming: true,
            chunk_ms: 4000,
            stream_format: 'pcm16_8k',
            sample_rate: 8000,
            language: 'zh-TW',
        });
        expect(options).toMatchObject({
            streaming: true,
            chunk_ms: 160,
            stream_format: PIPELINE_STT_STREAM_FORMAT,
            sample_rate: PIPELINE_STT_SAMPLE_RATE_HZ,
            encoding: 'linear16',
            channels: 1,
            language: 'zh-TW',
        });
    });

    it('forces Flux streaming and uses the provider-recommended default chunk size', () => {
        const options = normalizeSttOptions('deepgram_flux_stt', {
            streaming: false,
            chunk_ms: 4000,
        });
        expect(options.streaming).toBe(true);
        expect(options.chunk_ms).toBe(80);
    });

    it('ignores stale buffered streaming flags when switching to optional streaming STT', () => {
        const options = normalizeSttOptions(
            'local_stt',
            {
                streaming: false,
                chunk_ms: 4000,
            },
            {},
            { preserveStreamingChoice: false }
        );
        expect(options).toMatchObject({
            streaming: true,
            chunk_ms: 160,
            stream_format: PIPELINE_STT_STREAM_FORMAT,
            sample_rate: PIPELINE_STT_SAMPLE_RATE_HZ,
            mode: 'stt',
        });
    });

    it('preserves an explicit streaming-off choice for the same optional streaming STT', () => {
        const options = normalizeSttOptions('local_stt', {
            streaming: false,
            chunk_ms: 4000,
        });
        expect(options.streaming).toBe(false);
        expect(options.chunk_ms).toBe(4000);
        expect(options).not.toHaveProperty('stream_format');
    });

    it('does not attach raw streaming format fields to buffered providers', () => {
        const options = normalizeSttOptions('openai_stt', {
            stream_format: 'pcm16_8k',
            sample_rate: 8000,
        });
        expect(options.streaming).toBe(false);
        expect(options).not.toHaveProperty('stream_format');
        expect(options).not.toHaveProperty('sample_rate');
    });
});
