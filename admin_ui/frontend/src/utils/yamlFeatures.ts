export type YamlFeatureFlags = {
    hasAnchors: boolean;
    hasAliases: boolean;
    hasMergeKeys: boolean;
};

export const detectYamlFeatures = (content: string): YamlFeatureFlags => {
    const text = content || '';
    // YAML anchors: "&name"
    const hasAnchors = /&[A-Za-z0-9_-]+/.test(text);
    // YAML aliases: "*name" (avoid matching multiplication by requiring a word-ish token)
    const hasAliases = /\*[A-Za-z0-9_-]+/.test(text);
    // YAML merge keys: "<<:"
    const hasMergeKeys = /^[ \t]*<<:\s*/m.test(text);

    return { hasAnchors, hasAliases, hasMergeKeys };
};

