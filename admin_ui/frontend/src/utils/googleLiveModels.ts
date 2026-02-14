export const GOOGLE_LIVE_DEFAULT_MODEL = 'gemini-2.5-flash-native-audio-latest';

type GoogleLiveModelGroup = 'Gemini Developer API' | 'Vertex AI Live API';

type GoogleLiveModelOption = {
    value: string;
    label: string;
};

type GoogleLiveModelSection = {
    label: GoogleLiveModelGroup;
    options: GoogleLiveModelOption[];
};

export const GOOGLE_LIVE_MODEL_GROUPS: GoogleLiveModelSection[] = [
    {
        label: 'Gemini Developer API',
        options: [
            { value: 'gemini-2.5-flash-native-audio-latest', label: 'Gemini 2.5 Flash Native Audio (Latest)' },
            { value: 'gemini-2.5-flash-native-audio-preview-12-2025', label: 'Gemini 2.5 Flash Native Audio (Dec 2025)' },
            { value: 'gemini-2.5-flash-native-audio-preview-09-2025', label: 'Gemini 2.5 Flash Native Audio (Sep 2025)' },
        ],
    },
    {
        label: 'Vertex AI Live API',
        options: [
            { value: 'gemini-live-2.5-flash-native-audio', label: 'Gemini Live 2.5 Flash Native Audio (GA)' },
            { value: 'gemini-live-2.5-flash-preview-native-audio-09-2025', label: 'Gemini Live 2.5 Flash Native Audio (Preview 09-2025)' },
        ],
    },
];

export const GOOGLE_LIVE_MODEL_OPTIONS = GOOGLE_LIVE_MODEL_GROUPS.flatMap((group) => group.options);
export const GOOGLE_LIVE_SUPPORTED_MODELS = GOOGLE_LIVE_MODEL_OPTIONS.map((model) => model.value);

export const GOOGLE_LIVE_LEGACY_MODEL_MAP: Record<string, string> = {
    'gemini-live-2.5-flash-preview': GOOGLE_LIVE_DEFAULT_MODEL,
};

export function normalizeGoogleLiveModelForUi(model: unknown): string {
    let raw = typeof model === 'string' ? model.trim() : '';
    if (raw.startsWith('models/')) {
        raw = raw.slice(7);
    }

    if (!raw) {
        return GOOGLE_LIVE_DEFAULT_MODEL;
    }

    if (raw in GOOGLE_LIVE_LEGACY_MODEL_MAP) {
        return GOOGLE_LIVE_LEGACY_MODEL_MAP[raw];
    }

    // Always preserve the operator-configured model name.
    // Unknown models render in the "Custom" optgroup so the user
    // can see exactly what is configured and change it if needed.
    return raw;
}
