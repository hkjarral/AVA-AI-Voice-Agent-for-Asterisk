// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { toast } from 'sonner';
import EnvPage from './EnvPage';

vi.mock('axios');
const mocks = vi.hoisted(() => ({
    auth: { token: 'synthetic-A', loading: false }, confirm: vi.fn(),
}));
vi.mock('../../auth/AuthContext', () => ({ useAuth: () => mocks.auth }));
vi.mock('../../hooks/useConfirmDialog', () => ({ useConfirmDialog: () => ({ confirm: mocks.confirm }) }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() } }));
type Reply = { data: unknown };
const deferred = <T,>() => {
    let resolve!: (value: T) => void;
    let reject!: (reason: unknown) => void;
    const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
};
const plan = [{ service: 'ai_engine', method: 'recreate', endpoint: '/api/system/containers/ai_engine/restart' }];
const env = { CALL_HISTORY_TOOL_REDACTION_MODE: 'strict', ASTERISK_ARI_PORT: '8088' };
let readEnv: () => Promise<Reply>;
let readStatus: () => Promise<Reply>;
let readYaml: () => Promise<Reply>;
const page = () => <MemoryRouter><EnvPage /></MemoryRouter>;
const flush = async () => { await act(async () => {}); };
const calls = (url: string) => vi.mocked(axios.get).mock.calls.filter(([path]) => path === url);
beforeEach(() => {
    vi.resetAllMocks();
    mocks.auth = { token: 'synthetic-A', loading: false };
    mocks.confirm.mockResolvedValue(false);
    window.history.replaceState(null, '', '/env#system');
    Element.prototype.scrollIntoView = vi.fn();
    readEnv = async () => ({ data: env });
    readStatus = async () => ({ data: { pending_restart: true, apply_plan: plan } });
    readYaml = async () => ({ data: { providers: {} } });
    vi.mocked(axios.get).mockImplementation(async url => {
        if (url === '/api/config/env') return readEnv();
        if (url === '/api/config/env/status') return readStatus();
        if (url === '/api/config/yaml') return readYaml();
        if (url === '/api/config') return { data: { providers: {} } };
        throw new Error('Unexpected fixture GET: ' + url);
    });
    vi.mocked(axios.post).mockResolvedValue({ data: { status: 'success', apply_plan: plan, changed_keys: [] } });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('Environment action ownership', () => {
    it('requires a refresh after a scheduled Admin restart rather than claiming readiness', async () => {
        mocks.confirm.mockResolvedValue(true);
        readStatus = async () => ({ data: { apply_plan: [{ service: 'admin_ui', method: 'restart', endpoint: '/api/system/containers/admin_ui/restart' }], pending_restart: true } });
        render(page());
        await flush();
        fireEvent.click(screen.getByRole('button', { name: 'Apply Changes' }));
        await flush();
        expect(calls('/api/config/env/status')).toHaveLength(1);
        expect(screen.getByRole('button', { name: 'Apply Changes' })).toBeDisabled();
        expect(toast.info).toHaveBeenCalledWith('Admin UI restart requested. Refresh after it returns to verify pending changes.');
        expect(toast.success).not.toHaveBeenCalledWith('Changes applied');
    });

    it('does not proceed to the engine when Local AI reports a warning', async () => {
        readStatus = async () => ({ data: { apply_plan: [{ service: 'local_ai_server', method: 'recreate', endpoint: '/api/system/containers/local_ai_server/restart' }, ...plan], pending_restart: true } });
        vi.mocked(axios.post).mockResolvedValue({ data: { status: 'warning', message: 'Synthetic active-call guard' } });
        render(page());
        await flush();
        fireEvent.click(screen.getByRole('button', { name: 'Apply Changes' }));
        await flush();
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(screen.getByRole('button', { name: 'Apply Changes' })).toBeDisabled();
        expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled();
        expect(toast.success).not.toHaveBeenCalledWith('Changes applied');
    });

    it('requires current status after an apply error before another restart can be requested', async () => {
        vi.mocked(axios.post).mockRejectedValue(new Error('Synthetic lost response'));
        render(page());
        await flush();
        fireEvent.click(screen.getByRole('button', { name: 'Apply Changes' }));
        await flush();
        expect(screen.getByRole('button', { name: 'Apply Changes' })).toBeDisabled();
        expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled();
    });

    it.each([{ label: 'empty', pending: [] }, { label: 'existing', pending: plan }])('rechecks drift after a lost Save response with an $label plan', async ({ pending: initialPlan }) => {
        vi.spyOn(console, 'error').mockImplementation(() => {});
        const save = deferred<Reply>();
        readStatus = async () => ({ data: { apply_plan: initialPlan, pending_restart: initialPlan.length > 0 } });
        vi.mocked(axios.post).mockReturnValueOnce(save.promise);
        render(page());
        await flush();
        fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
        await flush();
        expect(screen.getByRole('button', { name: 'Refresh' })).toBeDisabled();
        await act(async () => { save.reject(new Error('Synthetic lost save response')); });
        expect(screen.getByRole('button', { name: 'Apply Changes' })).toBeDisabled();
        expect(screen.getByText(/Apply status is unknown/)).toBeInTheDocument();
        if (initialPlan.length) expect(screen.getByText('Pending changes require restart of: ai_engine')).toBeInTheDocument();
        readStatus = async () => ({ data: { apply_plan: [{ service: 'admin_ui', method: 'restart', endpoint: '/api/system/containers/admin_ui/restart' }], pending_restart: true } });
        fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
        await flush();
        expect(screen.getByRole('button', { name: 'Apply Changes' })).toBeEnabled();
        expect(screen.getByText('Pending changes require restart of: admin_ui')).toBeInTheDocument();
        expect(axios.post).toHaveBeenCalledTimes(1);
    });

    it('serializes Save/Refresh behind Apply and preserves current backend drift afterward', async () => {
        const pending = deferred<Reply>();
        vi.mocked(axios.post).mockReturnValue(pending.promise);
        render(page());
        await flush();
        fireEvent.click(screen.getByRole('button', { name: 'Apply Changes' }));
        await flush();
        expect(screen.getByRole('button', { name: 'Save Changes' })).toBeDisabled();
        expect(screen.getByRole('button', { name: 'Refresh' })).toBeDisabled();
        expect(screen.getByLabelText('Tool Diagnostic Redaction')).toBeDisabled();
        fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
        fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(calls('/api/config/env')).toHaveLength(1);
        await act(async () => { pending.resolve({ data: { status: 'success' } }); });
        expect(screen.getByRole('button', { name: 'Apply Changes' })).toBeEnabled();
        expect(screen.getByText('Pending changes require restart of: ai_engine')).toBeInTheDocument();
        expect(toast.success).not.toHaveBeenCalledWith('Changes applied');
    });

    it('clears the pending plan only after a successful drift recheck', async () => {
        render(page());
        await flush();
        readStatus = async () => ({ data: { apply_plan: [], pending_restart: false } });
        fireEvent.click(screen.getByRole('button', { name: 'Apply Changes' }));
        await flush();
        expect(screen.getByRole('button', { name: 'Apply Changes' })).toBeDisabled();
        expect(toast.success).toHaveBeenCalledWith('Changes applied');
    });

    it('keeps apply status unknown when verification fails and recovers on Refresh', async () => {
        render(page());
        await flush();
        readStatus = async () => { throw new Error('Synthetic status failure'); };
        fireEvent.click(screen.getByRole('button', { name: 'Apply Changes' }));
        await flush();
        expect(screen.getByRole('button', { name: 'Apply Changes' })).toBeDisabled();
        expect(screen.getByText(/Apply status is unknown/)).toBeInTheDocument();
        expect(toast.success).not.toHaveBeenCalledWith('Changes applied');
        readStatus = async () => ({ data: { apply_plan: plan, pending_restart: true } });
        fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
        await flush();
        expect(screen.getByRole('button', { name: 'Apply Changes' })).toBeEnabled();
    });

    it('locks the draft before redaction consent and does not submit after unmount', async () => {
        const consent = deferred<boolean>();
        mocks.confirm.mockReturnValue(consent.promise);
        const view = render(page());
        await flush();
        fireEvent.change(screen.getByLabelText('Tool Diagnostic Redaction'), { target: { value: 'off' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
        expect(screen.getByRole('button', { name: 'Refresh' })).toBeDisabled();
        fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
        expect(mocks.confirm).toHaveBeenCalledTimes(1);
        view.unmount();
        await act(async () => { consent.resolve(true); });
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('releases a canceled confirmation without discarding the draft', async () => {
        render(page());
        await flush();
        fireEvent.change(screen.getByLabelText('Tool Diagnostic Redaction'), { target: { value: 'off' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
        await flush();
        expect(axios.post).not.toHaveBeenCalled();
        expect(screen.getByRole('button', { name: 'Save Changes' })).toBeEnabled();
        expect(screen.getByLabelText('Tool Diagnostic Redaction')).toHaveValue('off');
    });

    it('does not use consent from a previous authentication scope', async () => {
        const consent = deferred<boolean>();
        mocks.confirm.mockReturnValue(consent.promise);
        const view = render(page());
        await flush();
        fireEvent.change(screen.getByLabelText('Tool Diagnostic Redaction'), { target: { value: 'off' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
        mocks.auth = { token: 'synthetic-B', loading: false };
        view.rerender(page());
        await flush();
        await act(async () => { consent.resolve(true); });
        expect(axios.post).not.toHaveBeenCalled();
        expect(screen.getByLabelText('Tool Diagnostic Redaction')).toHaveValue('strict');
    });

    it('does not issue follow-up reads or show success for an accepted save after navigation', async () => {
        const pending = deferred<Reply>();
        vi.mocked(axios.post).mockReturnValue(pending.promise);
        const view = render(page());
        await flush();
        fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
        const reads = vi.mocked(axios.get).mock.calls.length;
        view.unmount();
        await act(async () => { pending.resolve({ data: { apply_plan: plan } }); });
        expect(vi.mocked(axios.get).mock.calls).toHaveLength(reads);
        expect(toast.success).not.toHaveBeenCalled();
    });

    it('does not submit a delayed Admin restart confirmation after navigation', async () => {
        const consent = deferred<boolean>();
        mocks.confirm.mockReturnValue(consent.promise);
        readStatus = async () => ({ data: { apply_plan: [{ service: 'admin_ui', method: 'restart', endpoint: '/api/system/containers/admin_ui/restart' }], pending_restart: true } });
        const view = render(page());
        await flush();
        fireEvent.click(screen.getByRole('button', { name: 'Apply Changes' }));
        view.unmount();
        await act(async () => { consent.resolve(true); });
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('does not start later steps when an accepted apply finishes after navigation', async () => {
        const pending = deferred<Reply>();
        readStatus = async () => ({ data: { apply_plan: [{ service: 'local_ai_server', method: 'recreate', endpoint: '/api/system/containers/local_ai_server/restart' }, ...plan], pending_restart: true } });
        vi.mocked(axios.post).mockReturnValue(pending.promise);
        const view = render(page());
        await flush();
        fireEvent.click(screen.getByRole('button', { name: 'Apply Changes' }));
        view.unmount();
        await act(async () => { pending.resolve({ data: { status: 'success' } }); });
        expect(axios.post).toHaveBeenCalledTimes(1);
    });

    it('keeps cumulative drift unknown when status fails after a save', async () => {
        render(page());
        await flush();
        readStatus = async () => { throw new Error('Synthetic unavailable'); };
        fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
        await flush();
        expect(screen.getByRole('button', { name: 'Apply Changes' })).toBeDisabled();
        expect(screen.getByText(/Apply status is unknown/)).toBeInTheDocument();
        expect(screen.getByText('Pending changes require restart of: ai_engine')).toBeInTheDocument();
    });

    it('does not erase earlier pending services with an empty no-op save response', async () => {
        render(page());
        await flush();
        readStatus = async () => { throw new Error('Synthetic unavailable'); };
        vi.mocked(axios.post).mockResolvedValue({ data: { apply_plan: [], changed_keys: [] } });
        fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
        await flush();
        expect(screen.getByText('Pending changes require restart of: ai_engine')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Apply Changes' })).toBeDisabled();
        expect(toast.success).not.toHaveBeenCalled();
    });

    it('rechecks drift before retrying a partial Apply so Local AI is not recreated twice', async () => {
        const local = { service: 'local_ai_server', method: 'recreate', endpoint: '/api/system/containers/local_ai_server/restart' };
        readStatus = async () => ({ data: { apply_plan: [local, ...plan], pending_restart: true } });
        vi.mocked(axios.post).mockResolvedValueOnce({ data: { status: 'success' } }).mockResolvedValueOnce({ data: { status: 'warning', message: 'Synthetic active calls' } });
        render(page());
        await flush();
        fireEvent.click(screen.getByRole('button', { name: 'Apply Changes' }));
        await flush();
        expect(screen.getByRole('button', { name: 'Apply Changes' })).toBeDisabled();
        readStatus = async () => ({ data: { apply_plan: plan, pending_restart: true } });
        fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
        await flush();
        readStatus = async () => ({ data: { apply_plan: [], pending_restart: false } });
        fireEvent.click(screen.getByRole('button', { name: 'Apply Changes' }));
        await flush();
        expect(vi.mocked(axios.post).mock.calls.filter(([url]) => url.includes('/local_ai_server/'))).toHaveLength(1);
        expect(vi.mocked(axios.post).mock.calls.filter(([url]) => url.includes('/ai_engine/'))).toHaveLength(2);
    });
});

describe('Environment read ownership', () => {
    it('aborts old authentication reads and ignores their late values and follow-ups', async () => {
        const oldEnv = deferred<Reply>();
        readEnv = () => oldEnv.promise;
        const view = render(page());
        await flush();
        const signal = calls('/api/config/env')[0][1]?.signal;
        readEnv = async () => ({ data: { ...env, CALL_HISTORY_TOOL_REDACTION_MODE: 'show_routing' } });
        mocks.auth = { token: 'synthetic-B', loading: false };
        view.rerender(page());
        await flush();
        expect(signal?.aborted).toBe(true);
        const statusReads = calls('/api/config/env/status').length;
        await act(async () => { oldEnv.resolve({ data: env }); });
        expect(screen.getByLabelText('Tool Diagnostic Redaction')).toHaveValue('show_routing');
        expect(calls('/api/config/env/status')).toHaveLength(statusReads);
    });

    it('does not fan out credential reads after unmount', async () => {
        const pending = deferred<Reply>();
        readYaml = () => pending.promise;
        const view = render(page());
        await flush();
        const signal = calls('/api/config/yaml')[0][1]?.signal;
        const before = vi.mocked(axios.get).mock.calls.length;
        view.unmount();
        await act(async () => { pending.resolve({ data: { content: 'providers: {}' } }); });
        expect(signal?.aborted).toBe(true);
        expect(vi.mocked(axios.get).mock.calls).toHaveLength(before);
    });
});
