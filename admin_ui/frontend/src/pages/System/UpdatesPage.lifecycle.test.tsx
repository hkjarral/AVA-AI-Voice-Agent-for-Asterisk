// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios, { type AxiosError, type AxiosRequestConfig } from 'axios';
import UpdatesPage from './UpdatesPage';

const mocks = vi.hoisted(() => ({ confirm: vi.fn() }));
vi.mock('axios');
vi.mock('../../hooks/useConfirmDialog', () => ({ useConfirmDialog: () => ({ confirm: mocks.confirm }) }));
type Reply = { data: unknown };
const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const plan = (ref: string): Reply => ({ data: { plan: {
  ref, relation: 'behind', dirty: false, active_calls: 0, active_calls_reachable: true,
  services_rebuild: [`synthetic-${ref}`], services_restart: [], changed_file_count: 1,
} } });
const failedJob = { job_id: 'old-job', status: 'failed', pre_update_branch: 'synthetic-backup', backup_dir_rel: 'synthetic-backup-dir' };
let loadPlan: (config?: AxiosRequestConfig) => Promise<Reply>;
let loadJob: (id: string) => Promise<Reply>;
let loadFullLog: () => Promise<Reply>;
let loadHistory: () => Promise<Reply>;
let loadBranches: () => Promise<Reply>;
let loadStatus: () => Promise<Reply>;
let poll: (() => Promise<unknown>) | null;

beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  mocks.confirm.mockResolvedValue(true);
  vi.mocked(axios.isAxiosError).mockImplementation((error): error is AxiosError => Boolean(error?.isAxiosError));
  poll = null;
  for (const method of ['setInterval', 'setTimeout'] as const) {
    const original = window[method];
    vi.spyOn(window, method).mockImplementation(((handler: TimerHandler, delay?: number, ...args: unknown[]) => {
      if (delay === 2000) {
        poll = handler as () => Promise<unknown>;
        return 1;
      }
      return original(handler, delay, ...args);
    }) as typeof window[typeof method]);
  }
  loadPlan = async config => plan(config?.params.ref || 'v-fixture');
  loadJob = async id => ({ data: { job: { ...failedJob, job_id: id }, log_tail: `synthetic log ${id}` } });
  loadFullLog = async () => ({ data: { log: 'synthetic full old log' } });
  loadHistory = async () => ({ data: { jobs: [failedJob] } });
  loadBranches = async () => ({ data: { branches: ['main', 'fixture-branch'] } });
  loadStatus = async () => ({ data: { local: { branch: 'main', head_sha: 'synthetic-old', describe: 'fixture' }, remote: { latest_tag: 'v-fixture' } } });
  vi.mocked(axios.get).mockImplementation(async (url, config) => {
    if (url === '/api/system/updates/status') return loadStatus();
    if (url === '/api/system/updates/plan') return loadPlan(config);
    if (url === '/api/system/updates/history') return loadHistory();
    if (url === '/api/system/updates/branches') return loadBranches();
    if (url === '/api/system/updates/updater-image/status') return { data: { status: { status: 'idle' } } };
    if (url.endsWith('/log')) return loadFullLog();
    const match = url.match(/^\/api\/system\/updates\/jobs\/([^/]+)$/);
    if (match) return loadJob(match[1]);
    throw new Error(`Unexpected fixture GET: ${url}`);
  });
  vi.mocked(axios.post).mockResolvedValue({ data: { job_id: 'new-job' } });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const main = () => fireEvent.click(screen.getByRole('radio', { name: /Main \(hotfixes\)/ }));
const stable = () => fireEvent.click(screen.getByRole('radio', { name: /Stable/ }));
const proceed = () => screen.getByRole('button', { name: 'Proceed' });
const check = async () => {
  fireEvent.click(screen.getByRole('button', { name: 'Check updates' }));
  await screen.findByText('synthetic-v-fixture', {}, { timeout: 5000 });
};
const flush = async (work: () => void) => act(async () => { work(); });
const jobReads = (id: string) => vi.mocked(axios.get).mock.calls.filter(([url]) => url === `/api/system/updates/jobs/${id}`);
const tick = async () => act(async () => { await poll?.(); });
const readFailure = (status: number) => Object.assign(new Error(`Synthetic HTTP ${status}`), { isAxiosError: true, response: { status, data: {} } });

describe('update preview ownership', () => {
  it('ignores a late release preview after the main preview is ready', async () => {
    const old = deferred<Reply>();
    loadPlan = async config => config?.params.ref === 'v-fixture' ? old.promise : plan('main');
    render(<UpdatesPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Check updates' }));
    await waitFor(() => expect(axios.get).toHaveBeenCalledWith('/api/system/updates/plan', expect.anything()));
    main();
    await screen.findByText('synthetic-main');
    await flush(() => old.resolve(plan('v-fixture')));
    expect(screen.queryByText('synthetic-v-fixture')).not.toBeInTheDocument();
    expect(screen.getByText('synthetic-main')).toBeInTheDocument();
  });

  it('hides a previous preview and blocks Proceed while the next preview is pending', async () => {
    render(<UpdatesPage />);
    await check();
    const pending = deferred<Reply>();
    loadPlan = async () => pending.promise;
    main();
    expect(screen.queryByText('synthetic-v-fixture')).not.toBeInTheDocument();
    expect(proceed()).toBeDisabled();
    await flush(() => pending.resolve(plan('main')));
    await screen.findByText('synthetic-main');
    expect(proceed()).toBeEnabled();
  });

  it('invalidates confirmation if the target changes before acceptance', async () => {
    render(<UpdatesPage />);
    await check();
    const confirmation = deferred<boolean>();
    mocks.confirm.mockReturnValue(confirmation.promise);
    fireEvent.click(proceed());
    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledTimes(1));
    main();
    await screen.findByText('synthetic-main');
    await flush(() => confirmation.resolve(true));
    expect(axios.post).not.toHaveBeenCalled();
  });

  it('rejects an obsolete preview after a release to main to release round trip', async () => {
    const old = deferred<Reply>();
    let releases = 0;
    loadPlan = async config => config?.params.ref === 'v-fixture' && ++releases === 1 ? old.promise : plan(config?.params.ref);
    render(<UpdatesPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Check updates' }));
    await waitFor(() => expect(releases).toBe(1));
    main(); await screen.findByText('synthetic-main');
    stable(); await screen.findByText('synthetic-v-fixture');
    await flush(() => old.reject(new Error('Obsolete release failure')));
    expect(screen.queryByText('Obsolete release failure')).not.toBeInTheDocument();
    expect(proceed()).toBeEnabled();
  });

  it('clears a failed manual preview and recovers with a read-only retry', async () => {
    render(<UpdatesPage />);
    await check();
    loadPlan = async () => { throw new Error('Synthetic preview failure'); };
    fireEvent.click(screen.getByRole('button', { name: 'Refresh preview' }));
    await screen.findByText('Synthetic preview failure');
    expect(screen.queryByText('synthetic-v-fixture')).not.toBeInTheDocument();
    expect(proceed()).toBeDisabled();
    loadPlan = async () => plan('v-fixture');
    fireEvent.click(screen.getByRole('button', { name: 'Refresh preview' }));
    await screen.findByText('synthetic-v-fixture');
    expect(proceed()).toBeEnabled();
    expect(axios.post).not.toHaveBeenCalled();
  });

  it.each(['ui', 'cli', 'cli path', 'preview refresh', 'status refresh'] as const)('invalidates confirmation after changing %s', async setting => {
    render(<UpdatesPage />);
    await check();
    const confirmation = deferred<boolean>();
    mocks.confirm.mockReturnValue(confirmation.promise);
    fireEvent.click(proceed());
    if (setting === 'ui') fireEvent.click(screen.getByRole('checkbox', { name: /Update UI too/ }));
    else if (setting === 'cli') fireEvent.click(screen.getByRole('checkbox', { name: /Update agent CLI too/ }));
    else if (setting === 'cli path') fireEvent.change(screen.getByLabelText('Agent CLI install path'), { target: { value: '/synthetic/agent' } });
    else if (setting === 'preview refresh') fireEvent.click(screen.getByRole('button', { name: 'Refresh preview' }));
    else fireEvent.click(screen.getByRole('button', { name: 'Check updates' }));
    await flush(() => confirmation.resolve(true));
    expect(axios.post).not.toHaveBeenCalled();
  });

  it('invalidates a confirmation even after returning to the original target', async () => {
    render(<UpdatesPage />);
    await check();
    const confirmation = deferred<boolean>();
    mocks.confirm.mockReturnValue(confirmation.promise);
    fireEvent.click(proceed());
    main(); await screen.findByText('synthetic-main');
    stable(); await screen.findByText('synthetic-v-fixture');
    await flush(() => confirmation.resolve(true));
    expect(axios.post).not.toHaveBeenCalled();
  });

  it('uses the same captured options for confirmation and the accepted write', async () => {
    render(<UpdatesPage />);
    await check();
    main(); await screen.findByText('synthetic-main');
    fireEvent.click(screen.getByRole('checkbox', { name: /Update UI too/ }));
    await waitFor(() => expect(proceed()).toBeEnabled());
    fireEvent.change(screen.getByLabelText('Agent CLI install path'), { target: { value: ' /synthetic/agent ' } });
    fireEvent.click(proceed());
    await waitFor(() => expect(axios.post).toHaveBeenCalledWith('/api/system/updates/run', {
      include_ui: false, ref: 'main', checkout: true, local_changes: 'abort', update_cli_host: true,
      cli_install_path: '/synthetic/agent', force_active_calls: false,
    }));
    expect(mocks.confirm.mock.calls[0][0].description).toContain('Update UI: no');
    expect(localStorage.getItem('aava_update_job_id')).toBe('new-job');
  });

  it.each([2, null])('preserves dirty-tree and active-call guards with %s active calls', async activeCalls => {
    const guarded = plan('v-fixture').data as { plan: Record<string, unknown> };
    Object.assign(guarded.plan, { dirty: true, active_calls: activeCalls, active_calls_reachable: activeCalls !== null });
    loadPlan = async () => ({ data: guarded });
    render(<UpdatesPage />);
    await check();
    expect(proceed()).toBeDisabled();
    fireEvent.click(screen.getByRole('radio', { name: /Retain local changes/ }));
    await screen.findByText('synthetic-v-fixture');
    expect(proceed()).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: /Allow update to restart/ }));
    expect(proceed()).toBeEnabled();
    fireEvent.click(proceed());
    await waitFor(() => expect(axios.post).toHaveBeenCalledWith('/api/system/updates/run', expect.objectContaining({ local_changes: 'retain', force_active_calls: true })));
  });
});

describe('updater action and job lifecycle', () => {
  it('locks repeated update clicks while confirmation is pending', async () => {
    render(<UpdatesPage />);
    await check();
    const confirmation = deferred<boolean>();
    mocks.confirm.mockReturnValue(confirmation.promise);
    fireEvent.click(proceed());
    fireEvent.click(proceed());
    expect(mocks.confirm).toHaveBeenCalledTimes(1);
    await flush(() => confirmation.resolve(false));
  });

  it('does not submit an update accepted after unmount', async () => {
    const view = render(<UpdatesPage />);
    await check();
    const confirmation = deferred<boolean>();
    mocks.confirm.mockReturnValue(confirmation.promise);
    fireEvent.click(proceed());
    view.unmount();
    await flush(() => confirmation.resolve(true));
    expect(axios.post).not.toHaveBeenCalled();
  });

  it('rejects a full log from the previous job after a rollback starts', async () => {
    localStorage.setItem('aava_update_job_id', 'old-job');
    const oldLog = deferred<Reply>();
    loadFullLog = async () => oldLog.promise;
    loadJob = async id => ({ data: { job: id === 'old-job' ? failedJob : { job_id: id, status: 'running' }, log_tail: `synthetic log ${id}` } });
    render(<UpdatesPage />);
    await screen.findByText('synthetic log old-job');
    await check();
    fireEvent.click(screen.getByRole('button', { name: 'Load full log' }));
    fireEvent.click(screen.getByRole('button', { name: 'Rollback' }));
    await screen.findByText('synthetic log new-job');
    await flush(() => oldLog.resolve({ data: { log: 'obsolete full log' } }));
    expect(screen.queryByText('obsolete full log')).not.toBeInTheDocument();
    expect(screen.getByText('synthetic log new-job')).toBeInTheDocument();
  });

  it('blocks a new action until a restored job state is known', async () => {
    localStorage.setItem('aava_update_job_id', 'old-job');
    const pending = deferred<Reply>();
    loadJob = async () => pending.promise;
    render(<UpdatesPage />);
    await check();
    expect(screen.getByRole('button', { name: 'Update running…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Rollback' })).toBeDisabled();
    await flush(() => pending.resolve({ data: { job: failedJob, log_tail: 'restored terminal job' } }));
    await screen.findByText('restored terminal job');
    expect(proceed()).toBeEnabled();
  });

  it('does not overlap slow job reads, and stops polling at a terminal result', async () => {
    localStorage.setItem('aava_update_job_id', 'old-job');
    const pending = deferred<Reply>();
    loadJob = async () => pending.promise;
    render(<UpdatesPage />);
    await waitFor(() => expect(jobReads('old-job')).toHaveLength(1));
    await act(async () => { void poll?.(); void poll?.(); });
    expect(jobReads('old-job')).toHaveLength(1);
    await flush(() => pending.resolve({ data: { job: { job_id: 'old-job', status: 'running' }, log_tail: 'working' } }));
    await screen.findByText('working');
    const timers = vi.mocked(window.setTimeout).mock.calls.filter(([, delay]) => delay === 2000).length;
    loadJob = async () => ({ data: { job: { ...failedJob, status: 'success' }, log_tail: 'finished' } });
    await tick();
    await screen.findByText('finished');
    expect(vi.mocked(window.setTimeout).mock.calls.filter(([, delay]) => delay === 2000)).toHaveLength(timers);
  });

  it('keeps unknown job state locked during failure and recovers without resubmission', async () => {
    localStorage.setItem('aava_update_job_id', 'old-job');
    loadJob = async () => { throw readFailure(503); };
    render(<UpdatesPage />);
    await check();
    await screen.findByText(/Waiting for Admin UI\/update job/);
    expect(screen.getByRole('button', { name: 'Update running…' })).toBeDisabled();
    loadJob = async () => ({ data: { job: failedJob, log_tail: 'reconnected' } });
    await tick();
    await screen.findByText('reconnected');
    expect(proceed()).toBeEnabled();
    expect(axios.post).not.toHaveBeenCalled();
  });

  it('keeps transient 404s locked, then forgets a repeatedly missing job', async () => {
    localStorage.setItem('aava_update_job_id', 'old-job');
    loadJob = async () => { throw readFailure(404); };
    render(<UpdatesPage />);
    await check();
    expect(localStorage.getItem('aava_update_job_id')).toBe('old-job');
    expect(screen.getByRole('button', { name: 'Update running…' })).toBeDisabled();
    for (let i = 0; i < 9; i++) await tick();
    await screen.findByText('Update job not found (may be stale or pruned).');
    expect(localStorage.getItem('aava_update_job_id')).toBeNull();
    expect(proceed()).toBeEnabled();
  });

  it('aborts reads and stops scheduling polls on unmount', async () => {
    localStorage.setItem('aava_update_job_id', 'old-job');
    const pending = deferred<Reply>();
    loadJob = async () => pending.promise;
    const view = render(<UpdatesPage />);
    await waitFor(() => expect(jobReads('old-job')).toHaveLength(1));
    const signal = jobReads('old-job')[0][1]?.signal;
    view.unmount();
    expect(signal?.aborted).toBe(true);
    const timers = vi.mocked(window.setTimeout).mock.calls.length;
    await flush(() => pending.resolve({ data: { job: failedJob } }));
    expect(vi.mocked(window.setTimeout).mock.calls).toHaveLength(timers);
  });

  it('retains a server-accepted job ID when the write finishes after unmount', async () => {
    const receipt = deferred<Reply>();
    vi.mocked(axios.post).mockReturnValue(receipt.promise);
    const view = render(<UpdatesPage />);
    await check();
    fireEvent.click(proceed());
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    view.unmount();
    await flush(() => receipt.resolve({ data: { job_id: 'accepted-after-unmount' } }));
    expect(localStorage.getItem('aava_update_job_id')).toBe('accepted-after-unmount');
  });

  it('follows an accepted job even if browser recovery storage is full', async () => {
    render(<UpdatesPage />);
    await check();
    const setItem = localStorage.setItem;
    vi.spyOn(localStorage, 'setItem').mockImplementation(function (this: Storage, key, value) {
      if (key === 'aava_update_job_id') throw new DOMException('Synthetic storage quota', 'QuotaExceededError');
      setItem.call(this, key, value);
    });
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    loadJob = async id => ({ data: { job: { job_id: id, status: 'running' }, log_tail: 'accepted with storage unavailable' } });
    fireEvent.click(proceed());
    await screen.findByText('accepted with storage unavailable');
    expect(localStorage.getItem('aava_update_job_id')).toBeNull();
    expect(console.warn).toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Update running…' })).toBeDisabled();
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('Synthetic storage quota')).not.toBeInTheDocument();
  });

  it('shares the action lock between update and rollback confirmations', async () => {
    render(<UpdatesPage />);
    await check();
    const confirmation = deferred<boolean>();
    mocks.confirm.mockReturnValue(confirmation.promise);
    fireEvent.click(proceed());
    expect(screen.getByRole('button', { name: 'Rollback' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Rollback' }));
    expect(mocks.confirm).toHaveBeenCalledTimes(1);
    await flush(() => confirmation.resolve(false));
    expect(proceed()).toBeEnabled();
  });

  it('keeps rollback active-call protection when the override is declined', async () => {
    render(<UpdatesPage />);
    await check();
    mocks.confirm.mockResolvedValueOnce(true).mockResolvedValueOnce(false);
    fireEvent.click(screen.getByRole('button', { name: 'Rollback' }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledWith('/api/system/updates/rollback', { from_job_id: 'old-job', force_active_calls: false }));
  });

  it('releases the action lock on submission failure and retries only on another explicit acceptance', async () => {
    render(<UpdatesPage />);
    await check();
    vi.mocked(axios.post).mockRejectedValueOnce(readFailure(503));
    fireEvent.click(proceed());
    await screen.findByText(/Synthetic HTTP 503/);
    expect(proceed()).toBeEnabled();
    expect(axios.post).toHaveBeenCalledTimes(1);
    fireEvent.click(proceed());
    await waitFor(() => expect(localStorage.getItem('aava_update_job_id')).toBe('new-job'));
    expect(axios.post).toHaveBeenCalledTimes(2);
    expect(mocks.confirm).toHaveBeenCalledTimes(2);
  });

  it('cancels rollback before the active-call dialog when its first confirmation is declined', async () => {
    render(<UpdatesPage />);
    await check();
    mocks.confirm.mockResolvedValueOnce(false);
    fireEvent.click(screen.getByRole('button', { name: 'Rollback' }));
    await waitFor(() => expect(proceed()).toBeEnabled());
    expect(mocks.confirm).toHaveBeenCalledTimes(1);
    expect(axios.post).not.toHaveBeenCalled();
  });

  it.each(['target', 'ui', 'cli path'] as const)('reports a submitted update failure after changing %s', async change => {
    const submission = deferred<Reply>();
    vi.mocked(axios.post).mockReturnValue(submission.promise);
    render(<UpdatesPage />);
    await check();
    fireEvent.click(proceed());
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    if (change === 'target') main();
    else if (change === 'ui') fireEvent.click(screen.getByRole('checkbox', { name: /Update UI too/ }));
    else fireEvent.change(screen.getByLabelText('Agent CLI install path'), { target: { value: '/synthetic/changed-agent' } });
    await flush(() => submission.reject(readFailure(503)));
    await screen.findByText('Update to v-fixture failed: Synthetic HTTP 503');
    expect(proceed()).toBeEnabled();
    expect(axios.post).toHaveBeenCalledTimes(1);
  });

  it.each(['unmount', 'removed backup'] as const)('invalidates a rollback confirmation after %s', async change => {
    const view = render(<UpdatesPage />);
    await check();
    const confirmation = deferred<boolean>();
    mocks.confirm.mockResolvedValueOnce(true).mockReturnValueOnce(confirmation.promise);
    fireEvent.click(screen.getByRole('button', { name: 'Rollback' }));
    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledTimes(2));
    if (change === 'unmount') view.unmount();
    else {
      loadHistory = async () => ({ data: { jobs: [] } });
      fireEvent.click(screen.getByRole('button', { name: 'Refresh history' }));
      await screen.findByText('No recent runs yet.');
    }
    await flush(() => confirmation.resolve(true));
    expect(axios.post).not.toHaveBeenCalled();
  });
});

describe('updater metadata ownership', () => {
  it('preserves the operator branch selection when a slow branch refresh finishes', async () => {
    render(<UpdatesPage />);
    await check();
    fireEvent.click(screen.getByRole('radio', { name: /Advanced/ }));
    const branch = await screen.findByRole('combobox', { name: 'Target branch' });
    await waitFor(() => expect(branch).toBeEnabled());
    const pending = deferred<Reply>();
    loadBranches = async () => pending.promise;
    fireEvent.click(screen.getByRole('button', { name: 'Refresh branches' }));
    fireEvent.change(branch, { target: { value: 'fixture-branch' } });
    await screen.findByText('synthetic-fixture-branch');
    await flush(() => pending.resolve({ data: { branches: ['main', 'fixture-branch'] } }));
    expect(branch).toHaveValue('fixture-branch');
    expect(screen.getByText('synthetic-fixture-branch')).toBeInTheDocument();
  });

  it('aborts an older branch read and ignores its late failure', async () => {
    render(<UpdatesPage />);
    await check();
    fireEvent.click(screen.getByRole('radio', { name: /Advanced/ }));
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Target branch' })).toBeEnabled());
    const old = deferred<Reply>();
    loadBranches = async () => old.promise;
    fireEvent.click(screen.getByRole('button', { name: 'Refresh branches' }));
    const signal = vi.mocked(axios.get).mock.calls.filter(([url]) => url.endsWith('/branches')).slice(-1)[0][1]?.signal;
    loadBranches = async () => ({ data: { branches: ['main', 'new-fixture-branch'] } });
    fireEvent.click(screen.getByRole('button', { name: 'Refresh branches' }));
    await screen.findByRole('option', { name: 'new-fixture-branch' });
    expect(signal?.aborted).toBe(true);
    await flush(() => old.reject(new Error('obsolete branch failure')));
    expect(screen.queryByText('obsolete branch failure')).not.toBeInTheDocument();
  });

  it('ignores an obsolete history read after a new check refreshed history', async () => {
    localStorage.setItem('aava_update_job_id', 'old-job');
    const oldHistory = deferred<Reply>();
    loadHistory = async () => oldHistory.promise;
    render(<UpdatesPage />);
    await screen.findByText('synthetic log old-job');
    loadHistory = async () => ({ data: { jobs: [{ ...failedJob, ref: 'new-history-ref' }] } });
    await check();
    await screen.findByText('new-history-ref');
    await flush(() => oldHistory.resolve({ data: { jobs: [{ ...failedJob, ref: 'obsolete-history-ref' }] } }));
    expect(screen.queryByText('obsolete-history-ref')).not.toBeInTheDocument();
    expect(screen.getByText('new-history-ref')).toBeInTheDocument();
  });

  it('aborts an unfinished status check without persisting its late result after unmount', async () => {
    const pending = deferred<Reply>();
    loadStatus = async () => pending.promise;
    const view = render(<UpdatesPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Check updates' }));
    const signal = vi.mocked(axios.get).mock.calls.find(([url]) => url.endsWith('/status'))?.[1]?.signal;
    view.unmount();
    expect(signal?.aborted).toBe(true);
    await flush(() => pending.resolve({ data: { local: {}, remote: { latest_tag: 'obsolete' } } }));
    expect(localStorage.getItem('aava_update_status')).toBeNull();
    expect(axios.get).not.toHaveBeenCalledWith('/api/system/updates/plan', expect.anything());
  });
});

describe('uncertain submission recovery', () => {
  it.each(['update', 'rollback'] as const)('follows the unique active server job after a lost %s response', async action => {
    render(<UpdatesPage />);
    await check();
    loadHistory = async () => ({ data: { jobs: [{ job_id: 'reserved-job', status: 'starting' }, failedJob] } });
    loadJob = async id => ({ data: { job: { job_id: id, status: 'running' }, log_tail: 'reserved server job' } });
    vi.mocked(axios.post).mockRejectedValueOnce(readFailure(503));
    fireEvent.click(action === 'update' ? proceed() : screen.getByRole('button', { name: 'Rollback' }));
    await screen.findByText('reserved server job');
    expect(localStorage.getItem('aava_update_job_id')).toBe('reserved-job');
    expect(screen.getByText(/Following job reserved-job from Recent Runs/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Update running…' })).toBeDisabled();
    expect(axios.post).toHaveBeenCalledTimes(1);
  });

  it('follows a confirmed active job after a conflict without claiming a new submission succeeded', async () => {
    render(<UpdatesPage />);
    await check();
    loadHistory = async () => ({ data: { jobs: [{ job_id: 'other-operator-job', status: 'running' }] } });
    loadJob = async id => ({ data: { job: { job_id: id, status: 'running' }, log_tail: 'other operator progress' } });
    vi.mocked(axios.post).mockRejectedValueOnce(readFailure(409));
    fireEvent.click(proceed());
    await screen.findByText('other operator progress');
    expect(screen.getByText(/Following job other-operator-job from Recent Runs. No action was repeated./)).toBeInTheDocument();
    expect(axios.post).toHaveBeenCalledTimes(1);
  });

  it.each(['ambiguous', 'stale', 'unknown', 'unavailable'] as const)('retains the submission error when recovery history is %s', async history => {
    render(<UpdatesPage />);
    await check();
    loadHistory = async () => {
      if (history === 'unavailable') throw readFailure(503);
      return { data: { jobs: history === 'ambiguous'
        ? [{ job_id: 'one', status: 'running' }, { job_id: 'two', status: 'starting' }]
        : [{ job_id: 'one', status: history === 'unknown' ? 'unrecognized' : 'running', stale: history === 'stale' }] } };
    };
    vi.mocked(axios.post).mockRejectedValueOnce(readFailure(503));
    fireEvent.click(proceed());
    await screen.findByText('Update to v-fixture failed: Synthetic HTTP 503');
    expect(localStorage.getItem('aava_update_job_id')).toBeNull();
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(proceed()).toBeEnabled();
  });

  it('keeps the submission lock until read-only reconciliation finishes', async () => {
    render(<UpdatesPage />);
    await check();
    const history = deferred<Reply>();
    loadHistory = async () => history.promise;
    vi.mocked(axios.post).mockRejectedValueOnce(readFailure(503));
    fireEvent.click(proceed());
    await waitFor(() => expect(axios.get).toHaveBeenCalledWith('/api/system/updates/history', expect.objectContaining({ params: { limit: 25 } })));
    expect(proceed()).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Rollback' })).toBeDisabled();
    fireEvent.click(proceed());
    expect(axios.post).toHaveBeenCalledTimes(1);
    await flush(() => history.resolve({ data: { jobs: [] } }));
    await screen.findByText(/Update to v-fixture failed/);
  });

  it('aborts reconciliation and ignores a late history reply after unmount', async () => {
    const view = render(<UpdatesPage />);
    await check();
    const history = deferred<Reply>();
    loadHistory = async () => history.promise;
    vi.mocked(axios.post).mockRejectedValueOnce(readFailure(503));
    fireEvent.click(proceed());
    await waitFor(() => expect(axios.get).toHaveBeenCalledWith('/api/system/updates/history', expect.objectContaining({ params: { limit: 25 } })));
    const signal = vi.mocked(axios.get).mock.calls.filter(([url]) => url.endsWith('/history')).slice(-1)[0][1]?.signal;
    view.unmount();
    expect(signal?.aborted).toBe(true);
    await flush(() => history.resolve({ data: { jobs: [{ job_id: 'late-reservation', status: 'starting' }] } }));
    expect(localStorage.getItem('aava_update_job_id')).toBeNull();
  });

  it.each(['update', 'rollback'] as const)('can follow a late %s receipt from Recent Runs on a page reopened before the receipt', async action => {
    const receipt = deferred<Reply>();
    vi.mocked(axios.post).mockReturnValue(receipt.promise);
    const firstPage = render(<UpdatesPage />);
    await check();
    fireEvent.click(action === 'update' ? proceed() : screen.getByRole('button', { name: 'Rollback' }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    firstPage.unmount();
    render(<UpdatesPage />);
    await flush(() => receipt.resolve({ data: { job_id: 'late-accepted-job' } }));
    expect(jobReads('late-accepted-job')).toHaveLength(0);
    loadHistory = async () => ({ data: { jobs: [{ job_id: 'late-accepted-job', status: 'running' }] } });
    loadJob = async id => ({ data: { job: { job_id: id, status: 'running' }, log_tail: 'manually recovered job' } });
    await check();
    fireEvent.click(await screen.findByRole('button', { name: 'Follow job' }));
    await screen.findByText('manually recovered job');
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Update running…' })).toBeDisabled();
  });

  it('provides a manual read-only recovery path when browser job storage is full', async () => {
    render(<UpdatesPage />);
    await check();
    loadHistory = async () => ({ data: { jobs: [{ job_id: 'stored-on-server', status: 'running' }] } });
    fireEvent.click(screen.getByRole('button', { name: 'Refresh history' }));
    const follow = await screen.findByRole('button', { name: 'Follow job' });
    vi.spyOn(localStorage, 'setItem').mockImplementation(() => { throw new DOMException('Synthetic full storage'); });
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    loadJob = async id => ({ data: { job: { job_id: id, status: 'running' }, log_tail: 'following server-only job' } });
    fireEvent.click(follow);
    await screen.findByText('following server-only job');
    expect(screen.getByText(/Following job stored-on-server, but browser recovery storage is unavailable/)).toBeInTheDocument();
    expect(axios.post).not.toHaveBeenCalled();
  });
});
