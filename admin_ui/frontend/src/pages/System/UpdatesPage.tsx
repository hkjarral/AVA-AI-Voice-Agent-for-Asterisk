import { useEffect, useMemo, useState } from 'react';
import { ArrowUpCircle, RefreshCw, Play, AlertTriangle, CheckCircle2, XCircle } from 'lucide-react';
import axios from 'axios';
import { ConfigSection } from '../../components/ui/ConfigSection';
import { ConfigCard } from '../../components/ui/ConfigCard';

type UpdateAvailable = boolean | null;

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
  would_stash: boolean;
  would_abort: boolean;
  rebuild_mode: string;
  compose_changed: boolean;
  services_rebuild: string[];
  services_restart: string[];
  skipped_services?: Record<string, string>;
  changed_file_count: number;
  changed_files?: string[];
  changed_files_truncated?: boolean;
  warnings?: string[];
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

const UpdatesPage = () => {
  const [copiedJobId, setCopiedJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<UpdatesStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);

  const [branches, setBranches] = useState<string[]>([]);
  const [branchesError, setBranchesError] = useState<string | null>(null);
  const [selectedBranch, setSelectedBranch] = useState('main');
  const [targetMode, setTargetMode] = useState<'stable' | 'main' | 'advanced'>('stable');
  const [initialized, setInitialized] = useState(false);

  const [includeUI, setIncludeUI] = useState(false);
  const [updateCliHost, setUpdateCliHost] = useState(true);
  const [cliInstallPath, setCliInstallPath] = useState('');
  const [plan, setPlan] = useState<UpdatePlan | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);

  const [jobId, setJobId] = useState<string | null>(() => localStorage.getItem('aava_update_job_id'));
  const [job, setJob] = useState<any>(null);
  const [logTail, setLogTail] = useState<string>('');
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  const [history, setHistory] = useState<any[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const pickDefaultBranch = (remoteBranches: string[], localBranch?: string) => {
    const uniq = Array.from(new Set(remoteBranches || []));
    if (selectedBranch && uniq.includes(selectedBranch)) return selectedBranch;
    if (localBranch && uniq.includes(localBranch)) return localBranch;
    if (uniq.includes('main')) return 'main';
    return uniq[0] || 'main';
  };

  const targetRef = useMemo(() => {
    if (targetMode === 'stable') return status?.remote?.latest_tag || '';
    if (targetMode === 'main') return 'main';
    return selectedBranch;
  }, [targetMode, status, selectedBranch]);

  const targetCheckout = useMemo(() => {
    // For release tags, avoid branch switching semantics. For main/branches, allow checkout.
    return targetMode !== 'stable';
  }, [targetMode]);

  const loadBranches = async (opts?: { force?: boolean; localBranch?: string }) => {
    setBranchesError(null);
    try {
      const branchesRes = await axios.get<BranchesResponse>('/api/system/updates/branches', {
        // Branch listing requires git; if the updater image isn't present, allow a local build fallback.
        params: { build_updater: true, force: opts?.force ? true : false },
      });
      setBranches(branchesRes.data.branches || []);
      if (branchesRes.data.error) setBranchesError(branchesRes.data.error);
      const def = pickDefaultBranch(branchesRes.data.branches || [], opts?.localBranch ?? status?.local?.branch);
      setSelectedBranch(def);
    } catch (err: any) {
      setBranchesError(err.response?.data?.detail || err.message || 'Failed to load branches');
      setBranches([]);
    }
  };

  const checkUpdates = async (opts?: { force?: boolean }) => {
    setInitialized(false);
    setPlan(null);
    setPlanError(null);
    setRunError(null);
    setStatusError(null);
    setBranchesError(null);

    setStatusLoading(true);
    try {
      const statusRes = await axios.get<UpdatesStatus>('/api/system/updates/status', {
        // Allow updater image local-build fallback on explicit user action ("Check updates").
        params: { check_remote: true, build_updater: true, force: opts?.force ? true : false },
      });

      setStatus(statusRes.data);
      const localDef = statusRes.data.local?.branch || 'main';
      setSelectedBranch(localDef === 'detached' ? 'main' : localDef);
      setInitialized(true);

      // Best-effort: load recent history after a check.
      fetchHistory();

      // Advanced mode: fetch branches for branch selection.
      if (targetMode === 'advanced') {
        loadBranches({ localBranch: localDef });
      }
    } catch (err: any) {
      setStatusError(err.response?.data?.detail || err.message || 'Failed to check updates');
      setInitialized(false);
    } finally {
      setStatusLoading(false);
    }
  };

  const fetchHistory = async () => {
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const res = await axios.get<UpdateHistoryResponse>('/api/system/updates/history', { params: { limit: 10 } });
      setHistory(res.data.jobs || []);
    } catch (err: any) {
      setHistoryError(err.response?.data?.detail || err.message || 'Failed to load update history');
      setHistory([]);
    } finally {
      setHistoryLoading(false);
    }
  };

  const copyRecoveryCommands = async (job: any) => {
    const preBranch = job?.pre_update_branch;
    const backupRel = job?.backup_dir_rel;
    if (!preBranch || !backupRel) return;

    const composeTargets = job?.include_ui ? 'ai_engine local_ai_server admin_ui' : 'ai_engine local_ai_server';
    const text = [
      '# Roll back to pre-update code + restore operator config',
      '# NOTE: adjust REPO if your checkout path differs.',
      'REPO=/root/Asterisk-AI-Voice-Agent',
      'cd \"$REPO\"',
      'git config --global --add safe.directory \"$REPO\"',
      '',
      `git checkout \"${preBranch}\"`,
      '',
      `cp \"${backupRel}/.env\" .env`,
      `cp \"${backupRel}/config/ai-agent.yaml\" config/ai-agent.yaml`,
      `cp \"${backupRel}/config/users.json\" config/users.json`,
      `rm -rf config/contexts && cp -r \"${backupRel}/config/contexts\" config/contexts`,
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

  const rollbackFromJob = async (sourceJob: any) => {
    const fromJobId = sourceJob?.job_id;
    if (!fromJobId) return;

    const preBranch = sourceJob?.pre_update_branch || 'unknown';
    const backupRel = sourceJob?.backup_dir_rel || 'unknown';
    const ok = window.confirm(
      [
        'Start rollback?',
        '',
        `Source job: ${fromJobId}`,
        `Pre-update branch: ${preBranch}`,
        `Backup: ${backupRel}`,
        '',
        'Notes:',
        '- This will checkout the pre-update branch and restore operator config from the backup.',
        '- Services may rebuild/restart (including admin_ui if it was included in the original update).',
      ].join('\n')
    );
    if (!ok) return;

    setRunError(null);
    try {
      const res = await axios.post('/api/system/updates/rollback', { from_job_id: fromJobId });
      const id = res.data.job_id;
      setJobId(id);
      localStorage.setItem('aava_update_job_id', id);
      setRunning(true);
    } catch (err: any) {
      setRunError(err.response?.data?.detail || err.message || 'Failed to start rollback');
    }
  };

  const fetchPlan = async (ref?: string) => {
    setPlanLoading(true);
    setPlanError(null);
    try {
      const res = await axios.get('/api/system/updates/plan', {
        params: { ref: ref || targetRef, include_ui: includeUI, checkout: targetCheckout },
      });
      setPlan(res.data.plan);
    } catch (err: any) {
      setPlanError(err.response?.data?.detail || err.message || 'Failed to compute update plan');
    } finally {
      setPlanLoading(false);
    }
  };

  const fetchJob = async (id: string) => {
    const res = await axios.get<UpdateJobResponse>(`/api/system/updates/jobs/${id}`);
    setJob(res.data.job);
    setLogTail(res.data.log_tail || '');
    const st = (res.data.job?.status || '').toLowerCase();
    setRunning(!(st === 'success' || st === 'failed'));
    setRunError(null);

    if (st === 'success' || st === 'failed') {
      fetchHistory();
    }
    return st;
  };

  const runUpdate = async () => {
    setRunError(null);
    if (!initialized) {
      setRunError('Click “Check updates” first.');
      return;
    }
    if (!plan) {
      setRunError('Wait for the preview to load, then proceed.');
      return;
    }

    const rebuild = plan.services_rebuild?.length ? plan.services_rebuild.join(', ') : 'none';
    const restart = plan.services_restart?.length ? plan.services_restart.join(', ') : 'none';
    const skipped =
      plan.skipped_services && Object.keys(plan.skipped_services).length
        ? Object.entries(plan.skipped_services)
            .map(([k, v]) => `${k}:${v}`)
            .join(', ')
        : 'none';

    const ok = window.confirm(
      [
        'Proceed with update?',
        '',
        `Target: ${targetRef || 'unknown'}`,
        `Update UI too: ${includeUI ? 'yes' : 'no'}`,
        `Update agent CLI too: ${updateCliHost ? 'yes' : 'no'}`,
        updateCliHost ? `Agent CLI install path: ${cliInstallPath.trim() || 'auto'}` : '',
        `Will rebuild: ${rebuild}`,
        `Will restart: ${restart}`,
        `Skipped: ${skipped}`,
        `Files changed: ${plan.changed_file_count ?? 'unknown'}`,
        '',
        'Notes:',
        '- The updater will stash local changes first (may conflict on restore).',
        '- Services may restart during update.',
        '- Update logs are retained (last 10 runs) and visible in the UI after completion.',
      ].join('\n')
    );
    if (!ok) return;

    try {
      const res = await axios.post('/api/system/updates/run', {
        include_ui: includeUI,
        ref: targetRef,
        checkout: targetCheckout,
        update_cli_host: updateCliHost,
        cli_install_path: cliInstallPath.trim() || null,
      });
      const id = res.data.job_id;
      setJobId(id);
      localStorage.setItem('aava_update_job_id', id);
      setRunning(true);
    } catch (err: any) {
      setRunError(err.response?.data?.detail || err.message || 'Failed to start update');
    }
  };

  useEffect(() => {
    if (!initialized) return;
    if (!targetRef) return;
    fetchPlan(targetRef);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialized, includeUI, targetRef, targetCheckout]);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let interval: any;
    let notFoundCount = 0;
    const MAX_NOT_FOUND = 10; // ~20s at 2s intervals
    const tick = async () => {
      try {
        const st = await fetchJob(jobId);
        notFoundCount = 0;
        if (!cancelled && (st === 'success' || st === 'failed')) {
          clearInterval(interval);
        }
      } catch (err: any) {
        const status = err?.response?.status;
        // Immediately after starting a job, there can be a brief delay before the updater container
        // writes its state/log files. Treat 404 as transient to avoid spurious UI errors.
        if (!cancelled) {
          if (status === 404) {
            notFoundCount += 1;
            if (notFoundCount < MAX_NOT_FOUND) return;
            clearInterval(interval);
            setRunning(false);
            setJob(null);
            setJobId(null);
            localStorage.removeItem('aava_update_job_id');
            setRunError('Update job not found (may be stale or pruned).');
            return;
          }
          setRunError(err.response?.data?.detail || err.message || 'Failed to read update job');
        }
      }
    };
    tick();
    interval = setInterval(tick, 2000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [jobId]);

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
                value={cliInstallPath}
                onChange={(e) => setCliInstallPath(e.target.value)}
                placeholder="auto (detect existing or install to /usr/local/bin/agent)"
                className="w-full px-3 py-2 rounded-md border border-border bg-background text-sm font-mono"
              />
              <div className="mt-1 text-xs text-muted-foreground">Leave blank for auto-detect + default install.</div>
            </div>
          )}

          {planError && <div className="text-sm text-destructive">{planError}</div>}

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
          <div className="flex items-center gap-2">
            <button
              onClick={runUpdate}
              disabled={running || !initialized || !plan}
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
              ) : String(job.status || '').toLowerCase() === 'failed' ? (
                <XCircle className="w-4 h-4 text-destructive" />
              ) : (
                <RefreshCw className="w-4 h-4 animate-spin text-muted-foreground" />
              )}
              <div className="font-medium capitalize">{job.status || 'running'}</div>
              {job.exit_code !== undefined && job.exit_code !== null && <div className="text-muted-foreground">exit={job.exit_code}</div>}
            </div>
          )}

          <div className="border border-border rounded-lg bg-card/30 p-3">
            <div className="text-xs text-muted-foreground mb-2">Live output (tail)</div>
            <pre className="text-xs font-mono whitespace-pre-wrap break-words max-h-[340px] overflow-auto">
              {logTail ||
                (job && ['success', 'failed'].includes(String(job.status || '').toLowerCase())
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
                        ) : st === 'failed' ? (
                          <span className="inline-flex items-center gap-1 text-destructive">
                            <XCircle className="w-4 h-4" /> failed
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
                        {st === 'failed' && h.pre_update_branch && h.backup_dir_rel ? (
                          <div className="inline-flex items-center gap-2">
                            <button
                              onClick={() => rollbackFromJob(h)}
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
