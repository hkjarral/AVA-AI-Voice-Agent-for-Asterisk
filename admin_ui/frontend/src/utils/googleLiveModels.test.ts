import { describe, expect, it } from 'vitest';

import {
    GOOGLE_LIVE_MODEL_GROUPS,
    GOOGLE_LIVE_VERTEX_MODEL_REGIONS,
    GOOGLE_LIVE_VERTEX_REGIONS,
    getGoogleLiveVertexRegionSupport,
    isGoogleLiveModelCompatible,
    preferredGoogleLiveVertexRegion,
} from './googleLiveModels';

describe('Google Live model API compatibility', () => {
    it('offers Gemini 3.8 Live on both Developer API and Vertex AI', () => {
        expect(GOOGLE_LIVE_MODEL_GROUPS.find(group => group.label === 'Both Google APIs')?.options)
            .toContainEqual({ value: 'gemini-3.8-live', label: 'Gemini 3.8 Live (GA)' });
        expect(isGoogleLiveModelCompatible('gemini-3.8-live', false)).toBe(true);
        expect(isGoogleLiveModelCompatible('models/gemini-3.8-live', true)).toBe(true);
    });

    it('keeps existing surface-specific models restricted', () => {
        expect(isGoogleLiveModelCompatible('gemini-live-2.5-flash-native-audio', true)).toBe(true);
        expect(isGoogleLiveModelCompatible('gemini-live-2.5-flash-native-audio', false)).toBe(false);
        expect(isGoogleLiveModelCompatible('gemini-3.1-flash-live-preview', false)).toBe(true);
        expect(isGoogleLiveModelCompatible('gemini-3.1-flash-live-preview', true)).toBe(false);
    });
});

describe('Google Live Vertex model/region compatibility', () => {
    it('includes every officially listed GA model location', () => {
        expect(GOOGLE_LIVE_VERTEX_MODEL_REGIONS['gemini-3.8-live']).toEqual(['us-central1', 'us', 'eu', 'asia-south1']);
        expect(GOOGLE_LIVE_VERTEX_MODEL_REGIONS['gemini-live-2.5-flash-native-audio']).toHaveLength(13);
        for (const locations of Object.values(GOOGLE_LIVE_VERTEX_MODEL_REGIONS)) {
            for (const location of locations) {
                expect(GOOGLE_LIVE_VERTEX_REGIONS.some(region => region.value === location)).toBe(true);
            }
        }
        expect(getGoogleLiveVertexRegionSupport('models/gemini-3.8-live', true, 'us')).toBe('supported');
        expect(getGoogleLiveVertexRegionSupport('gemini-3.8-live', true, 'eu')).toBe('supported');
        expect(getGoogleLiveVertexRegionSupport('gemini-3.8-live', true, 'us-east1')).toBe('unsupported');
        expect(getGoogleLiveVertexRegionSupport('gemini-live-2.5-flash-native-audio', true, 'us-east1')).toBe('supported');
        expect(getGoogleLiveVertexRegionSupport('gemini-live-2.5-flash-native-audio', true, 'us')).toBe('unsupported');
    });

    it('keeps a valid region and falls back to the shared working default after model changes', () => {
        expect(preferredGoogleLiveVertexRegion('gemini-live-2.5-flash-native-audio', 'us-east1')).toBe('us-east1');
        expect(preferredGoogleLiveVertexRegion('gemini-3.8-live', 'us-east1')).toBe('us-central1');
        expect(preferredGoogleLiveVertexRegion('gemini-live-2.5-flash-native-audio', 'eu')).toBe('us-central1');
        expect(preferredGoogleLiveVertexRegion('gemini-3.8-live', 'us-central1')).toBe('us-central1');
    });

    it('does not guess availability for Developer API, legacy preview, or custom models', () => {
        expect(getGoogleLiveVertexRegionSupport('gemini-3.8-live', false, 'us-east1')).toBeNull();
        expect(getGoogleLiveVertexRegionSupport('gemini-live-2.5-flash-preview-native-audio-09-2025', true, 'us-central1')).toBe('unknown');
        expect(getGoogleLiveVertexRegionSupport('custom-live-model', true, 'us-east1')).toBe('unknown');
        expect(preferredGoogleLiveVertexRegion('custom-live-model', 'us-east1')).toBe('us-east1');
        expect(getGoogleLiveVertexRegionSupport('__proto__', true, 'us-central1')).toBe('unknown');
    });
});
