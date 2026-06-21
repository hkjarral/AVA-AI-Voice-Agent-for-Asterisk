import { describe, it, expect, vi, beforeEach } from 'vitest';
import axios from 'axios';
import { loadConfigYaml, invalidateConfigYaml, getCachedConfig } from './configCache';

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

    it('surfaces a server-side yaml_error without throwing', async () => {
        vi.mocked(axios.get).mockResolvedValue({ data: { content: '', yaml_error: 'bad indent' } });
        const r = await loadConfigYaml();
        expect(r.yamlError).toBe('bad indent');
        expect(r.config).toEqual({});
    });
});
