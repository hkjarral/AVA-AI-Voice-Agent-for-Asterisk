import { useEffect, useMemo, useState } from 'react';
import { ArrowUpCircle, RefreshCw, Play, AlertTriangle, CheckCircle2, XCircle } from 'lucide-react';
import axios from 'axios';
import { ConfigSection } from '../../components/ui/ConfigSection';
import { ConfigCard } from '../../components/ui/ConfigCard';

type UpdateAvailable = boolean | null;

interface UpdatesStatus {
  local: { head_sha: string; describe: string };
  remote?: { latest_tag: string; latest_tag_sha: string } | null;
  update_available?: UpdateAvailable;
  error?: string | null;
}

interface UpdatePlan {
  repo_root: string;
  remote: string;
  ref: string;
  old_sha: string;
  new_sha: string;
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
  warnings?: string[];
}

interface UpdateJobResponse {
  job: any;
  log_tail?: string | null;
}

const UpdatesPage = () => {
  const [status, setStatus] = useState<UpdatesStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusError, setStatusError] = useState<string | null>(null);

  const [includeUI, setIncludeUI] = useState(false);
  const [plan, setPlan] = useState<UpdatePlan | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);

  const [jobId, setJobId] = useState<string | null>(() => localStorage.getItem('aava_update_job_id'));
  const [job, setJob] = useState<any>(null);
  const [logTail, setLogTail] = useState<string>('');
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  const fetchStatus = async () => {
    setStatusLoading(true);
    setStatusError(null);
    try {
      const res = await axios.get('/api/system/updates/status');
      setStatus(res.data);
    } catch (err: any) {
      setStatusError(err.response?.data?.detail || err.message || 'Failed to load update status');
    } finally {
      setStatusLoading(false);
    }
  };

  const fetchPlan = async () => {
    setPlanLoading(true);
    setPlanError(null);
    try {
      const res = await axios.get('/api/system/updates/plan', { params: { include_ui: includeUI } });
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
    const rebuild = plan?.services_rebuild?.length ? plan.services_rebuild.join(', ') : 'none';
    const restart = plan?.services_restart?.length ? plan.services_restart.join(', ') : 'none';
    const skipped = plan?.skipped_services && Object.keys(plan.skipped_services).length
      ? Object.entries(plan.skipped_services).map(([k, v]) => `${k}:${v}`).join(', ')
      : 'none';

    const ok = window.confirm(
      [
        'Run update now?',
        '',
        `Update UI too: ${includeUI ? 'yes' : 'no'}`,
        `Will rebuild: ${rebuild}`,
        `Will restart: ${restart}`,
        `Skipped: ${skipped}`,
        '',
        'Notes:',
        '- Services may restart during update.',
        "- Successful update logs are auto-pruned; failed update logs are retained.",
      ].join('\n')
    );
    if (!ok) return;

    try {
      const res = await axios.post('/api/system/updates/run', { include_ui: includeUI });
      const id = res.data.job_id;
      setJobId(id);
      localStorage.setItem('aava_update_job_id', id);
      setRunning(true);
    } catch (err: any) {
      setRunError(err.response?.data?.detail || err.message || 'Failed to start update');
    }
  };

  useEffect(() => {
    fetchStatus();
  }, []);

  useEffect(() => {
    fetchPlan();
  }, [includeUI]);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        await fetchJob(jobId);
      } catch (err: any) {
        if (!cancelled) {
          setRunError(err.response?.data?.detail || err.message || 'Failed to read update job');
        }
      }
    };
    tick();
    const interval = setInterval(tick, 2000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [jobId]);

  const statusLabel = useMemo(() => {
    if (!status) return 'Unknown';
    const ua = status.update_available;
    if (ua === true) return `Update available${status.remote?.latest_tag ? ` (${status.remote.latest_tag})` : ''}`;
    if (ua === false) return 'Up to date';
    return 'Unknown';
  }, [status]);

  const statusIcon = useMemo(() => {
    if (!status) return <AlertTriangle className="w-4 h-4 text-muted-foreground" />;
    const ua = status.update_available;
    if (ua === true) return <AlertTriangle className="w-4 h-4 text-yellow-500" />;
    if (ua === false) return <CheckCircle2 className="w-4 h-4 text-primary" />;
    return <AlertTriangle className="w-4 h-4 text-muted-foreground" />;
  }, [status]);

  return (
    <ConfigSection
      title="Updates"
      description="Preview and apply updates via agent update. Logs are kept only when an update fails."
      icon={<ArrowUpCircle className="w-5 h-5" />}
    >
      <ConfigCard
        title="Status"
        icon={<ArrowUpCircle className="w-5 h-5" />}
        action={
          <button
            onClick={fetchStatus}
            disabled={statusLoading}
            className="p-1.5 hover:bg-accent rounded-lg transition-colors"
            title="Refresh status"
          >
            <RefreshCw className={`w-4 h-4 ${statusLoading ? 'animate-spin' : ''}`} />
          </button>
        }
      >
        <div className="p-4 space-y-2">
          {statusError && <div className="text-sm text-destructive">{statusError}</div>}
          {status && status.error && <div className="text-sm text-muted-foreground">{status.error}</div>}
          <div className="flex items-center gap-2">
            {statusIcon}
            <div className="text-sm font-medium">{statusLabel}</div>
          </div>
          {status && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
              <div>
                <div className="text-xs text-muted-foreground">Local</div>
                <div className="font-mono text-xs break-all">{status.local?.describe || 'Unknown'}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Remote (latest v*)</div>
                <div className="font-mono text-xs break-all">{status.remote?.latest_tag || 'Unknown'}</div>
              </div>
            </div>
          )}
        </div>
      </ConfigCard>

      <ConfigCard
        title="Pre-Update Preview"
        icon={<RefreshCw className="w-5 h-5" />}
        action={
          <button
            onClick={fetchPlan}
            disabled={planLoading}
            className="p-1.5 hover:bg-accent rounded-lg transition-colors"
            title="Refresh plan"
          >
            <RefreshCw className={`w-4 h-4 ${planLoading ? 'animate-spin' : ''}`} />
          </button>
        }
      >
        <div className="p-4 space-y-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={includeUI}
              onChange={(e) => setIncludeUI(e.target.checked)}
              className="rounded border-border"
            />
            Update UI too (allow admin_ui rebuild/restart)
          </label>

          {planError && <div className="text-sm text-destructive">{planError}</div>}

          {plan && (
            <div className="space-y-2 text-sm">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div className="p-3 border border-border rounded-lg">
                  <div className="text-xs text-muted-foreground">Will rebuild</div>
                  <div className="mt-1 font-mono text-xs">
                    {plan.services_rebuild?.length ? plan.services_rebuild.join(', ') : 'none'}
                  </div>
                </div>
                <div className="p-3 border border-border rounded-lg">
                  <div className="text-xs text-muted-foreground">Will restart</div>
                  <div className="mt-1 font-mono text-xs">
                    {plan.services_restart?.length ? plan.services_restart.join(', ') : 'none'}
                  </div>
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
                {plan.update_available ? 'Update available' : 'Already up to date'} • changed files: {plan.changed_file_count} • compose changed:{' '}
                {plan.compose_changed ? 'yes' : 'no'}
              </div>

              {plan.warnings?.length ? (
                <div className="text-xs text-yellow-500">
                  {plan.warnings.map((w, i) => (
                    <div key={i}>{w}</div>
                  ))}
                </div>
              ) : null}
            </div>
          )}
        </div>
      </ConfigCard>

      <ConfigCard title="Run Update" icon={<Play className="w-5 h-5" />}>
        <div className="p-4 space-y-3">
          {runError && <div className="text-sm text-destructive">{runError}</div>}
          <div className="flex items-center gap-2">
            <button
              onClick={runUpdate}
              disabled={running}
              className="inline-flex items-center gap-2 px-3 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              title="Run agent update"
            >
              <Play className="w-4 h-4" />
              {running ? 'Update running…' : 'Run update'}
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
              {job.exit_code !== undefined && job.exit_code !== null && (
                <div className="text-muted-foreground">exit={job.exit_code}</div>
              )}
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
