import axios from 'axios';
import yaml from 'js-yaml';
import { YamlErrorInfo } from '../components/ui/YamlErrorBanner';

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
    yamlError: YamlErrorInfo | null;
}

let cache: ConfigYaml | null = null;
let inflight: Promise<ConfigYaml> | null = null;
// Bumped on every invalidation so a response that was already in flight when a
// save happened can be recognised as stale and discarded instead of caching it.
let generation = 0;

function clone<T>(v: T): T {
    return typeof structuredClone === 'function' ? structuredClone(v) : JSON.parse(JSON.stringify(v));
}

// Hand out an independent copy of the mutable `config` so a consumer that edits
// it (and a save that later fails) can't corrupt the shared cache.
function snapshot(): ConfigYaml | null {
    return cache ? { content: cache.content, config: clone(cache.config), yamlError: cache.yamlError } : null;
}

async function fetchFromApi(): Promise<ConfigYaml> {
    const res = await axios.get('/api/config/yaml');
    if (res.data?.yaml_error) {
        return { content: res.data.content ?? '', config: {}, yamlError: res.data.yaml_error };
    }
    const parsed = (yaml.load(res.data.content) as any) || {};
    return { content: res.data.content, config: parsed, yamlError: null };
}

/** A deep-cloned copy of the cached config, or null if nothing is loaded yet. */
export function getCachedConfig(): ConfigYaml | null {
    return snapshot();
}

/** Resolve the config: cached unless `force`, with concurrent calls deduped. */
export function loadConfigYaml(force = false): Promise<ConfigYaml> {
    if (!force && cache) return Promise.resolve(snapshot()!);
    if (inflight) return inflight;
    const gen = generation;
    const p: Promise<ConfigYaml> = fetchFromApi().then(
        (r) => {
            if (inflight === p) inflight = null;
            // Invalidated while this request was in flight → the response is
            // stale; discard it and refetch the current config instead.
            if (gen !== generation) return loadConfigYaml(true);
            cache = r;
            return snapshot()!;
        },
        (e) => {
            if (inflight === p) inflight = null;
            throw e;
        },
    );
    inflight = p;
    return p;
}

/** Drop the cache so the next `loadConfigYaml` refetches (call after a save). */
export function invalidateConfigYaml(): void {
    cache = null;
    generation++;
    inflight = null;
}

// Backend endpoints that rewrite the merged config file (ai-agent.local.yaml):
// the normal save, the config import, and the Setup Wizard's save. A write to any
// of them must drop the cache, or a cached page could reopen with stale config and
// overwrite those changes on its next save.
const CONFIG_WRITE_PATHS = ['/api/config/yaml', '/api/config/import', '/api/wizard/save'];

/** Invalidate when a response is a successful write to the shared config document. */
export function handleConfigWrite(response: { config?: { method?: string; url?: string } }): void {
    const cfg = response?.config;
    const method = cfg?.method?.toLowerCase();
    const path = cfg?.url?.split('?')[0];
    if (path && (method === 'post' || method === 'put') && CONFIG_WRITE_PATHS.includes(path)) {
        invalidateConfigYaml();
    }
}

// Any successful write to a config-file endpoint — including from pages/flows not
// migrated to this cache (e.g. the Setup Wizard) — drops the cache, so migrated
// pages never seed stale config after an edit made elsewhere. (Optional-chained so
// unit-test axios mocks without an interceptor registry are a no-op.)
axios.interceptors?.response?.use?.((response) => {
    handleConfigWrite(response);
    return response;
});
