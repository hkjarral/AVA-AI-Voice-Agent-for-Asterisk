import axios from 'axios';
import yaml from 'js-yaml';

/**
 * Shared cache for the single `/api/config/yaml` document that every config
 * page reads. Without it, each navigation re-fetches and re-shows
 * "Loading configuration…". Pages seed their initial state from the cache (no
 * flash on revisit), fetch through `loadConfigYaml`, and call
 * `invalidateConfigYaml` after a save so the next reader gets fresh data.
 */
export interface ConfigYaml {
    content: string;
    config: any;
    yamlError: string | null;
}

let cache: ConfigYaml | null = null;
let inflight: Promise<ConfigYaml> | null = null;

async function fetchFromApi(): Promise<ConfigYaml> {
    const res = await axios.get('/api/config/yaml');
    if (res.data?.yaml_error) {
        return { content: res.data.content ?? '', config: {}, yamlError: res.data.yaml_error };
    }
    const parsed = (yaml.load(res.data.content) as any) || {};
    return { content: res.data.content, config: parsed, yamlError: null };
}

/** The cached config, or null if nothing has been loaded yet. */
export function getCachedConfig(): ConfigYaml | null {
    return cache;
}

/** Resolve the config: cached unless `force`, with concurrent calls deduped. */
export function loadConfigYaml(force = false): Promise<ConfigYaml> {
    if (!force && cache) return Promise.resolve(cache);
    if (inflight) return inflight;
    inflight = fetchFromApi()
        .then((r) => {
            cache = r;
            inflight = null;
            return r;
        })
        .catch((e) => {
            inflight = null;
            throw e;
        });
    return inflight;
}

/** Drop the cache so the next `loadConfigYaml` refetches (call after a save). */
export function invalidateConfigYaml(): void {
    cache = null;
}
