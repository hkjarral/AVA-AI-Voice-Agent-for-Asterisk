import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ArrowUpCircle, RefreshCw, Play, AlertTriangle, CheckCircle2, XCircle } from 'lucide-react';
import axios from 'axios';
import { apiErrorMessage } from '../../utils/apiErrors';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import { ConfigSection } from '../../components/ui/ConfigSection';
import { ConfigCard } from '../../components/ui/ConfigCard';
import { copyTextToClipboard, extractUpdateRecoveryCommands } from '../../utils/clipboard';

type UpdateAvailable = boolean | null;
interface PlanQuery {
  ref: string;
  includeUI: boolean;
  checkout: boolean;
  localChanges: string;
  checkVersion: number;
}
type ReadKey = 'status' | 'branches' | 'history' | 'image' | 'plan' | 'job' | 'log';

interface UpdatesStatus {
  local: { branch?: string; head_sha: string; describe: string; deployed_tag?: string | null };
  remote?: { latest_tag: string; latest_tag_sha: string } | null;
  update_available?: UpdateAvailable;
  changelog_latest?: string | null;
  error?: string | null;
}

interface UpdatePlan {
  repo_root: string;
  remote: string;
  ref: string;
  current_branch?: string;
  target_branch?: string;
  checkout?: boolean;
  would_checkout?: boolean;
  old_sha: string;
  new_sha: string;
  relation?: 'equal' | 'behind' | 'ahead' | 'diverged' | string;
  code_changed?: boolean;
  update_available: boolean;
  dirty: boolean;
  no_stash: boolean;
  stash_untracked: boolean;
  local_changes?: 'ask' | 'retain' | 'overwrite' | 'abort' | string;
  would_stash: boolean;
  would_overwrite?: boolean;
  would_abort: boolean;
  rebuild_mode: string;
  compose_changed: boolean;
  services_rebuild: string[];
  services_restart: string[];
  skipped_services?: Record<string, string>;
  changed_file_count: number;
  changed_files?: string[];
  changed_files_truncated?: boolean;
  local_file_count?: number;
  local_files?: string[];
  local_files_truncated?: boolean;
  warnings?: string[];
  active_calls?: number | null;
  active_calls_reachable?: boolean;
}

interface BranchesResponse {
  branches: string[];
  error?: string | null;
}

interface UpdateJobResponse {
  job: any;
  log_tail?: string | null;
}

interface UpdateHistoryResponse {
  jobs: any[];
}

interface UpdaterImageStatus {
  status?: string;
  phase?: string;
  image?: string;
  message?: string;
  detail_tail?: string[];
  started_at?: string;
  updated_at?: string;
  finished_at?: string;
}

const TERMINAL_UPDATE_STATUSES = new Set(['success', 'failed', 'validation_failed', 'stale']);
const isTerminalUpdateStatus = (st?: string) => TERMINAL_UPDATE_STATUSES.has((st || '').toLowerCase());
const ROLLBACK_ELIGIBLE_STATUSES = new Set(['failed', 'validation_failed', 'stale']);
const canRollbackJob = (job: any, st?: string) =>
  ROLLBACK_ELIGIBLE_STATUSES.has((st || String(job?.status || '')).toLowerCase()) &&
  Boolean(job?.pre_update_branch) &&
  Boolean(job?.backup_dir_rel);

const UpdatesPage = () => {
  const { confirm } = useConfirmDialog();
  const [copiedJobId, setCopiedJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<UpdatesStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);

  const [branches, setBranches] = useState<string[]>([]);
  const [branchesError, setBranchesError] = useState<string | null>(null);
  const [selectedBranch, setSelectedBranch] = useState('main');
  const [targetMode, setTargetMode] = useState<'stable' | 'main' | 'advanced'>('stable');
  const [initialized, setInitialized] = useState(false);

  // Default ON: the admin_ui frontend is image-baked, so skipping its rebuild leaves a
  // stale UI (and possible FE/BE contract mismatch) after an update. Matches the CLI's
  // --include-ui default (HIGH-6).
  const [includeUI, setIncludeUI] = useState(true);
  const [updateCliHost, setUpdateCliHost] = useState(true);
  const [cliInstallPath, setCliInstallPath] = useState('');
  const [forceActiveCalls, setForceActiveCalls] = useState(false);
  const [planResult, setPlanResult] = useState<{ query: PlanQuery; value: UpdatePlan } | null>(null);
  const [planFailure, setPlanFailure] = useState<{ query: PlanQuery; message: string } | null>(null);
  const [checkVersion, setCheckVersion] = useState(0);
  const [planRecoveryCopied, setPlanRecoveryCopied] = useState(false);

  const [jobId, setJobId] = useState<string | null>(() => localStorage.getItem('aava_update_job_id'));
  const [job, setJob] = useState<any>(null);
  const [logTail, setLogTail] = useState<string>('');
  const [fullLogLoading, setFullLogLoading] = useState(false);
  const [running, setRunning] = useState(Boolean(jobId));
  const [actionPending, setActionPending] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [localChangesPolicy, setLocalChangesPolicy] = useState<'retain' | 'overwrite' | 'abort' | ''>('');
  const [updaterImageStatus, setUpdaterImageStatus] = useState<UpdaterImageStatus | null>(null);

  const [history, setHistory] = useState<any[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

  // Each read channel has one owner, even when an aborted promise still settles.
  const mounted = useRef(false);
  const reads = useRef(new Map<ReadKey, AbortController>());
  const actionLock = useRef(false);
  const cancelRead = useCallback((key: ReadKey) => {
    reads.current.get(key)?.abort();
    reads.current.delete(key);
  }, []);
  const startRead = useCallback((key: ReadKey, skipIfPending = false) => {
    if (!mounted.current || (skipIfPending && reads.current.has(key))) return null;
    reads.current.get(key)?.abort();
    const controller = new AbortController();
    reads.current.set(key, controller);
    return {
      signal: controller.signal,
      isCurrent: () => mounted.current && !controller.signal.aborted && reads.current.get(key) === controller,
      finish: () => { if (reads.current.get(key) === controller) reads.current.delete(key); },
    };
  }, []);
  useLayoutEffect(() => {
    mounted.current = true;
    const pendingReads = reads.current;
    return () => {
      mounted.current = false;
      pendingReads.forEach(controller => controller.abort());
      pendingReads.clear();
    };
  }, []);

  const targetRef = useMemo(() => {
    if (targetMode === 'stable') return status?.remote?.latest_tag || '';
    if (targetMode === 'main') return 'main';
    return selectedBranch;
  }, [targetMode, status, selectedBranch]);

  const targetCheckout = useMemo(() => {
    // For release tags, avoid branch switching semantics. For main/branches, allow checkout.
    return targetMode !== 'stable';
  }, [targetMode]);

  // Object identity also distinguishes A → B → A and repeated status checks.
  const planQuery = useMemo<PlanQuery | null>(() => initialized && targetRef ? ({
    ref: targetRef, includeUI, checkout: targetCheckout, localChanges: localChangesPolicy || 'ask', checkVersion,
  }) : null, [initialized, targetRef, includeUI, targetCheckout, localChangesPolicy, checkVersion]);
  const activePlanQuery = useRef<PlanQuery | null>(null);
  const plan = planResult?.query === planQuery ? planResult.value : null;
  const planError = planFailure?.query === planQuery ? planFailure.message : null;
  const planLoading = Boolean(planQuery && !plan && !planError);
  const jobView = useMemo(() => ({ id: jobId }), [jobId]);
  const activeJobView = useRef<typeof jobView | null>(null);
  const historyRef = useRef(history);
  const targetModeRef = useRef(targetMode);
  const actionView = useMemo(() => ({ preview: planResult, planQuery, updateCliHost, cliInstallPath, forceActiveCalls }),
    [planResult, planQuery, updateCliHost, cliInstallPath, forceActiveCalls]);
  const activeActionView = useRef<typeof actionView | null>(null);
  useLayoutEffect(() => {
    activePlanQuery.current = planQuery;
    setPlanRecoveryCopied(false);
    return () => { activePlanQuery.current = null; cancelRead('plan'); };
  }, [planQuery, cancelRead]);
  useLayoutEffect(() => {
    activeActionView.current = actionView;
    return () => { activeActionView.current = null; };
  }, [actionView]);
  useLayoutEffect(() => { historyRef.current = history; targetModeRef.current = targetMode; }, [history, targetMode]);
  useLayoutEffect(() => {
    activeJobView.current = jobView;
    setJob(null);
    setLogTail('');
    setFullLogLoading(false);
    setRunning(Boolean(jobView.id));
    return () => { activeJobView.current = null; cancelRead('job'); cancelRead('log'); };
  }, [jobView, cancelRead]);

  const planHasActiveCalls = typeof plan?.active_calls === 'number' && plan.active_calls > 0;
  const activeCallCheckUnavailable = plan?.active_calls_reachable === false;
  const activeCallOverrideRequired = planHasActiveCalls || activeCallCheckUnavailable;
  const localChangeChoiceRequired = Boolean(plan?.dirty && (!localChangesPolicy || localChangesPolicy === 'abort'));
  const updaterImageVisible =
    !!updaterImageStatus &&
    updaterImageStatus.status !== 'idle' &&
    (statusLoading || planLoading || running || updaterImageStatus.status === 'error');
  const planRecoveryCommands = useMemo(() => extractUpdateRecoveryCommands(planError || ''), [planError]);

  const loadBranches = async (opts?: { force?: boolean; localBranch?: string }) => {
    const request = startRead('branches');
    if (!request) return;
    setBranchesError(null);
    const localBranch = opts?.localBranch ?? status?.local?.branch;
    try {
      const response = await axios.get<BranchesResponse>('/api/system/updates/branches', {
        params: { build_updater: true, force: opts?.force ? true : false }, signal: request.signal,
      });
      if (!request.isCurrent()) return;
      const list = response.data.branches || [];
      setBranches(list);
      if (response.data.error) setBranchesError(response.data.error);
      setSelectedBranch(current => list.includes(current) ? current :
        localBranch && list.includes(localBranch) ? localBranch :
          list.includes('main') ? 'main' : list[0] || 'main');
    } catch (error) {
      if (request.isCurrent()) {
        setBranchesError(apiErrorMessage(error, 'Failed to load branches'));
        setBranches([]);
      }
    } finally { request.finish(); }
  };

  const fetchHistory = useCallback(async () => {
    const request = startRead('history');
    if (!request) return;
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const response = await axios.get<UpdateHistoryResponse>('/api/system/updates/history', {
        params: { limit: 10 }, signal: request.signal, timeout: 15000,
      });
      if (request.isCurrent()) setHistory(response.data.jobs || []);
    } catch (error) {
      if (request.isCurrent()) {
        setHistoryError(apiErrorMessage(error, 'Failed to load update history'));
        setHistory([]);
      }
    } finally {
      if (request.isCurrent()) setHistoryLoading(false);
      request.finish();
    }
  }, [startRead]);

  const checkUpdates = async (opts?: { force?: boolean }) => {
    const request = startRead('status');
    if (!request) return;
    cancelRead('branches');
    cancelRead('plan');
    setCheckVersion(value => value + 1);
    setInitialized(false);
    setPlanResult(null);
    setPlanFailure(null);
    setRunError(null);
    setStatusError(null);
    setBranchesError(null);
    setLocalChangesPolicy('');
    setStatusLoading(true);
    try {
      const response = await axios.get<UpdatesStatus>('/api/system/updates/status', {
        params: { check_remote: true, build_updater: true, force: opts?.force ? true : false }, signal: request.signal,
      });
      if (!request.isCurrent()) return;
      setStatus(response.data);
      const localBranch = response.data.local?.branch || 'main';
      setSelectedBranch(localBranch === 'detached' ? 'main' : localBranch);
      setInitialized(true);
      localStorage.setItem('aava_update_status', JSON.stringify({
        checked_at: new Date().toISOString(), update_available: response.data.update_available ?? null,
        local_version: response.data.local?.deployed_tag || response.data.local?.describe || null,
        remote_version: response.data.remote?.latest_tag || null,
      }));
      void fetchHistory();
      if (targetModeRef.current === 'advanced') void loadBranches({ localBranch });
    } catch (error) {
      if (request.isCurrent()) {
        setStatusError(apiErrorMessage(error, 'Failed to check updates'));
        setInitialized(false);
      }
    } finally {
      if (request.isCurrent()) setStatusLoading(false);
      request.finish();
    }
  };

  const fetchUpdaterImageStatus = useCallback(async () => {
    const request = startRead('image', true);
    if (!request) return;
    try {
      const response = await axios.get<{ status: UpdaterImageStatus }>('/api/system/updates/updater-image/status', {
        signal: request.signal, timeout: 15000,
      });
      if (request.isCurrent()) setUpdaterImageStatus(response.data.status || null);
    } catch {
      // Best-effort progress signal; the status/plan/job read owns user-facing errors.
    } finally { request.finish(); }
  }, [startRead]);

  const copyRecoveryCommands = async (job: any) => {
    const preBranch = job?.pre_update_branch;
    const backupRel = job?.backup_dir_rel;
    if (!preBranch || !backupRel) return;

    const shQuote = (value: unknown) => `'${String(value ?? '').replace(/'/g, "'\\''")}'`;
    const composeTargets = job?.include_ui ? 'ai_engine local_ai_server admin_ui' : 'ai_engine local_ai_server';
    const repoRoot = job?.repo_root || job?.plan?.repo_root || '/root/Asterisk-AI-Voice-Agent';
    const text = [
      '# Roll back to pre-update code + restore operator config',
      '# NOTE: this restores code + config only. It does NOT restore agents.db or call_history.db.',
      '# Restore DB snapshots manually only when you are sure newer call/agent data can be discarded.',
      `REPO=${shQuote(repoRoot)}`,
      'cd "$REPO"',
      'git config --global --add safe.directory "$REPO"',
      '',
      `git checkout ${shQuote(preBranch)}`,
      '',
      `cp -- ${shQuote(`${backupRel}/.env`)} .env`,
      `cp -- ${shQuote(`${backupRel}/config/ai-agent.yaml`)} config/ai-agent.yaml`,
      `cp -- ${shQuote(`${backupRel}/config/ai-agent.local.yaml`)} config/ai-agent.local.yaml 2>/dev/null || true`,
      `cp -- ${shQuote(`${backupRel}/config/users.json`)} config/users.json`,
      `rm -rf config/contexts && cp -r -- ${shQuote(`${backupRel}/config/contexts`)} config/contexts`,
      '',
      `docker compose up -d --build ${composeTargets}`,
    ].join('\n');

    try {
      await navigator.clipboard.writeText(text);
      setCopiedJobId(job.job_id);
      setTimeout(() => setCopiedJobId(null), 2000);
    } catch (e) {
      window.prompt('Copy recovery commands:', text);
    }
  };

  const copyPlanErrorRecovery = async () => {
    if (!planRecoveryCommands) return;
    const copied = await copyTextToClipboard(planRecoveryCommands);
    if (!copied) {
      window.prompt('Copy recovery commands:', planRecoveryCommands);
      return;
    }
    setPlanRecoveryCopied(true);
    setTimeout(() => setPlanRecoveryCopied(false), 2000);
  };

  // Once accepted by the server, keep the job ID even if the page unmounted
  // during the write, so returning to Updates resumes its status instead of resubmitting.
  const followJob = (id: string) => {
    try {
      localStorage.setItem('aava_update_job_id', id);
    } catch {
      // Browser storage failure must not turn an accepted write into a retry.
      console.warn('Update job accepted, but browser recovery storage is unavailable.');
    }
    if (mounted.current) {
      setJobId(id);
      setRunning(true);
    }
  };

  const rollbackFromJob = async (sourceJob: any) => {
    if (actionLock.current || running || !canRollbackJob(sourceJob)) return;
    actionLock.current = true;
    setActionPending(true);
    const owner = jobView;
    const { job_id: fromJobId, pre_update_branch: preBranch, backup_dir_rel: backupRel } = sourceJob;
    const stillOwned = () => mounted.current && activeJobView.current === owner &&
      historyRef.current.some(item => item.job_id === fromJobId && canRollbackJob(item) &&
        item.pre_update_branch === preBranch && item.backup_dir_rel === backupRel);
    try {
      const accepted = await confirm({
        title: 'Start Rollback?',
        description: `Source job: ${fromJobId}\nPre-update branch: ${preBranch}\nBackup: ${backupRel}\n\nThis will checkout the pre-update branch and restore operator config from the backup. Services may rebuild/restart.\n\nNote: this restores code + config only — it does NOT touch agents.db. To revert the YAML->agents.db migration too, see docs/OPERATOR_MIGRATION.md (Rollback).`,
        confirmText: 'Start Rollback', variant: 'destructive',
      });
      if (!accepted || !stillOwned()) return;
      const force = await confirm({
        title: 'Active-Call Safety',
        description: 'Rollback will refuse to rebuild or restart ai_engine if active calls are detected.\n\nChoose Keep Guard for normal safe rollback behavior. Choose Enable Override only if you intentionally want rollback to proceed even when calls are active.',
        confirmText: 'Enable Override', cancelText: 'Keep Guard', variant: 'destructive',
      });
      if (!stillOwned()) return;
      setRunError(null);
      const response = await axios.post('/api/system/updates/rollback', { from_job_id: fromJobId, force_active_calls: force });
      followJob(response.data.job_id);
    } catch (error) {
      if (mounted.current && activeJobView.current === owner) setRunError(apiErrorMessage(error, 'Failed to start rollback'));
    } finally {
      actionLock.current = false;
      if (mounted.current) setActionPending(false);
    }
  };

  const fetchPlan = useCallback(async () => {
    const query = planQuery;
    if (!query || activePlanQuery.current !== query) return;
    const request = startRead('plan');
    if (!request) return;
    setPlanResult(null);
    setPlanFailure(null);
    try {
      const response = await axios.get<{ plan: UpdatePlan }>('/api/system/updates/plan', {
        params: { ref: query.ref, include_ui: query.includeUI, checkout: query.checkout, local_changes: query.localChanges },
        signal: request.signal,
      });
      if (request.isCurrent() && activePlanQuery.current === query) setPlanResult({ query, value: response.data.plan });
    } catch (error) {
      if (request.isCurrent() && activePlanQuery.current === query) setPlanFailure({ query, message: apiErrorMessage(error, 'Failed to compute update plan') });
    } finally { request.finish(); }
  }, [planQuery, startRead]);

  const fetchFullLog = async () => {
    const owner = jobView;
    if (!owner.id || activeJobView.current !== owner) return;
    const request = startRead('log');
    if (!request) return;
    const isCurrent = () => request.isCurrent() && activeJobView.current === owner;
    setFullLogLoading(true);
    try {
      const response = await axios.get<{ log: string }>(`/api/system/updates/jobs/${owner.id}/log`, { signal: request.signal, timeout: 15000 });
      if (isCurrent()) setLogTail(response.data.log || '');
    } catch (error) {
      if (isCurrent()) setRunError(apiErrorMessage(error, 'Failed to load full log'));
    } finally {
      if (isCurrent()) setFullLogLoading(false);
      request.finish();
    }
  };

  const runUpdate = async () => {
    if (actionLock.current || running || !plan || !planQuery || planLoading || localChangeChoiceRequired ||
        (activeCallOverrideRequired && !forceActiveCalls)) return;
    actionLock.current = true;
    setActionPending(true);
    const owner = actionView;
    const ownerJob = jobView;
    const stillOwned = () => mounted.current && activeActionView.current === owner && activeJobView.current === ownerJob;
    const payload = {
      include_ui: planQuery.includeUI, ref: planQuery.ref, checkout: planQuery.checkout,
      local_changes: localChangesPolicy || 'abort', update_cli_host: updateCliHost,
      cli_install_path: cliInstallPath.trim() || null, force_active_calls: forceActiveCalls,
    };
    const rebuild = plan.services_rebuild?.join(', ') || 'none';
    const restart = plan.services_restart?.join(', ') || 'none';
    const skipped = Object.entries(plan.skipped_services || {}).map(([key, value]) => `${key}:${value}`).join(', ') || 'none';
    const localChangeText = plan.dirty ? localChangesPolicy === 'retain'
      ? 'retain local tracked changes (stash and reapply; may conflict)'
      : 'overwrite local tracked code changes and restore operator config from backup' : 'none detected';
    let submitted = false;
    try {
      setRunError(null);
      const accepted = await confirm({
        title: 'Proceed with Update?',
        description: `Target: ${planQuery.ref}\nMode: ${targetMode === 'stable' ? 'stable release' : targetMode === 'main' ? 'main hotfixes' : 'advanced branch'}\nUpdate UI: ${includeUI ? 'yes' : 'no'}\nUpdate CLI: ${updateCliHost ? 'yes' : 'no'}\nLocal changes: ${localChangeText}\nWill rebuild: ${rebuild}\nWill restart: ${restart}\nSkipped services: ${skipped}\nFiles changed: ${plan.changed_file_count ?? 'unknown'}\nActive calls: ${typeof plan.active_calls === 'number' ? plan.active_calls : 'unknown'}${forceActiveCalls ? ' (override enabled)' : ''}\n\nServices may restart during update.`,
        confirmText: 'Start Update', variant: 'default',
      });
      if (!accepted || !stillOwned()) return;
      submitted = true;
      const response = await axios.post('/api/system/updates/run', payload);
      followJob(response.data.job_id);
    } catch (error) {
      if (mounted.current && (submitted || stillOwned())) {
        setRunError(`Update to ${payload.ref} failed: ${apiErrorMessage(error, 'Failed to start update')}`);
      }
    } finally {
      actionLock.current = false;
      if (mounted.current) setActionPending(false);
    }
  };

  useEffect(() => { void fetchPlan(); }, [fetchPlan]);

  useEffect(() => {
    const owner = jobView;
    if (!owner.id) return;
    let stopped = false;
    let timer: number | undefined;
    let notFoundCount = 0;
    const tick = async () => {
      if (stopped || activeJobView.current !== owner) return;
      const request = startRead('job', true);
      if (!request) return;
      const isCurrent = () => !stopped && request.isCurrent() && activeJobView.current === owner;
      let terminal = false;
      try {
        const response = await axios.get<UpdateJobResponse>(`/api/system/updates/jobs/${owner.id}`, { signal: request.signal, timeout: 15000 });
        if (!isCurrent()) return;
        notFoundCount = 0;
        setJob(response.data.job);
        setLogTail(response.data.log_tail || '');
        terminal = isTerminalUpdateStatus(response.data.job?.status);
        setRunning(!terminal);
        setRunError(null);
        if (terminal) void fetchHistory();
      } catch (error) {
        if (!isCurrent()) return;
        if (axios.isAxiosError(error) && error.response?.status === 404 && ++notFoundCount >= 10) {
          terminal = true;
          setRunning(false);
          setJobId(null);
          localStorage.removeItem('aava_update_job_id');
          setRunError('Update job not found (may be stale or pruned).');
        } else if (!(axios.isAxiosError(error) && error.response?.status === 404)) {
          // Unknown job state stays locked, including after an Admin UI restart.
          setRunError(`Waiting for Admin UI/update job to come back online: ${apiErrorMessage(error, 'Failed to read update job')}`);
        }
      } finally {
        const retry = isCurrent() && !terminal;
        request.finish();
        if (retry) timer = window.setTimeout(tick, 2000);
      }
    };
    void tick();
    return () => { stopped = true; window.clearTimeout(timer); cancelRead('job'); };
  }, [jobView, startRead, cancelRead, fetchHistory]);

  useEffect(() => {
    if (!(statusLoading || planLoading || running)) return;
    void fetchUpdaterImageStatus();
    const interval = window.setInterval(fetchUpdaterImageStatus, 1500);
    return () => { window.clearInterval(interval); cancelRead('image'); };
  }, [statusLoading, planLoading, running, fetchUpdaterImageStatus, cancelRead]);

  useEffect(() => {
    if (!activeCallOverrideRequired && forceActiveCalls) setForceActiveCalls(false);
  }, [activeCallOverrideRequired, forceActiveCalls]);

  const renderUpdaterImageStatus = (section: 'status' | 'plan' | 'run') => {
    if (!updaterImageVisible) return null;
    if (section === 'status' && !statusLoading && updaterImageStatus?.status !== 'error') return null;
    if (section === 'plan' && (!planLoading || statusLoading)) return null;
    if (section === 'run' && (!running || statusLoading || planLoading)) return null;
    const st = updaterImageStatus?.status || 'unknown';
    const phase = updaterImageStatus?.phase || 'updater image';
    const tail = updaterImageStatus?.detail_tail || [];
    const isActive = st === 'running';
    const isError = st === 'error';
    return (
      <div
        className={`rounded-md border p-3 text-xs ${
          isError
            ? 'border-destructive/30 bg-destructive/10'
            : 'border-border bg-card/30'
        }`}
      >
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 font-medium">
            {isError ? (
              <XCircle className="w-3.5 h-3.5 text-destructive" />
            ) : isActive ? (
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <CheckCircle2 className="w-3.5 h-3.5" />
            )}
            <span className="capitalize">{phase.replace(/_/g, ' ')}</span>
          </div>
          {updaterImageStatus?.updated_at && <div className="text-muted-foreground">{updaterImageStatus.updated_at}</div>}
        </div>
        <div className="mt-1 text-muted-foreground">{updaterImageStatus?.message || 'Preparing updater image'}</div>
        {updaterImageStatus?.image && <div className="mt-1 font-mono break-all">{updaterImageStatus.image}</div>}
        {tail.length ? (
          <pre className="mt-2 max-h-[160px] overflow-auto whitespace-pre-wrap break-words rounded-md bg-background/70 p-2 font-mono">
            {tail.join('\n')}
          </pre>
        ) : null}
      </div>
    );
  };

  const previewLabel = useMemo(() => {
    if (!initialized) return 'Not checked';
    if (!plan) return planLoading ? 'Loading preview…' : 'Preview unavailable';
    if (plan.would_abort) return 'Blocked (dirty tree)';
    if (plan.relation === 'behind') return 'Update available';
    if (plan.relation === 'equal') return 'Up to date';
    if (plan.relation === 'ahead') return 'Local ahead';
    if (plan.relation === 'diverged') return 'Diverged';
    return plan.relation || 'Unknown';
  }, [initialized, plan, planLoading]);

  const previewIcon = useMemo(() => {
    if (!initialized) return <AlertTriangle className="w-4 h-4 text-muted-foreground" />;
    if (planLoading) return <RefreshCw className="w-4 h-4 animate-spin text-muted-foreground" />;
    if (!plan) return <AlertTriangle className="w-4 h-4 text-muted-foreground" />;
    if (plan.relation === 'behind') return <AlertTriangle className="w-4 h-4 text-yellow-500" />;
    if (plan.relation === 'equal' || plan.relation === 'ahead') return <CheckCircle2 className="w-4 h-4 text-primary" />;
    if (plan.relation === 'diverged') return <AlertTriangle className="w-4 h-4 text-yellow-500" />;
    return <AlertTriangle className="w-4 h-4 text-muted-foreground" />;
  }, [initialized, plan, planLoading]);

  return (
    <ConfigSection
      title="Updates"
      description="Mimics a GitHub-style update flow: check updates, pick a branch, preview file/container impact, then proceed."
    >
      <ConfigCard>
        <div className="flex items-center justify-between gap-3 mb-3">
          <div className="flex items-center gap-2">
            <ArrowUpCircle className="w-5 h-5" />
            <div className="text-base font-semibold">Check Updates</div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => checkUpdates()}
              disabled={statusLoading}
              className="inline-flex items-center gap-2 px-3 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              title="Check updates"
            >
              <RefreshCw className={`w-4 h-4 ${statusLoading ? 'animate-spin' : ''}`} />
              {statusLoading ? 'Checking…' : 'Check updates'}
            </button>
            <button
              onClick={() => checkUpdates({ force: true })}
              disabled={statusLoading}
              className="inline-flex items-center gap-2 px-3 py-2 rounded-md border border-border hover:bg-accent disabled:opacity-50"
              title="Force refresh (ignore cached status)"
            >
              <RefreshCw className="w-4 h-4" />
              Force refresh
            </button>
          </div>
        </div>

        <div className="space-y-2">
          {statusError && <div className="text-sm text-destructive">{statusError}</div>}
          {branchesError && <div className="text-sm text-muted-foreground">{branchesError}</div>}
          {status && status.error && <div className="text-sm text-muted-foreground">{status.error}</div>}
          {renderUpdaterImageStatus('status')}

          <div className="flex items-center gap-2">
            {previewIcon}
            <div className="text-sm font-medium">{previewLabel}</div>
          </div>

          {status ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
              <div>
                <div className="text-xs text-muted-foreground">Local (branch)</div>
                <div className="font-mono text-xs break-all">{status.local?.branch || 'Unknown'}</div>
                <div className="mt-1 text-xs text-muted-foreground">Deployed tag</div>
                <div className="font-mono text-xs break-all">{status.local?.deployed_tag || 'Unknown'}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Remote (latest v*)</div>
                <div className="font-mono text-xs break-all">{status.remote?.latest_tag || 'Unknown'}</div>
              </div>
            </div>
          ) : (
            <div className="text-sm text-muted-foreground">Click “Check updates” to load status and release info.</div>
          )}

          {status?.changelog_latest ? (
            <div className="border border-border rounded-lg bg-card/30 p-3">
              <div className="text-xs text-muted-foreground mb-2">Latest release notes</div>
              <pre className="text-xs font-mono whitespace-pre-wrap break-words max-h-[260px] overflow-auto">{status.changelog_latest}</pre>
            </div>
          ) : null}
        </div>
      </ConfigCard>

      <ConfigCard>
        <div className="flex items-center justify-between gap-3 mb-3">
          <div className="flex items-center gap-2">
            <RefreshCw className="w-5 h-5" />
            <div className="text-base font-semibold">Select Target + Preview</div>
          </div>
          <button
            onClick={() => fetchPlan()}
            disabled={!initialized || planLoading}
            className="p-1.5 hover:bg-accent rounded-lg transition-colors disabled:opacity-50"
            aria-label="Refresh preview"
            title="Refresh preview"
          >
            <RefreshCw className={`w-4 h-4 ${planLoading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        <div className="space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <div className="text-xs text-muted-foreground mb-2">Update target</div>
              <div className="space-y-2 text-sm">
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="aava-update-target"
                    checked={targetMode === 'stable'}
                    onChange={() => setTargetMode('stable')}
                    className="rounded border-border"
                  />
                  Stable (latest release tag)
                  <span className="ml-auto font-mono text-xs text-muted-foreground">{status?.remote?.latest_tag || 'unknown'}</span>
                </label>
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="aava-update-target"
                    checked={targetMode === 'main'}
                    onChange={() => setTargetMode('main')}
                    className="rounded border-border"
                  />
                  Main (hotfixes)
                  <span className="ml-auto font-mono text-xs text-muted-foreground">main</span>
                </label>
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="aava-update-target"
                    checked={targetMode === 'advanced'}
                    onChange={() => {
                      setTargetMode('advanced');
                      if (initialized && !branches.length) loadBranches();
                    }}
                    className="rounded border-border"
                  />
                  Advanced (pick a branch)
                  <span className="ml-auto text-xs text-muted-foreground">custom</span>
                </label>
              </div>

              {targetMode === 'advanced' && (
                <div className="mt-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-xs text-muted-foreground">Target branch</div>
                    <button
                      onClick={() => loadBranches({ force: true })}
                      disabled={!initialized}
                      className="text-xs px-2 py-1 rounded-md border border-border hover:bg-accent disabled:opacity-50"
                      title="Refresh branches"
                    >
                      Refresh branches
                    </button>
                  </div>
                  <select
                    aria-label="Target branch"
                    value={selectedBranch}
                    onChange={(e) => setSelectedBranch(e.target.value)}
                    disabled={!initialized || !branches.length}
                    className="w-full mt-1 px-3 py-2 rounded-md border border-border bg-background text-sm"
                  >
                    {(branches.length ? branches : [selectedBranch]).map((b) => (
                      <option key={b} value={b}>
                        {b}
                      </option>
                    ))}
                  </select>
                  {!branches.length && initialized && (
                    <div className="mt-1 text-xs text-muted-foreground">No branches returned.</div>
                  )}
                </div>
              )}
            </div>
            <div className="flex flex-col justify-end gap-2">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={includeUI}
                  onChange={(e) => setIncludeUI(e.target.checked)}
                  className="rounded border-border"
                />
                Update UI too (allow admin_ui rebuild/restart)
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={updateCliHost}
                  onChange={(e) => setUpdateCliHost(e.target.checked)}
                  className="rounded border-border"
                />
                Update agent CLI too (best-effort)
              </label>
            </div>
          </div>

          {updateCliHost && (
            <div>
              <div className="text-xs text-muted-foreground mb-1">Agent CLI install path (optional)</div>
              <input
                aria-label="Agent CLI install path"
                value={cliInstallPath}
                onChange={(e) => setCliInstallPath(e.target.value)}
                placeholder="auto (detect existing or install to /usr/local/bin/agent)"
                className="w-full px-3 py-2 rounded-md border border-border bg-background text-sm font-mono"
              />
              <div className="mt-1 text-xs text-muted-foreground">Leave blank for auto-detect + default install.</div>
            </div>
          )}

          {planError && (
            <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm">
              <div className="flex items-center justify-between gap-3">
                <div className="font-medium text-destructive">Update preview failed</div>
                {planRecoveryCommands && (
                  <button
                    type="button"
                    onClick={copyPlanErrorRecovery}
                    className="shrink-0 rounded-md border border-border px-2 py-1 text-xs hover:bg-accent"
                  >
                    {planRecoveryCopied ? 'Copied' : 'Copy recovery commands'}
                  </button>
                )}
              </div>
              <pre className="mt-2 max-h-[420px] overflow-auto whitespace-pre-wrap break-words rounded-md bg-background/80 p-3 font-mono text-xs text-foreground">
                {planError}
              </pre>
              <a
                href="https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/blob/main/docs/INSTALLATION.md#update-planner-recovery-including-issue-518"
                target="_blank"
                rel="noreferrer"
                className="mt-2 inline-block text-xs text-primary hover:underline"
              >
                Open the full upgrade recovery guide
              </a>
            </div>
          )}
          {renderUpdaterImageStatus('plan')}

          {plan && (
            <div className="space-y-2 text-sm">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div className="p-3 border border-border rounded-lg">
                  <div className="text-xs text-muted-foreground">Will rebuild</div>
                  <div className="mt-1 font-mono text-xs">{plan.services_rebuild?.length ? plan.services_rebuild.join(', ') : 'none'}</div>
                </div>
                <div className="p-3 border border-border rounded-lg">
                  <div className="text-xs text-muted-foreground">Will restart</div>
                  <div className="mt-1 font-mono text-xs">{plan.services_restart?.length ? plan.services_restart.join(', ') : 'none'}</div>
                </div>
                <div className="p-3 border border-border rounded-lg">
                  <div className="text-xs text-muted-foreground">Skipped</div>
                  <div className="mt-1 font-mono text-xs">
                    {plan.skipped_services && Object.keys(plan.skipped_services).length
                      ? Object.entries(plan.skipped_services)
                          .map(([k, v]) => `${k}:${v}`)
                          .join(', ')
                      : 'none'}
                  </div>
                </div>
              </div>

              <div className="text-xs text-muted-foreground">
                Target: <span className="font-mono">{targetRef || 'unknown'}</span> • files changed: {plan.changed_file_count} • compose changed:{' '}
                {plan.compose_changed ? 'yes' : 'no'}
              </div>

              {plan.warnings?.length ? (
                <div className="text-xs text-yellow-500">
                  {plan.warnings.map((w, i) => (
                    <div key={i}>{w}</div>
                  ))}
                </div>
              ) : null}

              {plan.dirty ? (
                <div className="rounded-md border border-yellow-500/40 bg-yellow-500/10 p-3 text-xs text-yellow-700 dark:text-yellow-300">
                  <div className="font-medium">Local code changes detected</div>
                  <div className="mt-1 text-muted-foreground">
                    Choose whether the updater should keep tracked local edits or replace them with the release code. Operator config is backed up before either path.
                  </div>
                  <div className="mt-3 space-y-2">
                    <label className="flex items-start gap-2">
                      <input
                        type="radio"
                        name="aava-local-changes"
                        checked={localChangesPolicy === 'retain'}
                        onChange={() => setLocalChangesPolicy('retain')}
                        className="mt-0.5 rounded border-border"
                      />
                      <span>
                        <span className="font-medium">Retain local changes</span>
                        <span className="block text-muted-foreground">Stash and reapply tracked edits after the update. This can still conflict if the release changed the same code.</span>
                      </span>
                    </label>
                    <label className="flex items-start gap-2">
                      <input
                        type="radio"
                        name="aava-local-changes"
                        checked={localChangesPolicy === 'overwrite'}
                        onChange={() => setLocalChangesPolicy('overwrite')}
                        className="mt-0.5 rounded border-border"
                      />
                      <span>
                        <span className="font-medium">Overwrite local code changes</span>
                        <span className="block text-muted-foreground">Discard tracked source-code edits and restore operator config from the pre-update backup.</span>
                      </span>
                    </label>
                    <label className="flex items-start gap-2">
                      <input
                        type="radio"
                        name="aava-local-changes"
                        checked={localChangesPolicy === 'abort'}
                        onChange={() => setLocalChangesPolicy('abort')}
                        className="mt-0.5 rounded border-border"
                      />
                      <span>
                        <span className="font-medium">Abort update</span>
                        <span className="block text-muted-foreground">Stop before changing the checkout so the local edits can be reviewed manually.</span>
                      </span>
                    </label>
                  </div>
                  {plan.local_files?.length ? (
                    <pre className="mt-3 max-h-[160px] overflow-auto whitespace-pre-wrap break-words rounded-md bg-background/70 p-2 font-mono">
                      {plan.local_files.join('\n')}
                      {plan.local_files_truncated ? '\n...(truncated)' : ''}
                    </pre>
                  ) : null}
                </div>
              ) : null}

              {activeCallOverrideRequired ? (
                <div className="rounded-md border border-yellow-500/40 bg-yellow-500/10 p-3 text-xs text-yellow-700 dark:text-yellow-300">
                  <div className="font-medium">
                    {planHasActiveCalls ? `Active calls detected: ${plan.active_calls}` : 'Active-call status unavailable'}
                  </div>
                  {!planHasActiveCalls && (
                    <div className="mt-1 text-muted-foreground">
                      The updater could not verify whether calls are active. Explicitly allow the update to proceed.
                    </div>
                  )}
                  <label className="mt-2 flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={forceActiveCalls}
                      onChange={(e) => setForceActiveCalls(e.target.checked)}
                      className="rounded border-border"
                    />
                    Allow update to restart services while call status is active or unknown
                  </label>
                </div>
              ) : null}

              {plan.changed_files?.length ? (
                <div className="border border-border rounded-lg bg-card/30 p-3">
                  <div className="text-xs text-muted-foreground mb-2">
                    Files to update ({plan.changed_files.length}
                    {plan.changed_files_truncated ? '+' : ''})
                  </div>
                  <pre className="text-xs font-mono whitespace-pre-wrap break-words max-h-[260px] overflow-auto">
                    {plan.changed_files.join('\n')}
                    {plan.changed_files_truncated ? '\n…(truncated)' : ''}
                  </pre>
                </div>
              ) : null}
            </div>
          )}

          {!plan && initialized && !planLoading && !planError && (
            <div className="text-sm text-muted-foreground">Select a target to see a preview.</div>
          )}
          {!initialized && <div className="text-sm text-muted-foreground">Click “Check updates” first.</div>}
        </div>
      </ConfigCard>

      <ConfigCard>
        <div className="flex items-center gap-2 mb-3">
          <Play className="w-5 h-5" />
          <div className="text-base font-semibold">Proceed</div>
        </div>

        <div className="space-y-3">
          {runError && <div className="text-sm text-destructive">{runError}</div>}
          {renderUpdaterImageStatus('run')}
          <div className="flex items-center gap-2">
            <button
              onClick={runUpdate}
              disabled={actionPending || running || !initialized || !plan || planLoading || localChangeChoiceRequired || (activeCallOverrideRequired && !forceActiveCalls)}
              className="inline-flex items-center gap-2 px-3 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              title="Proceed"
            >
              <Play className="w-4 h-4" />
              {running ? 'Update running…' : 'Proceed'}
            </button>
            {job && (
              <div className="text-sm text-muted-foreground">
                Job: <span className="font-mono text-xs">{job.job_id || jobId}</span>
              </div>
            )}
          </div>

          {job && (
            <div className="flex items-center gap-2 text-sm">
              {String(job.status || '').toLowerCase() === 'success' ? (
                <CheckCircle2 className="w-4 h-4 text-primary" />
              ) : ['failed', 'validation_failed', 'stale'].includes(String(job.status || '').toLowerCase()) ? (
                <XCircle className="w-4 h-4 text-destructive" />
              ) : (
                <RefreshCw className="w-4 h-4 animate-spin text-muted-foreground" />
              )}
              <div className="font-medium capitalize">{String(job.status || 'running').replace(/_/g, ' ')}</div>
              {job.exit_code !== undefined && job.exit_code !== null && <div className="text-muted-foreground">exit={job.exit_code}</div>}
            </div>
          )}

          {job?.failure_reason && (
            <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm">
              <div className="font-medium text-destructive">{job.failed_stage ? String(job.failed_stage).replace(/_/g, ' ') : 'Failure reason'}</div>
              <div className="mt-1 text-muted-foreground">{job.failure_reason}</div>
            </div>
          )}

          <div className="border border-border rounded-lg bg-card/30 p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <div className="text-xs text-muted-foreground">Live output (tail)</div>
              {jobId && (
                <button
                  onClick={fetchFullLog}
                  disabled={fullLogLoading}
                  className="text-xs px-2 py-1 rounded-md border border-border hover:bg-accent disabled:opacity-50"
                  title="Load full log"
                >
                  {fullLogLoading ? 'Loading…' : 'Load full log'}
                </button>
              )}
            </div>
            <pre className="text-xs font-mono whitespace-pre-wrap break-words max-h-[340px] overflow-auto">
              {logTail ||
                (job && isTerminalUpdateStatus(String(job.status || '').toLowerCase())
                  ? 'No log available for this job.'
                  : 'No output yet.')}
            </pre>
          </div>
        </div>
      </ConfigCard>

      <ConfigCard>
        <div className="flex items-center justify-between gap-3 mb-3">
          <div className="flex items-center gap-2">
            <ArrowUpCircle className="w-5 h-5" />
            <div className="text-base font-semibold">Recent Runs</div>
          </div>
          <button
            onClick={fetchHistory}
            disabled={historyLoading}
            className="p-1.5 hover:bg-accent rounded-lg transition-colors disabled:opacity-50"
            aria-label="Refresh history"
            title="Refresh history"
          >
            <RefreshCw className={`w-4 h-4 ${historyLoading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {historyError && <div className="text-sm text-destructive mb-2">{historyError}</div>}
        {!initialized && <div className="text-sm text-muted-foreground mb-2">Click “Check updates” to load history.</div>}

        <div className="overflow-auto border border-border rounded-lg">
          <table className="min-w-[780px] w-full text-sm">
            <thead className="bg-muted/30">
              <tr className="text-left">
                <th className="px-3 py-2 text-xs text-muted-foreground">When</th>
                <th className="px-3 py-2 text-xs text-muted-foreground">Branch</th>
                <th className="px-3 py-2 text-xs text-muted-foreground">Result</th>
                <th className="px-3 py-2 text-xs text-muted-foreground">UI</th>
                <th className="px-3 py-2 text-xs text-muted-foreground">Rebuild</th>
                <th className="px-3 py-2 text-xs text-muted-foreground">Restart</th>
                <th className="px-3 py-2 text-xs text-muted-foreground">Files</th>
                <th className="px-3 py-2 text-xs text-muted-foreground">Recovery</th>
              </tr>
            </thead>
            <tbody>
              {history.length ? (
                history.map((h) => {
                  const st = String(h.status || '').toLowerCase();
                  const plan = h.plan || {};
                  const rebuild = Array.isArray(plan.services_rebuild) ? plan.services_rebuild.join(', ') : '';
                  const restart = Array.isArray(plan.services_restart) ? plan.services_restart.join(', ') : '';
                  const files = plan.changed_file_count ?? '';
                  const when = h.finished_at || h.started_at || '';
                  return (
                    <tr key={h.job_id} className="border-t border-border">
                      <td className="px-3 py-2 font-mono text-xs whitespace-nowrap">{when || '-'}</td>
                      <td className="px-3 py-2 font-mono text-xs">{h.ref || '-'}</td>
                      <td className="px-3 py-2">
                        {st === 'success' ? (
                          <span className="inline-flex items-center gap-1 text-primary">
                            <CheckCircle2 className="w-4 h-4" /> success
                          </span>
                        ) : ['failed', 'validation_failed', 'stale'].includes(st) ? (
                          <span className="inline-flex items-center gap-1 text-destructive">
                            <XCircle className="w-4 h-4" /> {st.replace(/_/g, ' ')}
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-muted-foreground">
                            <RefreshCw className="w-4 h-4 animate-spin" /> {st || 'unknown'}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-xs">{h.include_ui ? 'yes' : 'no'}</td>
                      <td className="px-3 py-2 font-mono text-xs">{rebuild || '-'}</td>
                      <td className="px-3 py-2 font-mono text-xs">{restart || '-'}</td>
                      <td className="px-3 py-2 font-mono text-xs">{files !== '' ? String(files) : '-'}</td>
                      <td className="px-3 py-2">
                        {canRollbackJob(h, st) ? (
                          <div className="inline-flex items-center gap-2">
                            <button
                              onClick={() => rollbackFromJob(h)}
                              disabled={actionPending || running}
                              className="px-2 py-1 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
                              title="Rollback using this job's backup"
                            >
                              Rollback
                            </button>
                            <button
                              onClick={() => copyRecoveryCommands(h)}
                              className="px-2 py-1 text-xs rounded-md border border-border hover:bg-accent transition-colors"
                              title="Copy rollback commands"
                            >
                              {copiedJobId === h.job_id ? 'Copied' : 'Copy'}
                            </button>
                          </div>
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan={8} className="px-3 py-6 text-center text-sm text-muted-foreground">
                    {historyLoading ? 'Loading…' : 'No recent runs yet.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </ConfigCard>
    </ConfigSection>
  );
};

export default UpdatesPage;
