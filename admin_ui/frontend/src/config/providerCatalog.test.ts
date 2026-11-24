import { describe, expect, it } from 'vitest';
import { modelIds, providerDefault, providerVoices, voiceIdsForModel } from './providerCatalog';
import { MODULAR_SUBTYPES, inferSubtype } from './modularProviderSubtypes';
import { getGoogleLiveVertexRegionSupport, isGoogleLiveModelCompatible } from '../utils/googleLiveModels';

const subtype = (role: keyof typeof MODULAR_SUBTYPES, id: string) => MODULAR_SUBTYPES[role].find(item => item.id === id)!;

describe('verified provider catalog consumers', () => {
    it('uses current models for new providers while retaining deprecated supported choices', () => {
        expect(providerDefault('openai_realtime', 'model')).toBe('gpt-realtime-2.1');
        expect(providerDefault('openai', 'tts_model')).toBe('gpt-realtime-2.1-mini');
        expect(modelIds('openai', 'tts')).toContain('tts-1');
        expect(subtype('llm', 'openai').fields.find(field => field.key === 'chat_model')?.suggestions).not.toContain('gpt-3.5-turbo');
        expect(subtype('tts', 'elevenlabs').fields.find(field => field.key === 'model_id')?.suggestions).not.toContain('eleven_monolingual_v1');
    });

    it('separates voice sets by model and includes Arabic Groq voices', () => {
        expect(voiceIdsForModel('openai', 'tts-1')).not.toContain('ballad');
        expect(voiceIdsForModel('openai', 'tts-1')).not.toContain('marin');
        expect(voiceIdsForModel('openai', 'gpt-4o-mini-tts')).toContain('marin');
        expect(voiceIdsForModel('openai', 'gpt-realtime-2.1-mini')).not.toContain('nova');
        expect(voiceIdsForModel('groq', 'canopylabs/orpheus-arabic-saudi')).toEqual(['abdullah', 'fahad', 'sultan', 'lulwa', 'noura', 'aisha']);
    });

    it('offers complete verified Aura and Flux catalogs independently', () => {
        expect(providerVoices('deepgram', 'tts')).toHaveLength(103);
        expect(providerVoices('deepgram', 'flux_tts')).toHaveLength(266);
        expect(providerVoices('deepgram', 'flux_tts')).toContainEqual(expect.objectContaining({ id: 'flux-ambreen-ur', language: 'ur' }));
    });

    it('offers engine-implemented modular recognition and local backends', () => {
        expect(inferSubtype({ type: 'deepgram', capabilities: ['stt'], model: 'flux-general-multi' })?.id).toBe('deepgram_flux');
        expect(subtype('stt', 'google')).toBeDefined();
        expect(subtype('stt', 'local').fields.find(field => field.key === 'stt_backend')?.suggestions).toContain('tone');
        expect(subtype('tts', 'local').fields.find(field => field.key === 'tts_backend')?.suggestions).toContain('matcha');
    });

    it('keeps extended thinking explicitly on the Developer API and adds Mumbai for regular 3.8', () => {
        expect(isGoogleLiveModelCompatible('gemini-3.8-live-extended-thinking', false)).toBe(true);
        expect(isGoogleLiveModelCompatible('gemini-3.8-live-extended-thinking', true)).toBe(false);
        expect(getGoogleLiveVertexRegionSupport('gemini-3.8-live', true, 'asia-south1')).toBe('supported');
    });
});
