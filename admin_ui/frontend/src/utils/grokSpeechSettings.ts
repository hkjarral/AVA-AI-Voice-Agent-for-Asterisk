// Keep multiline edits verbatim until Save so incomplete JSON and trailing
// newlines remain editable. Only structured values go into provider YAML.
export function grokSpeechSettingsForSave(config: {
    keyterms?: string | string[] | null;
    language_hint?: string | null;
    pronunciation_replacements?: unknown;
}) {
    const patch: { keyterms?: string[]; pronunciation_replacements?: Record<string, string> } = {};
    if (config.keyterms != null) {
        const terms = typeof config.keyterms === 'string'
            ? config.keyterms.split('\n').map(term => term.trim()).filter(Boolean)
            : config.keyterms;
        if (!Array.isArray(terms) || terms.length > 100 || terms.some(term =>
            typeof term !== 'string' || !term.trim() || Array.from(term).length > 50)) {
            throw new Error('Enter up to 100 keyterms of 1–50 characters each.');
        }
        patch.keyterms = terms;
    }
    if (config.language_hint != null && (
        !/^[a-z]{2,8}(-[a-z0-9]{1,8})*$/i.test(config.language_hint) || /^(es|pt)$/i.test(config.language_hint)
    )) {
        throw new Error('Enter a language tag such as ja, es-MX or pt-BR. Spanish and Portuguese require a region.');
    }
    if (config.pronunciation_replacements != null) {
        let map = config.pronunciation_replacements;
        const message = 'Enter pronunciation replacements as a JSON object with nonblank phrases and spoken text.';
        if (typeof map === 'string') {
            try {
                map = JSON.parse(map);
            } catch {
                throw new Error(message);
            }
        }
        if (!map || typeof map !== 'object' || Array.isArray(map) || Object.entries(map).some(
            ([phrase, spoken]) => !phrase.trim() || typeof spoken !== 'string' || !spoken.trim(),
        )) {
            throw new Error(message);
        }
        patch.pronunciation_replacements = map as Record<string, string>;
    }
    return patch;
}
