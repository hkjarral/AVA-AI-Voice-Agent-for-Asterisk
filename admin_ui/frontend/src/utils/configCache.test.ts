import { describe, it, expect, vi, beforeEach } from 'vitest';
import axios from 'axios';
import { loadConfigYaml, invalidateConfigYaml, getCachedConfig, handleConfigWrite } from './configCache';

vi.mock('axios');

beforeEach(() => {
    invalidateConfigYaml();
    vi.mocked(axios.get).mockReset();
});

describe('configCache', () => {
    it('fetches and parses the config on first load', async () => {
        vi.mocked(axios.get).mockResolvedValue({ data: { content: 'agent:\n  name: A' } });
        const r = await loadConfigYaml();
        expect(r.config).toEqual({ agent: { name: 'A' } });
        expect(r.yamlError).toBeNull();
        expect(axios.get).toHaveBeenCalledTimes(1);
    });

    it('serves the cached config without a second request', async () => {
        vi.mocked(axios.get).mockResolvedValue({ data: { content: 'k: 1' } });
        await loadConfigYaml();
        const r2 = await loadConfigYaml();
        expect(r2.config).toEqual({ k: 1 });
        expect(axios.get).toHaveBeenCalledTimes(1);
    });

    it('refetches after invalidation', async () => {
        vi.mocked(axios.get).mockResolvedValue({ data: { content: 'k: 1' } });
        await loadConfigYaml();
        invalidateConfigYaml();
        expect(getCachedConfig()).toBeNull();
        await loadConfigYaml();
        expect(axios.get).toHaveBeenCalledTimes(2);
    });

    it.each(['/api/config/yaml', '/api/wizard/save', '/api/config/import'])(
        'invalidates the cache when a config write to %s succeeds',
        async (url) => {
            vi.mocked(axios.get).mockResolvedValue({ data: { content: 'k: 1' } });
            await loadConfigYaml();
            expect(getCachedConfig()).not.toBeNull();
            handleConfigWrite({ config: { method: 'post', url } });
            expect(getCachedConfig()).toBeNull(); // any config writer drops the shared cache
        }
    );

    it('ignores writes to other endpoints', async () => {
        vi.mocked(axios.get).mockResolvedValue({ data: { content: 'k: 1' } });
        await loadConfigYaml();
        handleConfigWrite({ config: { method: 'post', url: '/api/providers/health' } });
        expect(getCachedConfig()).not.toBeNull();
    });

    it('refetches when forced even with a warm cache', async () => {
        vi.mocked(axios.get).mockResolvedValue({ data: { content: 'k: 1' } });
        await loadConfigYaml();
        await loadConfigYaml(true);
        expect(axios.get).toHaveBeenCalledTimes(2);
    });

    it('dedupes concurrent in-flight loads into one request', async () => {
        vi.mocked(axios.get).mockResolvedValue({ data: { content: 'k: 1' } });
        await Promise.all([loadConfigYaml(), loadConfigYaml()]);
        expect(axios.get).toHaveBeenCalledTimes(1);
    });

    it('discards a stale in-flight response when invalidated mid-flight and refetches fresh', async () => {
        let resolveFirst: (v: any) => void = () => {};
        const firstResponse = new Promise((res) => { resolveFirst = res; });
        vi.mocked(axios.get)
            .mockReturnValueOnce(firstResponse as any) // first GET stays pending
            .mockResolvedValueOnce({ data: { content: 'k: 2' } }); // the refetch

        const p = loadConfigYaml();            // first GET in flight (generation 0)
        invalidateConfigYaml();                // a save invalidates while in flight
        resolveFirst({ data: { content: 'k: 1' } }); // the now-stale response resolves
        const r = await p;

        expect(r.config).toEqual({ k: 2 });    // fresh, not the stale k:1
        expect(getCachedConfig()?.config).toEqual({ k: 2 });
    });

    it('surfaces the structured server-side yaml_error without throwing', async () => {
        // The backend returns yaml_error as a structured object (config.py), not a string.
        const yamlError = { type: 'yaml_error', message: 'Invalid YAML', line: 3, column: 1, context: null, snippet: null };
        vi.mocked(axios.get).mockResolvedValue({ data: { content: 'bad: [', yaml_error: yamlError } });
        const r = await loadConfigYaml();
        expect(r.yamlError).toEqual(yamlError);
        expect(r.config).toEqual({});
    });

    it('returns an independent copy so mutating it does not corrupt the cache', async () => {
        vi.mocked(axios.get).mockResolvedValue({ data: { content: 'providers:\n  a:\n    enabled: true' } });
        const r1 = await loadConfigYaml();
        r1.config.providers.a.enabled = false; // mutate the returned config
        const r2 = await loadConfigYaml(); // cache hit
        expect(r2.config.providers.a.enabled).toBe(true); // cache unaffected by the mutation
    });
});
