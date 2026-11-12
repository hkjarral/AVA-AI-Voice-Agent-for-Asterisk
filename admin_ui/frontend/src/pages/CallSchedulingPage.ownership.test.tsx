// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios, { type AxiosRequestConfig } from 'axios';
import CallSchedulingPage from './CallSchedulingPage';

const mocks = vi.hoisted(() => ({ confirm: vi.fn(), success: vi.fn(), error: vi.fn() }));
vi.mock('axios');
vi.mock('../hooks/useConfirmDialog', () => ({ useConfirmDialog: () => ({ confirm: mocks.confirm }) }));
vi.mock('sonner', () => ({ toast: { success: mocks.success, error: mocks.error, info: vi.fn() } }));
type Reply = { data: unknown };
const deferred = <T,>() => {
    let resolve!: (value: T) => void;
    let reject!: (reason: unknown) => void;
    const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
};
const campaign = (id: string) => ({ id, name: `Fixture campaign ${id}`, status: 'paused', timezone: 'UTC', daily_window_start_local: '00:00', daily_window_end_local: '23:59', max_concurrent: 1, voicemail_drop_enabled: false, consent_enabled: false });
const lead = (id: string, label = id) => ({ id: `lead-${id}`, campaign_id: id, name: `Synthetic lead ${label}`, phone_number: `synthetic-${id}`, state: 'pending', attempt_count: 0 });
const stats = (pending = 3, canceled = 0): Reply => ({ data: { lead_states: { pending, canceled } } });
const rows = (id: string, label = id, pages = 2): Reply => ({ data: { leads: [lead(id, label)], total_pages: pages } });
let loadStats: (id: string) => Promise<Reply>;
let loadLeads: (id: string, config?: AxiosRequestConfig) => Promise<Reply>;
let loadCampaigns: () => Promise<Reply>;
let poll: (() => Promise<void>) | null;

beforeEach(() => {
    vi.resetAllMocks();
    mocks.confirm.mockResolvedValue(true);
    poll = null;
    const interval = globalThis.setInterval;
    vi.spyOn(globalThis, 'setInterval').mockImplementation((handler, delay, ...args) => {
        if (delay === 5000) {
            poll = handler as () => Promise<void>;
            return 0 as unknown as ReturnType<typeof setInterval>;
        }
        return interval(handler, delay, ...args);
    });
    loadStats = async () => stats();
    loadLeads = async id => rows(id);
    loadCampaigns = async () => ({ data: [campaign('A'), campaign('B')] });
    vi.mocked(axios.get).mockImplementation(async (url, config) => {
        if (url === '/api/outbound/meta') return { data: { agents: [], iana_timezones: ['UTC'], server_timezone: 'UTC' } };
        if (url === '/api/outbound/recordings') return { data: [] };
        if (url === '/api/outbound/campaigns') return loadCampaigns();
        const match = url.match(/^\/api\/outbound\/campaigns\/([^/]+)\/(stats|leads)$/);
        if (match) return match[2] === 'stats' ? loadStats(match[1]) : loadLeads(match[1], config);
        throw new Error(`Unexpected fixture GET: ${url}`);
    });
    vi.mocked(axios.post).mockResolvedValue({ data: {} });
    vi.mocked(axios.delete).mockResolvedValue({ data: {} });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const select = (id: string) => fireEvent.click(screen.getByRole('button', { name: new RegExp(`Fixture campaign ${id}`) }));
const latestSignal = (id: string) => vi.mocked(axios.get).mock.calls.filter(([url]) => url === `/api/outbound/campaigns/${id}/leads`).slice(-1)[0]?.[1]?.signal;
const flush = async (work: () => void) => act(async () => { work(); });

describe('campaign lead query ownership', () => {
    it('hides old rows immediately and ignores a late A success after B is ready', async () => {
        const oldA = deferred<Reply>();
        let initialA = true;
        loadLeads = async id => id === 'A' && !initialA ? oldA.promise : rows(id);
        render(<CallSchedulingPage />);
        await screen.findByText('Synthetic lead A');
        initialA = false;
        await act(async () => { void poll?.(); });
        await waitFor(() => expect(vi.mocked(axios.get).mock.calls.filter(([url]) => url === '/api/outbound/campaigns/A/leads')).toHaveLength(2));
        select('B');
        expect(screen.queryByText('Synthetic lead A')).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Ignore' })).not.toBeInTheDocument();
        await screen.findByText('Synthetic lead B');
        await flush(() => oldA.resolve(rows('A', 'late A')));
        expect(screen.queryByText('Synthetic lead late A')).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Ignore' }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledWith('/api/outbound/leads/lead-B/ignore'));
        expect(axios.post).not.toHaveBeenCalledWith('/api/outbound/leads/lead-A/ignore');
    });

    it('keeps an initial A request from committing after an A to B to A round trip', async () => {
        const first = deferred<Reply>();
        let aRequests = 0;
        loadLeads = async id => id === 'A' && ++aRequests === 1 ? first.promise : rows(id, id === 'A' ? 'new A' : id);
        render(<CallSchedulingPage />);
        await screen.findByRole('button', { name: /Fixture campaign B/ });
        await waitFor(() => expect(latestSignal('A')).toBeDefined());
        const firstSignal = latestSignal('A');
        select('B');
        expect(firstSignal?.aborted).toBe(true);
        await screen.findByText('Synthetic lead B');
        select('A');
        await screen.findByText('Synthetic lead new A');
        await flush(() => first.resolve(rows('A', 'old A')));
        expect(screen.queryByText('Synthetic lead old A')).not.toBeInTheDocument();
        expect(screen.getByText('Synthetic lead new A')).toBeInTheDocument();
    });

    it('does not post a confirmed delete after the operator changes campaign', async () => {
        const confirmation = deferred<boolean>();
        mocks.confirm.mockReturnValue(confirmation.promise);
        render(<CallSchedulingPage />);
        await screen.findByText('Synthetic lead A');
        fireEvent.click(within(screen.getByText('Synthetic lead A').closest('tr')!).getByRole('button', { name: 'Delete' }));
        await waitFor(() => expect(mocks.confirm).toHaveBeenCalledTimes(1));
        select('B');
        await screen.findByText('Synthetic lead B');
        await flush(() => confirmation.resolve(true));
        expect(axios.delete).not.toHaveBeenCalled();
    });

    it('discards mutation completion from A after navigating to B', async () => {
        const mutation = deferred<Reply>();
        vi.mocked(axios.post).mockReturnValue(mutation.promise);
        render(<CallSchedulingPage />);
        await screen.findByText('Synthetic lead A');
        fireEvent.click(screen.getByRole('button', { name: 'Ignore' }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        select('B');
        await screen.findByText('Synthetic lead B');
        const reads = vi.mocked(axios.get).mock.calls.length;
        await flush(() => mutation.resolve({ data: {} }));
        expect(vi.mocked(axios.get).mock.calls).toHaveLength(reads);
        expect(mocks.success).not.toHaveBeenCalled();
        expect(screen.getByText('Synthetic lead B')).toBeInTheDocument();
    });
});

describe('query refresh lifecycle', () => {
    it.each(['search', 'state', 'page'] as const)('owns the complete %s query and ignores its late failure', async kind => {
        render(<CallSchedulingPage />);
        await screen.findByText('Synthetic lead A');
        const old = deferred<Reply>();
        let reads = 0;
        loadLeads = async id => ++reads === 1 ? old.promise : rows(id, 'current query');
        const change = (first: boolean) => {
            if (kind === 'search') fireEvent.change(screen.getByLabelText('Search campaign leads'), { target: { value: first ? 'one' : 'two' } });
            else if (kind === 'state') fireEvent.change(screen.getByLabelText('Lead state filter'), { target: { value: first ? 'pending' : 'failed' } });
            else fireEvent.click(screen.getByRole('button', { name: first ? 'Next' : 'Prev' }));
        };
        change(true);
        expect(screen.queryByText('Synthetic lead A')).not.toBeInTheDocument();
        await waitFor(() => expect(reads).toBe(1));
        const oldSignal = latestSignal('A');
        change(false);
        expect(oldSignal?.aborted).toBe(true);
        await screen.findByText('Synthetic lead current query');
        await flush(() => old.reject(new Error('obsolete query failure')));
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(screen.getByText('Synthetic lead current query')).toBeInTheDocument();
    });

    it('lets manual refresh supersede a pending refresh of the same campaign', async () => {
        render(<CallSchedulingPage />);
        await screen.findByText('Synthetic lead A');
        const old = deferred<Reply>();
        let reads = 0;
        loadLeads = async id => ++reads === 1 ? old.promise : rows(id, 'new refresh');
        fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
        await waitFor(() => expect(reads).toBe(1));
        const oldSignal = latestSignal('A');
        fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
        await screen.findByText('Synthetic lead new refresh');
        expect(oldSignal?.aborted).toBe(true);
        await flush(() => old.resolve(rows('A', 'old refresh')));
        expect(screen.queryByText('Synthetic lead old refresh')).not.toBeInTheDocument();
    });

    it('skips overlapping polls while a slow request is pending', async () => {
        const slow = deferred<Reply>();
        loadLeads = async () => slow.promise;
        render(<CallSchedulingPage />);
        await screen.findByRole('button', { name: /Fixture campaign A/ });
        await waitFor(() => expect(latestSignal('A')).toBeDefined());
        const signal = latestSignal('A');
        await act(async () => { await poll?.(); await poll?.(); });
        expect(vi.mocked(axios.get).mock.calls.filter(([url]) => url === '/api/outbound/campaigns/A/leads')).toHaveLength(1);
        expect(signal?.aborted).toBe(false);
        await flush(() => slow.resolve(rows('A')));
        await screen.findByText('Synthetic lead A');
    });

    it('shows failure without stale rows or zero-lead guidance, and retries only the read', async () => {
        const failed = deferred<Reply>();
        loadLeads = async id => id === 'B' ? failed.promise : rows(id);
        render(<CallSchedulingPage />);
        await screen.findByText('Synthetic lead A');
        select('B');
        expect(screen.queryByText('Synthetic lead A')).not.toBeInTheDocument();
        expect(screen.queryByText('No pending leads')).not.toBeInTheDocument();
        await flush(() => failed.reject(new Error('Read timed out')));
        expect(await screen.findByRole('alert')).toHaveTextContent('Read timed out');
        expect(screen.queryByRole('button', { name: 'Ignore' })).not.toBeInTheDocument();
        loadLeads = async id => rows(id);
        fireEvent.click(screen.getByRole('button', { name: 'Retry campaign details' }));
        await screen.findByText('Synthetic lead B');
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('does not restore a stale selection after an old poll finishes its list request', async () => {
        render(<CallSchedulingPage />);
        await screen.findByText('Synthetic lead A');
        const oldList = deferred<Reply>();
        loadCampaigns = async () => oldList.promise;
        await act(async () => { void poll?.(); });
        await waitFor(() => expect(vi.mocked(axios.get).mock.calls.filter(([url]) => url === '/api/outbound/campaigns')).toHaveLength(2));
        select('B');
        await screen.findByText('Synthetic lead B');
        const aReads = vi.mocked(axios.get).mock.calls.filter(([url]) => url === '/api/outbound/campaigns/A/leads').length;
        await flush(() => oldList.resolve({ data: [campaign('A'), campaign('B')] }));
        expect(screen.getByRole('button', { name: /Fixture campaign B/ })).toHaveClass('bg-primary/5');
        expect(vi.mocked(axios.get).mock.calls.filter(([url]) => url === '/api/outbound/campaigns/A/leads')).toHaveLength(aReads);
        expect(screen.getByText('Synthetic lead B')).toBeInTheDocument();
    });

    it('invalidates a delete confirmation even after returning to the original campaign', async () => {
        const confirmation = deferred<boolean>();
        mocks.confirm.mockReturnValue(confirmation.promise);
        render(<CallSchedulingPage />);
        await screen.findByText('Synthetic lead A');
        fireEvent.click(within(screen.getByText('Synthetic lead A').closest('tr')!).getByRole('button', { name: 'Delete' }));
        await waitFor(() => expect(mocks.confirm).toHaveBeenCalledTimes(1));
        select('B'); await screen.findByText('Synthetic lead B');
        select('A'); await screen.findByText('Synthetic lead A');
        await flush(() => confirmation.resolve(true));
        expect(axios.delete).not.toHaveBeenCalled();
    });

    it('permits confirmed deletion for the current owned row', async () => {
        render(<CallSchedulingPage />);
        await screen.findByText('Synthetic lead A');
        loadLeads = async () => ({ data: { leads: [], total_pages: 1 } });
        fireEvent.click(within(screen.getByText('Synthetic lead A').closest('tr')!).getByRole('button', { name: 'Delete' }));
        await waitFor(() => expect(axios.delete).toHaveBeenCalledWith('/api/outbound/leads/lead-A'));
        await screen.findByText(/No leads yet/);
        expect(mocks.success).toHaveBeenCalledWith('Lead deleted');
    });

    it('aborts and invalidates pending reads on unmount', async () => {
        const slow = deferred<Reply>();
        loadLeads = async () => slow.promise;
        const view = render(<CallSchedulingPage />);
        await screen.findByRole('button', { name: /Fixture campaign A/ });
        await waitFor(() => expect(latestSignal('A')).toBeDefined());
        const signal = latestSignal('A');
        const reads = vi.mocked(axios.get).mock.calls.length;
        view.unmount();
        expect(signal?.aborted).toBe(true);
        await flush(() => slow.resolve(rows('A')));
        expect(vi.mocked(axios.get).mock.calls).toHaveLength(reads);
    });
});

describe('campaign statistics readiness', () => {
    it('treats loading statistics as unknown and uses fresh positive statistics', async () => {
        const fresh = deferred<Reply>();
        loadStats = async () => fresh.promise;
        render(<CallSchedulingPage />);
        await screen.findByRole('button', { name: /Fixture campaign A/ });
        expect(screen.queryByText('No pending leads')).not.toBeInTheDocument();
        await flush(() => fresh.resolve(stats(3)));
        await screen.findByText('Synthetic lead A');
        expect(screen.queryByText('No pending leads')).not.toBeInTheDocument();
    });

    it('updates missing-lead guidance after a mutation refresh without replacing the campaign', async () => {
        render(<CallSchedulingPage />);
        await screen.findByText('Synthetic lead A');
        expect(screen.queryByText(/No pending leads/)).not.toBeInTheDocument();
        loadStats = async () => stats(0, 4);
        loadLeads = async () => ({ data: { leads: [], total_pages: 1 } });
        fireEvent.click(screen.getByRole('button', { name: 'Ignore' }));
        await screen.findByText('No pending leads (canceled: 4)');
        expect(vi.mocked(axios.get).mock.calls.filter(([url]) => url === '/api/outbound/campaigns')).toHaveLength(1);
    });
});

describe('campaign removal and recycle ownership', () => {
    it('closes recycle when its campaign disappears during a poll', async () => {
        render(<CallSchedulingPage />);
        await screen.findByText('Synthetic lead A');
        fireEvent.click(screen.getByRole('button', { name: 'Recycle' }));
        await screen.findByRole('dialog', { name: 'Recycle Lead' });
        loadCampaigns = async () => ({ data: [campaign('B')] });
        await act(async () => { await poll?.(); });
        await screen.findByText('Synthetic lead B');
        expect(screen.queryByRole('dialog', { name: 'Recycle Lead' })).not.toBeInTheDocument();
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('allows recycling an owned current lead', async () => {
        render(<CallSchedulingPage />);
        await screen.findByText('Synthetic lead A');
        fireEvent.click(screen.getByRole('button', { name: 'Recycle' }));
        const dialog = await screen.findByRole('dialog', { name: 'Recycle Lead' });
        fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm' }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledWith('/api/outbound/leads/lead-A/recycle', { mode: 'redial' }));
        await waitFor(() => expect(mocks.success).toHaveBeenCalledWith('Lead re-queued for re-dial'));
    });

    it('rejects an old archive-filter list response', async () => {
        render(<CallSchedulingPage />);
        await screen.findByText('Synthetic lead A');
        const old = deferred<Reply>();
        loadCampaigns = async () => old.promise;
        fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
        await waitFor(() => expect(vi.mocked(axios.get).mock.calls.filter(([url]) => url === '/api/outbound/campaigns')).toHaveLength(2));
        const signal = vi.mocked(axios.get).mock.calls.filter(([url]) => url === '/api/outbound/campaigns').slice(-1)[0]?.[1]?.signal;
        loadCampaigns = async () => ({ data: [campaign('B')] });
        fireEvent.click(screen.getByRole('checkbox', { name: 'Show archived' }));
        await screen.findByText('Synthetic lead B');
        expect(signal?.aborted).toBe(true);
        await flush(() => old.resolve({ data: [campaign('A'), campaign('B')] }));
        expect(screen.queryByRole('button', { name: /Fixture campaign A/ })).not.toBeInTheDocument();
        expect(screen.getByText('Synthetic lead B')).toBeInTheDocument();
    });
});
