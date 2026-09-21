import { describe, expect, it } from 'vitest';
import { FISH_AUDIO_MODELS, MODULAR_SUBTYPES, inferSubtype } from './modularProviderSubtypes';

describe('modular LLM provider subtypes', () => {
    it('offers a first-class DeepSeek preset with current official defaults', () => {
        const deepseek = MODULAR_SUBTYPES.llm.find(subtype => subtype.id === 'deepseek');

        expect(deepseek).toBeDefined();
        expect(deepseek?.yamlType).toBe('openai');
        expect(deepseek?.fields).toEqual(
            expect.arrayContaining([
                expect.objectContaining({
                    key: 'chat_base_url',
                    default: 'https://api.deepseek.com',
                }),
                expect.objectContaining({
                    key: 'chat_model',
                    default: 'deepseek-v4-flash',
                }),
            ])
        );
    });

    it('recognizes an existing DeepSeek OpenAI-compatible configuration', () => {
        expect(
            inferSubtype({
                type: 'openai',
                capabilities: ['llm'],
                chat_base_url: 'https://api.deepseek.com',
                chat_model: 'deepseek-v4-pro',
            })?.id
        ).toBe('deepseek');
    });
});

describe('modular TTS provider subtypes', () => {
    it('offers Fish Audio with secure streaming defaults', () => {
        const fishAudio = MODULAR_SUBTYPES.tts.find(subtype => subtype.id === 'fishaudio');

        expect(fishAudio).toBeDefined();
        expect(fishAudio?.yamlType).toBe('fishaudio');
        expect(fishAudio?.fields).toEqual(
            expect.arrayContaining([
                expect.objectContaining({
                    key: 'base_url',
                    default: 'https://api.fish.audio/v1',
                }),
                expect.objectContaining({ key: 'reference_id', required: true }),
                expect.objectContaining({
                    key: 'transport',
                    type: 'select',
                    default: 'http',
                    suggestions: ['http', 'websocket'],
                }),
                expect.objectContaining({ key: 'ws_base_url', required: false }),
                expect.objectContaining({ key: 'connect_timeout_sec', default: 10 }),
                expect.objectContaining({ key: 'read_timeout_sec', default: 30 }),
            ])
        );
        expect(
            fishAudio?.fields.find(field => field.key === 'sample_rate')?.suggestions
        ).not.toContain('48000');
        expect(fishAudio?.fields.find(field => field.key === 'model')).toEqual(
            expect.objectContaining({
                type: 'select',
                default: 's2.1-pro',
                suggestions: FISH_AUDIO_MODELS,
            })
        );
        expect(FISH_AUDIO_MODELS).toEqual([
            's2.1-pro',
            's2.1-pro-free',
            's2-pro',
            's1',
            'drama-3-preview',
        ]);
    });

    it('recognizes an existing Fish Audio TTS configuration', () => {
        expect(
            inferSubtype({
                type: 'fishaudio',
                capabilities: ['tts'],
                reference_id: 'voice-id',
            })?.id
        ).toBe('fishaudio');
    });
});
