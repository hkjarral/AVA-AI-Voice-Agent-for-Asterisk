// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import PipelineForm from './PipelineForm';


const providers = {
    local_stt: { type: 'local', capabilities: ['stt'] },
    local_llm: { type: 'local', capabilities: ['llm'] },
    local_tts: { type: 'local', capabilities: ['tts'] },
};

const baseConfig = {
    name: 'local_hybrid',
    stt: 'local_stt',
    llm: 'local_llm',
    tts: 'local_tts',
    options: {},
};

describe('PipelineForm TTS playback policy', () => {
    beforeEach(() => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({ healthy: true }),
        }));
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it('inherits the global overlap policy by default and stores a scoped override', async () => {
        const onChange = vi.fn();
        render(
            <PipelineForm
                config={baseConfig}
                providers={providers}
                onChange={onChange}
            />
        );

        const selector = screen.getByLabelText('Streaming Overlap');
        expect(selector).toHaveValue('');

        fireEvent.change(selector, { target: { value: 'false' } });

        await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(
            expect.objectContaining({
                options: { tts: { streaming_overlap: false } },
            })
        ));
    });

    it('removes only the overlap override when returning to inherit', async () => {
        const onChange = vi.fn();
        render(
            <PipelineForm
                config={{
                    ...baseConfig,
                    options: {
                        tts: {
                            output_resampler: 'bandlimited',
                            streaming_overlap: false,
                        },
                    },
                }}
                providers={providers}
                onChange={onChange}
            />
        );

        const selector = screen.getByLabelText('Streaming Overlap');
        expect(selector).toHaveValue('false');
        fireEvent.change(selector, { target: { value: '' } });

        await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(
            expect.objectContaining({
                options: { tts: { output_resampler: 'bandlimited' } },
            })
        ));
    });

    it('edits Local Whisper segmentation without changing other roles', async () => {
        const onChange = vi.fn();
        render(
            <PipelineForm
                config={{
                    ...baseConfig,
                    options: {
                        stt: {
                            segment_energy_threshold: 800,
                            segment_silence_ms: 1200,
                        },
                        tts: { output_resampler: 'bandlimited' },
                    },
                }}
                providers={providers}
                onChange={onChange}
            />
        );

        const silenceField = screen.getByLabelText('Local STT End Silence (ms)');
        expect(silenceField).toHaveValue(1200);
        fireEvent.change(silenceField, { target: { value: '1400' } });

        await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(
            expect.objectContaining({
                options: {
                    stt: {
                        segment_energy_threshold: 800,
                        segment_silence_ms: 1400,
                    },
                    tts: { output_resampler: 'bandlimited' },
                },
            })
        ));
    });
});
