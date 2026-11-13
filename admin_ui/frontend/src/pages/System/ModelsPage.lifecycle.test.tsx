// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios, { type AxiosRequestConfig } from 'axios';
import type { LiveStatusSnapshot } from '../../hooks/useLiveStatus';
import ModelsPage from './ModelsPage';

vi.mock('axios');
const live = vi.hoisted(() => ({ snapshot: null as LiveStatusSnapshot | null, error: null as string | null }));
vi.mock('../../hooks/useLiveStatus', () => ({ useLiveStatus: () => ({ ...live, loading: false, connected: !live.error, mode: 'stream' }) }));
vi.mock('../../hooks/useConfirmDialog', () => ({ useConfirmDialog: () => ({ confirm: vi.fn().mockResolvedValue(false) }) }));
type Reply = { data: unknown };
const deferred = <T,>() => {
    let resolve!: (value: T) => void;
    let reject!: (reason: unknown) => void;
    const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
};
const snapshot = (state: string, id = 1): LiveStatusSnapshot => ({
    version: 1, event_id: id, generated_at: `fixture-${id}`, summary: { state, component_count: 1 },
    components: { local_ai_server: { state, freshness: 'fresh', source: 'fixture', updated_at: `fixture-${id}`,
        summary: state, details: {}, metrics: {}, warnings: [], errors: [] } },
});
const available = { stt: {}, tts: {}, llm: [] };
let loadLive: () => Promise<Reply>;
let loadDownload: (config?: AxiosRequestConfig) => Promise<Reply>;
let loadCatalog: () => Promise<Reply>;

beforeEach(() => {
    vi.resetAllMocks();
    live.snapshot = null;
    live.error = null;
    loadLive = async () => ({ data: snapshot('error') });
    loadDownload = async () => ({ data: { running: false } });
    loadCatalog = async () => ({ data: { catalog: { stt: [], tts: [], llm: [] }, language_names: {}, region_names: {} } });
    vi.mocked(axios.get).mockImplementation(async (url, config) => {
        if (url === '/api/system/live-status') return loadLive();
        if (url === '/api/system/health') return { data: { local_ai_server: { status: 'error' } } };
        if (url === '/api/local-ai/models') return { data: available };
        if (url === '/api/local-ai/capabilities') return { data: {} };
        if (url === '/api/config/env') return { data: {} };
        if (url === '/api/custom-models') return { data: { enabled: false, models: [] } };
        if (url === '/api/wizard/local/available-models') return loadCatalog();
        if (url === '/api/wizard/local/download-progress') return loadDownload(config);
        throw new Error(`Unexpected fixture GET ${url}`);
    });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });
const page = () => <MemoryRouter><ModelsPage /></MemoryRouter>;

describe('Models lifecycle ownership', () => {
    it('retains discovered download progress while pinned reads fail', async () => {
        vi.useFakeTimers();
        let reads = 0;
        loadDownload = async () => {
            if (++reads === 1) return { data: { running: true, job_id: 'recovering-job', percent: 37, total_bytes: 100, bytes_downloaded: 37 } };
            throw new Error('Synthetic progress disconnect');
        };
        render(page());
        await act(async () => {});
        expect(screen.getByText('37%')).toBeInTheDocument();
        await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
        expect(screen.getByText('37%')).toBeInTheDocument();
    });

    it('does not replace a newer pushed status with an older HTTP result', async () => {
        const old = deferred<Reply>();
        loadLive = async () => old.promise;
        const view = render(page());
        await act(async () => {});
        live.snapshot = snapshot('ready', 2);
        view.rerender(page());
        expect(screen.getByText('Connected')).toBeInTheDocument();
        await act(async () => { old.resolve({ data: snapshot('error', 1) }); });
        expect(screen.getByText('Connected')).toBeInTheDocument();
        expect(screen.queryByText('Error')).not.toBeInTheDocument();
    });

    it('does not replace a newer pushed failure with an older ready HTTP snapshot', async () => {
        const old = deferred<Reply>();
        loadLive = async () => old.promise;
        const view = render(page());
        await act(async () => {});
        live.snapshot = snapshot('unreachable', 2);
        view.rerender(page());
        await act(async () => { old.resolve({ data: snapshot('ready', 1) }); });
        expect(screen.getByText('Error')).toBeInTheDocument();
        expect(screen.queryByText('Connected')).not.toBeInTheDocument();
    });

    it('uses HTTP fallback when the stream disconnects instead of retaining cached readiness', async () => {
        live.snapshot = snapshot('ready', 2);
        const view = render(page());
        await act(async () => {});
        expect(screen.getByText('Connected')).toBeInTheDocument();
        live.error = 'Synthetic stream disconnect';
        view.rerender(page());
        await act(async () => {});
        expect(screen.getByText('Error')).toBeInTheDocument();
    });

    it('uses a newer HTTP failure even when a ready stream event arrived during the request', async () => {
        const pending = deferred<Reply>();
        loadLive = () => pending.promise;
        const view = render(page());
        await act(async () => {});
        live.snapshot = snapshot('ready', 2);
        view.rerender(page());
        await act(async () => { pending.resolve({ data: snapshot('unreachable', 3) }); });
        expect(screen.getByText('Error')).toBeInTheDocument();
        expect(screen.queryByText('Connected')).not.toBeInTheDocument();
    });

    it('aborts outstanding status reads on unmount and ignores a late failure', async () => {
        const pending = deferred<Reply>();
        loadLive = async () => pending.promise;
        const view = render(page());
        await act(async () => {});
        const signal = vi.mocked(axios.get).mock.calls.find(([url]) => url === '/api/system/live-status')?.[1]?.signal;
        view.unmount();
        expect(signal?.aborted).toBe(true);
        const count = vi.mocked(axios.get).mock.calls.length;
        await act(async () => { pending.reject(new Error('Obsolete status failure')); });
        expect(vi.mocked(axios.get).mock.calls).toHaveLength(count);
    });

    it('pins a resumed progress poll to the returned job ID', async () => {
        let reads = 0;
        loadDownload = async () => ({ data: ++reads === 1 ? { running: true, job_id: 'synthetic-job-A' } : { completed: true, job_id: 'synthetic-job-A' } });
        await act(async () => { render(page()); });
        const calls = vi.mocked(axios.get).mock.calls.filter(([url]) => url.endsWith('/download-progress'));
        expect(calls[1][1]?.params).toEqual({ job_id: 'synthetic-job-A' });
    });

    it('does not refresh the model catalog when a resumed poll completes after unmount', async () => {
        const pending = deferred<Reply>();
        let reads = 0;
        loadDownload = async () => ++reads === 1 ? { data: { running: true, job_id: 'synthetic-job-A' } } : pending.promise;
        const view = render(page());
        await act(async () => {});
        expect(reads).toBe(2);
        view.unmount();
        const count = vi.mocked(axios.get).mock.calls.length;
        await act(async () => { pending.resolve({ data: { completed: true, job_id: 'synthetic-job-A' } }); });
        expect(vi.mocked(axios.get).mock.calls).toHaveLength(count);
    });

    it('cancels a resumed download retry timer on unmount', async () => {
        vi.useFakeTimers();
        let reads = 0;
        loadDownload = async () => {
            if (++reads === 1) return { data: { running: true, job_id: 'synthetic-job-A' } };
            throw new Error('Synthetic read failure');
        };
        let view!: ReturnType<typeof render>;
        await act(async () => { view = render(page()); });
        expect(reads).toBe(2);
        view.unmount();
        expect(vi.getTimerCount()).toBe(0);
        await act(async () => { vi.advanceTimersByTime(10000); });
        expect(reads).toBe(2);
    });

    it('does not start polling a download accepted after the page closed', async () => {
        vi.useFakeTimers();
        loadCatalog = async () => ({ data: { catalog: { stt: [{ id: 'fixture-model', name: 'Fixture Model', size_mb: 1, size_display: '1 MB', download_url: 'https://example.com/model' }], tts: [], llm: [] } } });
        const submission = deferred<Reply>();
        vi.mocked(axios.post).mockReturnValue(submission.promise);
        let view!: ReturnType<typeof render>;
        await act(async () => { view = render(page()); });
        fireEvent.click(screen.getByRole('button', { name: 'STT (1)' }));
        fireEvent.click(screen.getByRole('button', { name: 'Download' }));
        expect(axios.post).toHaveBeenCalledTimes(1);
        view.unmount();
        const count = vi.mocked(axios.get).mock.calls.length;
        await act(async () => { submission.resolve({ data: { job_id: 'accepted-server-job' } }); vi.advanceTimersByTime(10000); });
        expect(vi.mocked(axios.get).mock.calls).toHaveLength(count);
        expect(vi.getTimerCount()).toBe(0);
    });
});
