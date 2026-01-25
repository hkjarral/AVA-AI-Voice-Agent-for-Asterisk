import { useEffect, useMemo, useState } from 'react';
import { ArrowUpCircle, RefreshCw, Play, AlertTriangle, CheckCircle2, XCircle } from 'lucide-react';
import axios from 'axios';
import { ConfigSection } from '../../components/ui/ConfigSection';
import { ConfigCard } from '../../components/ui/ConfigCard';

type UpdateAvailable = boolean | null;

interface UpdatesStatus {
  local: { branch?: string; head_sha: string; describe: string };
  remote?: { latest_tag: string; latest_tag_sha: string } | null;
  update_available?: UpdateAvailable;
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

const UpdatesPage = () => {
  const [status, setStatus] = useState<UpdatesStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);

  const [branches, setBranches] = useState<string[]>([]);
  const [branchesError, setBranchesError] = useState<string | null>(null);
  const [selectedBranch, setSelectedBranch] = useState('main');
  const [initialized, setInitialized] = useState(false);

  const [includeUI, setIncludeUI] = useState(false);
  const [plan, setPlan] = useState<UpdatePlan | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);

  const [jobId, setJobId] = useState<string | null>(() => localStorage.getItem('aava_update_job_id'));
  const [job, setJob] = useState<any>(null);
  const [logTail, setLogTail] = useState<string>('');
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  const pickDefaultBranch = (remoteBranches: string[], localBranch?: string) => {
    const uniq = Array.from(new Set(remoteBranches || []));
    if (selectedBranch && uniq.includes(selectedBranch)) return selectedBranch;
    if (localBranch && uniq.includes(localBranch)) return localBranch;
    if (uniq.includes('main')) return 'main';
    return uniq[0] || 'main';
  };

  const checkUpdates = async () => {
    setInitialized(false);
    setPlan(null);
    setPlanError(null);
    setRunError(null);
    setStatusError(null);
    setBranchesError(null);

    setStatusLoading(true);
    try {
      const [statusRes, branchesRes] = await Promise.all([
        axios.get<UpdatesStatus>('/api/system/updates/status'),
        axios.get<BranchesResponse>('/api/system/updates/branches'),
      ]);

      setStatus(statusRes.data);
      setBranches(branchesRes.data.branches || []);
      if (branchesRes.data.error) setBranchesError(branchesRes.data.error);

      const def = pickDefaultBranch(branchesRes.data.branches || [], statusRes.data.local?.branch);
      setSelectedBranch(def);
      setInitialized(true);
    } catch (err: any) {
      setStatusError(err.response?.data?.detail || err.message || 'Failed to check updates');
      setInitialized(false);
    } finally {
      setStatusLoading(false);
    }
  };

  const fetchPlan = async (ref?: string) => {
    setPlanLoading(true);
    setPlanError(null);
    try {
      const res = await axios.get('/api/system/updates/plan', {
        params: { ref: ref || selectedBranch, include_ui: includeUI, checkout: true },
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
    setRunning(st === 'running');
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
        `Target branch: ${selectedBranch}`,
        `Update UI too: ${includeUI ? 'yes' : 'no'}`,
        `Will rebuild: ${rebuild}`,
        `Will restart: ${restart}`,
        `Skipped: ${skipped}`,
        `Files changed: ${plan.changed_file_count ?? 'unknown'}`,
        '',
        'Notes:',
        '- The updater will stash local changes first (may conflict on restore).',
        '- Services may restart during update.',
        '- Successful update logs are auto-pruned; failed update logs are retained.',
      ].join('\n')
    );
    if (!ok) return;

    try {
      const res = await axios.post('/api/system/updates/run', { include_ui: includeUI, ref: selectedBranch, checkout: true });
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
    fetchPlan();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialized, includeUI, selectedBranch]);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        await fetchJob(jobId);
      } catch (err: any) {
        if (!cancelled) setRunError(err.response?.data?.detail || err.message || 'Failed to read update job');
      }
    };
    tick();
    const interval = setInterval(tick, 2000);
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
      icon={<ArrowUpCircle className="w-5 h-5" />}
    >
      <ConfigCard
        title="Check Updates"
        icon={<ArrowUpCircle className="w-5 h-5" />}
        action={
          <button
            onClick={checkUpdates}
            disabled={statusLoading}
            className="inline-flex items-center gap-2 px-3 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            title="Check updates"
          >
            <RefreshCw className={`w-4 h-4 ${statusLoading ? 'animate-spin' : ''}`} />
            {statusLoading ? 'Checking…' : 'Check updates'}
          </button>
        }
      >
        <div className="p-4 space-y-2">
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
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Remote (latest v*)</div>
                <div className="font-mono text-xs break-all">{status.remote?.latest_tag || 'Unknown'}</div>
              </div>
            </div>
          ) : (
            <div className="text-sm text-muted-foreground">Click “Check updates” to load status and branches.</div>
          )}
        </div>
      </ConfigCard>

      <ConfigCard
        title="Select Branch + Preview"
        icon={<RefreshCw className="w-5 h-5" />}
        action={
          <button
            onClick={() => fetchPlan()}
            disabled={!initialized || planLoading}
            className="p-1.5 hover:bg-accent rounded-lg transition-colors"
            title="Refresh preview"
          >
            <RefreshCw className={`w-4 h-4 ${planLoading ? 'animate-spin' : ''}`} />
          </button>
        }
      >
        <div className="p-4 space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <div className="text-xs text-muted-foreground mb-1">Target branch</div>
              <select
                value={selectedBranch}
                onChange={(e) => setSelectedBranch(e.target.value)}
                disabled={!initialized || !branches.length}
                className="w-full px-3 py-2 rounded-md border border-border bg-background text-sm"
              >
                {(branches.length ? branches : [selectedBranch]).map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
              </select>
              {!branches.length && initialized && <div className="mt-1 text-xs text-muted-foreground">No branches returned.</div>}
            </div>
            <div className="flex items-end">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={includeUI}
                  onChange={(e) => setIncludeUI(e.target.checked)}
                  className="rounded border-border"
                />
                Update UI too (allow admin_ui rebuild/restart)
              </label>
            </div>
          </div>

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
                Branch: <span className="font-mono">{selectedBranch}</span> • files changed: {plan.changed_file_count} • compose changed:{' '}
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
            <div className="text-sm text-muted-foreground">Select a branch to see a preview.</div>
          )}
          {!initialized && <div className="text-sm text-muted-foreground">Click “Check updates” first.</div>}
        </div>
      </ConfigCard>

      <ConfigCard title="Proceed" icon={<Play className="w-5 h-5" />}>
        <div className="p-4 space-y-3">
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
              {logTail || (job && job.status === 'success' ? 'Logs pruned after successful update.' : 'No output yet.')}
            </pre>
          </div>
        </div>
      </ConfigCard>
    </ConfigSection>
  );
};

export default UpdatesPage;
