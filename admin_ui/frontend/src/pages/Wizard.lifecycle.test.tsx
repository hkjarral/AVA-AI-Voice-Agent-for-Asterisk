// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios, { type AxiosRequestConfig } from 'axios';
import { toast } from 'sonner';
import Wizard from './Wizard';
import { saveWizardDraft } from '../utils/wizardDraft';

vi.mock('axios');
vi.mock('../hooks/useConfirmDialog', () => ({ useConfirmDialog: () => ({ confirm: vi.fn() }) }));
vi.mock('../components/SetupHandoff', () => ({ default: () => <div>Mock handoff</div> }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));
type Reply = { data: unknown };
const deferred = <T,>() => {
    let resolve!: (value: T) => void;
    let reject!: (reason: unknown) => void;
    const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
};
const saved = { provider: 'local', asterisk_host: '127.0.0.1', asterisk_username: 'test', asterisk_password: 'synthetic-password', openai_key: 'synthetic-key' };
let loadProgress: (config?: AxiosRequestConfig) => Promise<Reply>;
let startServer: () => Promise<Reply>;
let loadLogs: () => Promise<Reply>;
let loadCatalog: () => Promise<Reply>;
const page = () => <MemoryRouter><Wizard /></MemoryRouter>;
const flush = async () => { await act(async () => {}); };
const requests = (url: string) => vi.mocked(axios.get).mock.calls.filter(([path]) => path === url);
beforeEach(() => {
    vi.resetAllMocks();
    sessionStorage.clear();
    saveWizardDraft(3, saved);
    loadProgress = async () => ({ data: { running: false } });
    startServer = async () => ({ data: { success: true } });
    loadLogs = async () => ({ data: { ready: true, logs: ['Synthetic ready'] } });
    loadCatalog = async () => ({ data: { catalog: { stt: [], tts: [], llm: [] }, languages: {}, language_names: {}, region_names: {} } });
    vi.mocked(axios.get).mockImplementation(async (url, config) => {
        if (url.endsWith('/load-config')) return { data: saved };
        if (url.endsWith('/download-progress')) return loadProgress(config);
        if (url.endsWith('/available-models')) return loadCatalog();
        if (url.endsWith('/models-status')) return { data: { ready: true } };
        if (url.endsWith('/server-logs')) return loadLogs();
        return { data: {} };
    });
    vi.mocked(axios.post).mockImplementation(async url => {
        if (url.endsWith('/start-server')) return startServer();
        if (url.endsWith('/download-selected-models')) return { data: { job_id: 'submitted-A' } };
        return { data: { success: true, valid: true } };
    });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

describe('Wizard lifecycle ownership', () => {
    it('pins resumed downloads to their job and ignores a completion after unmount', async () => {
        const pending = deferred<Reply>();
        let reads = 0;
        loadProgress = async () => ++reads === 1 ? { data: { running: true, job_id: 'resumed-A' } } : pending.promise;
        const view = render(page());
        await flush();
        expect(requests('/api/wizard/local/download-progress')[1][1]?.params).toEqual({ job_id: 'resumed-A' });
        const before = requests('/api/wizard/local/models-status').length;
        view.unmount();
        await act(async () => { pending.resolve({ data: { completed: true } }); });
        expect(requests('/api/wizard/local/models-status')).toHaveLength(before);
    });

    it('does not mark a different model selection downloaded when the previous job completes', async () => {
        const pending = deferred<Reply>();
        const next = deferred<Reply>();
        let polls = 0;
        loadProgress = async config => config?.params ? (++polls === 1 ? pending.promise : next.promise) : { data: { running: false } };
        render(page());
        await flush();
        fireEvent.click(screen.getByRole('button', { name: 'Download Models' }));
        await flush();
        fireEvent.change(screen.getByDisplayValue('English (US)'), { target: { value: 'es-ES' } });
        expect(screen.getByRole('button', { name: 'Downloading...' })).toBeDisabled();
        fireEvent.click(screen.getByRole('button', { name: 'Downloading...' }));
        expect(vi.mocked(axios.post).mock.calls.filter(([url]) => url.endsWith('/download-selected-models'))).toHaveLength(1);
        await act(async () => { pending.resolve({ data: { completed: true } }); });
        expect(screen.getByRole('button', { name: 'Download Models' })).toBeEnabled();
        expect(screen.queryByRole('button', { name: 'Downloaded' })).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Download Models' }));
        await flush();
        const submissions = vi.mocked(axios.post).mock.calls.filter(([url]) => url.endsWith('/download-selected-models'));
        expect(submissions).toHaveLength(2);
        expect(submissions[0][1]).toMatchObject({ language: 'en-US' });
        expect(submissions[1][1]).toMatchObject({ language: 'es-ES' });
        await act(async () => { next.resolve({ data: { completed: true } }); });
        expect(screen.getByRole('button', { name: 'Downloaded' })).toBeDisabled();
    });

    it('makes one automatic start attempt and waits for an explicit retry after failure', async () => {
        let attempts = 0;
        const second = deferred<Reply>();
        startServer = async () => ++attempts === 1 ? Promise.reject(new Error('Synthetic startup failure')) : second.promise;
        loadProgress = async config => ({ data: config?.params ? { completed: true } : { running: false } });
        render(page());
        await flush();
        fireEvent.click(screen.getByRole('button', { name: 'Download Models' }));
        await flush();
        fireEvent.click(screen.getByRole('button', { name: 'Next' }));
        await flush();
        fireEvent.click(screen.getByRole('button', { name: 'Finish Setup' }));
        await flush();
        expect(attempts).toBe(1);
        fireEvent.click(screen.getByRole('button', { name: 'Start Local AI Server' }));
        await flush();
        expect(attempts).toBe(2);
        await act(async () => { second.resolve({ data: { success: true } }); });
        expect(screen.getByText('Server Ready!')).toBeInTheDocument();
    });
});

const finishLocalSetup = async () => {
    loadProgress = async config => ({ data: config?.params ? { completed: true } : { running: false } });
    const view = render(page());
    await flush();
    fireEvent.click(screen.getByRole('button', { name: 'Download Models' }));
    await flush();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await flush();
    fireEvent.click(screen.getByRole('button', { name: 'Finish Setup' }));
    await flush();
    return view;
};

describe('Wizard recovery and cleanup', () => {
    it('blocks another download until initial job discovery settles', async () => {
        const pending = deferred<Reply>();
        loadProgress = () => pending.promise;
        render(page());
        await flush();
        expect(screen.getByRole('button', { name: 'Download Models' })).toBeDisabled();
        fireEvent.click(screen.getByRole('button', { name: 'Download Models' }));
        expect(axios.post).not.toHaveBeenCalled();
        await act(async () => { pending.resolve({ data: { running: false } }); });
        expect(screen.getByRole('button', { name: 'Download Models' })).toBeEnabled();
    });

    it('offers read-only recovery when download discovery fails', async () => {
        loadProgress = async () => { throw new Error('Synthetic unavailable'); };
        render(page());
        await flush();
        expect(screen.getByRole('button', { name: 'Download Models' })).toBeDisabled();
        loadProgress = async () => ({ data: { running: false } });
        fireEvent.click(screen.getByRole('button', { name: 'Check download status' }));
        await flush();
        expect(screen.getByRole('button', { name: 'Download Models' })).toBeEnabled();
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('reconciles a failed submission response before permitting another write', async () => {
        render(page());
        await flush();
        vi.mocked(axios.post).mockRejectedValueOnce(new Error('Synthetic lost response'));
        fireEvent.click(screen.getByRole('button', { name: 'Download Models' }));
        await flush();
        expect(screen.getByRole('button', { name: 'Download Models' })).toBeDisabled();
        const pending = deferred<Reply>();
        loadProgress = async config => config?.params ? pending.promise : { data: { running: true, job_id: 'accepted-with-lost-response' } };
        fireEvent.click(screen.getByRole('button', { name: 'Check download status' }));
        await flush();
        expect(screen.getByRole('button', { name: 'Downloading...' })).toBeDisabled();
        expect(axios.post).toHaveBeenCalledTimes(1);
        await act(async () => { pending.resolve({ data: { completed: true } }); });
        expect(screen.getByRole('button', { name: 'Download Models' })).toBeEnabled();
        expect(screen.queryByRole('button', { name: 'Downloaded' })).not.toBeInTheDocument();
    });

    it('keeps setup save blocked after leaving a selection with an accepted download', async () => {
        const pending = deferred<Reply>();
        loadProgress = async config => config?.params ? pending.promise : { data: { running: false } };
        render(page());
        await flush();
        fireEvent.click(screen.getByRole('button', { name: 'Download Models' }));
        await flush();
        fireEvent.click(screen.getByRole('button', { name: 'Back' }));
        fireEvent.click(screen.getByText('OpenAI Realtime'));
        fireEvent.click(screen.getByRole('button', { name: 'Next' }));
        await flush();
        fireEvent.click(screen.getByRole('button', { name: 'Next' }));
        await flush();
        expect(screen.getByRole('button', { name: 'Finish Setup' })).toBeDisabled();
        fireEvent.click(screen.getByRole('button', { name: 'Finish Setup' }));
        expect(vi.mocked(axios.post).mock.calls.some(([url]) => url.endsWith('/save'))).toBe(false);
        await act(async () => { pending.resolve({ data: { completed: true } }); });
        fireEvent.click(screen.getByRole('button', { name: 'Finish Setup' }));
        await flush();
        expect(axios.post).toHaveBeenCalledWith('/api/wizard/save', expect.objectContaining({ provider: 'openai_realtime' }));
    });

    it('keeps Kroko embedded mode consistent when automatic language reconciliation selects a new model', async () => {
        const stt = [
            { id: 'kroko-cloud', name: 'Kroko Cloud', backend: 'kroko', language: 'en-US', embedded: false },
            { id: 'kroko-uk', name: 'Kroko Ukrainian', backend: 'kroko', language: 'uk-UA', embedded: true }
        ];
        loadCatalog = async () => ({ data: { catalog: { stt, tts: [], llm: [] }, languages: {}, language_names: {}, region_names: {} } });
        render(page());
        await flush();
        fireEvent.change(screen.getByDisplayValue('English (US)'), { target: { value: 'uk-UA' } });
        await flush();
        fireEvent.click(screen.getByRole('button', { name: 'Download Models' }));
        expect(axios.post).toHaveBeenLastCalledWith('/api/wizard/local/download-selected-models', expect.objectContaining({ stt_model_id: 'kroko-uk', kroko_embedded: true }));
        await flush();
        fireEvent.change(screen.getByDisplayValue('Ukrainian'), { target: { value: 'en-US' } });
        await flush();
        fireEvent.click(screen.getByRole('button', { name: 'Download Models' }));
        expect(axios.post).toHaveBeenLastCalledWith('/api/wizard/local/download-selected-models', expect.objectContaining({ stt_model_id: 'kroko-cloud', kroko_embedded: false }));
    });

    it.each([
        { name: 'normal', building: false, downloading: false, timeout: 120000 },
        { name: 'model download', building: false, downloading: true, timeout: 600000 },
        { name: 'image build', building: true, downloading: false, timeout: 3600000 },
    ])('keeps the $name startup timeout and permits explicit retry on expiry', async ({ building, downloading, timeout }) => {
        vi.useFakeTimers();
        startServer = async () => ({ data: { success: true, building } });
        loadLogs = async () => ({ data: { ready: false, downloading, logs: [] } });
        await finishLocalSetup();
        const started = Date.now();
        if (timeout > 120000) {
            vi.setSystemTime(started + 121000);
            await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
            expect(requests('/api/wizard/local/server-logs')).toHaveLength(2);
            expect(screen.queryByRole('button', { name: 'Start Local AI Server' })).not.toBeInTheDocument();
        }
        vi.setSystemTime(started + timeout);
        await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
        expect(screen.getByRole('button', { name: 'Start Local AI Server' })).toBeEnabled();
        expect(vi.mocked(axios.post).mock.calls.filter(([url]) => url.endsWith('/start-server'))).toHaveLength(1);
    });

    it('ignores a submitted download receipt after unmount', async () => {
        vi.useFakeTimers();
        const pending = deferred<Reply>();
        vi.mocked(axios.post).mockReturnValue(pending.promise);
        const view = render(page());
        await flush();
        fireEvent.click(screen.getByRole('button', { name: 'Download Models' }));
        const before = requests('/api/wizard/local/download-progress').length;
        view.unmount();
        await act(async () => { pending.resolve({ data: { job_id: 'late-job' } }); });
        await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
        expect(requests('/api/wizard/local/download-progress')).toHaveLength(before);
        expect(vi.getTimerCount()).toBe(0);
    });

    it('does not start log polling after an accepted start completes off the page', async () => {
        vi.useFakeTimers();
        const pending = deferred<Reply>();
        startServer = () => pending.promise;
        const view = await finishLocalSetup();
        view.unmount();
        await act(async () => { pending.resolve({ data: { success: true } }); });
        await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
        expect(requests('/api/wizard/local/server-logs')).toHaveLength(0);
        expect(vi.getTimerCount()).toBe(0);
        expect(toast.error).not.toHaveBeenCalled();
    });

    it('prevents an old log response from reporting readiness for a new start attempt', async () => {
        vi.useFakeTimers();
        const oldLogs = deferred<Reply>();
        loadLogs = () => oldLogs.promise;
        await finishLocalSetup();
        const oldSignal = requests('/api/wizard/local/server-logs')[0][1]?.signal;
        fireEvent.click(screen.getByRole('button', { name: 'Edit setup settings' }));
        expect(oldSignal?.aborted).toBe(true);
        await flush();
        const newLogs = deferred<Reply>();
        loadLogs = () => newLogs.promise;
        fireEvent.click(screen.getByRole('button', { name: 'Finish Setup' }));
        await flush();
        expect(vi.mocked(axios.post).mock.calls.filter(([url]) => url.endsWith('/start-server'))).toHaveLength(2);
        await act(async () => { oldLogs.resolve({ data: { ready: true, logs: ['Obsolete ready'] } }); });
        expect(screen.queryByText('Server Ready!')).not.toBeInTheDocument();
        await act(async () => { newLogs.resolve({ data: { ready: true, logs: ['Current ready'] } }); });
        expect(screen.getByText('Server Ready!')).toBeInTheDocument();
    });

    it('cancels resumed download retries on unmount', async () => {
        vi.useFakeTimers();
        let reads = 0;
        loadProgress = async () => {
            if (++reads === 1) return { data: { running: true, job_id: 'retry-job' } };
            throw new Error('Synthetic disconnect');
        };
        const view = render(page());
        await flush();
        expect(reads).toBe(2);
        view.unmount();
        expect(vi.getTimerCount()).toBe(0);
        await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
        expect(reads).toBe(2);
    });

    it('releases an idle resumed job without falsely completing the current selection', async () => {
        let reads = 0;
        loadProgress = async () => ({ data: ++reads === 1 ? { running: true, job_id: 'vanished-job' } : { running: false } });
        render(page());
        await flush();
        expect(screen.getByRole('button', { name: 'Download Models' })).toBeEnabled();
        expect(screen.queryByRole('button', { name: 'Downloaded' })).not.toBeInTheDocument();
    });

    it('preserves manual backend choices when reconciling a new language', async () => {
        const stt = [
            { id: 'vosk-en', name: 'Vosk English', backend: 'vosk', language: 'en-US' },
            { id: 'whisper-multi', name: 'Whisper multilingual', backend: 'faster_whisper', language: 'multi' }
        ];
        const tts = [
            { id: 'piper-en', name: 'Piper English', backend: 'piper', language: 'en-US' },
            { id: 'silero-ua', name: 'Silero Ukrainian', backend: 'silero', language: 'uk-UA', speaker: 'mykyta' }
        ];
        loadCatalog = async () => ({ data: { catalog: { stt, tts, llm: [] }, languages: {}, language_names: {}, region_names: {} } });
        render(page());
        await flush();
        fireEvent.change(screen.getByDisplayValue('Vosk English (vosk) -'), { target: { value: 'whisper-multi' } });
        await flush();
        fireEvent.change(screen.getByDisplayValue('English (US)'), { target: { value: 'uk-UA' } });
        await flush();
        const draft = JSON.parse(sessionStorage.getItem('ava-onboarding-draft-v1') || '{}');
        expect(draft.config).toMatchObject({ local_stt_backend: 'faster_whisper', local_stt_model: 'whisper-multi', local_tts_backend: 'silero', local_tts_model: 'silero-ua' });
        fireEvent.click(screen.getByRole('button', { name: 'Download Models' }));
        expect(axios.post).toHaveBeenCalledWith('/api/wizard/local/download-selected-models', expect.objectContaining({ silero_speaker: 'mykyta', silero_language: 'ua' }));
    });
});
