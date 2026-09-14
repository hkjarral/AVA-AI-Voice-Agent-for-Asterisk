import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  CheckCircle2,
  Copy,
  Download,
  FileArchive,
  Loader2,
  ShieldCheck,
  XCircle,
} from 'lucide-react';
import { toast } from 'sonner';

type LogEvent = {
  ts: string | null;
  level: string;
  msg: string;
  category: string;
  component: string | null;
};

type Preview = {
  call: {
    call_id: string;
    start_time: string | null;
    duration_seconds: number;
    provider_name: string;
    pipeline_name: string | null;
    agent: string | null;
    outcome: string;
  };
  analysis: {
    status: 'healthy' | 'review' | 'issues_found' | 'incomplete';
    headline: string;
    event_count: number;
    findings: Array<{ severity: string; message: string; component?: string | null }>;
    lifecycle: Array<{ name: string; captured: boolean }>;
    missing_evidence: string[];
    recommendation: string;
  };
  settings: Record<string, unknown>;
  tool_counts: { pre_call: number; in_call: number; post_call: number };
  log_evidence: {
    available: boolean;
    format: string;
    observed_levels: string[];
    matching_events: number;
    original_bytes?: number;
    truncated?: boolean;
  };
  sources: Record<string, { selected: boolean; required: boolean; recommended?: boolean; reason: string }>;
};

type Props = {
  callId: string;
  events: LogEvent[];
  onChooseAnotherCall: () => void;
};

const tabs = ['Summary', 'Lifecycle', 'Settings', 'Tools', 'Technical Logs'] as const;
type Tab = (typeof tabs)[number];

const downloadBlob = (data: BlobPart, filename: string) => {
  const url = window.URL.createObjectURL(new Blob([data]));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
};

const formatBytes = (bytes = 0) => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const CallTroubleshootView = ({ callId, events, onChooseAnotherCall }: Props) => {
  const navigate = useNavigate();
  const [preview, setPreview] = useState<Preview | null>(null);
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState(false);
  const [tab, setTab] = useState<Tab>('Summary');
  const [options, setOptions] = useState({
    include_local_ai_server: true,
    include_admin_ui: true,
    include_transcript: true,
    include_tools: true,
    include_settings: true,
  });

  useEffect(() => {
    let active = true;
    setLoading(true);
    axios
      .get<Preview>('/api/support/call-preview', { params: { call_id: callId } })
      .then(response => {
        if (active) setPreview(response.data);
      })
      .catch(error => {
        console.error('Failed to load call support preview', error);
        if (active) toast.error('Could not prepare troubleshooting summary');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [callId]);

  const downloadPackage = async () => {
    setDownloading(true);
    try {
      const response = await axios.post(
        '/api/support/call-bundle',
        { call_id: callId, ...options },
        { responseType: 'blob' },
      );
      const disposition = String(response.headers?.['content-disposition'] || '');
      const match = disposition.match(/filename="?([^";]+)"?/i);
      const filename = match?.[1] || `ava-call-support-${callId}.zip`;
      downloadBlob(response.data, filename);
      toast.success('Support package downloaded');
    } catch (error) {
      console.error('Failed to download support package', error);
      toast.error('Failed to download support package');
    } finally {
      setDownloading(false);
    }
  };

  const outcomeClass = ['error', 'failed', 'abandoned', 'no_input_timeout'].includes(preview?.call.outcome || '')
    ? 'border-red-800 bg-red-500/10 text-red-300'
    : preview?.call.outcome === 'completed'
      ? 'border-emerald-800 bg-emerald-500/10 text-emerald-300'
      : 'border-amber-800 bg-amber-500/10 text-amber-300';
  const statusClass = preview?.analysis.status === 'issues_found'
    ? 'border-red-800 bg-red-500/10 text-red-200'
    : preview?.analysis.status === 'healthy'
      ? 'border-emerald-800 bg-emerald-500/10 text-emerald-200'
      : 'border-amber-800 bg-amber-500/10 text-amber-200';

  const toolTotal = useMemo(() => preview
    ? preview.tool_counts.pre_call + preview.tool_counts.in_call + preview.tool_counts.post_call
    : 0, [preview]);

  if (loading) {
    return (
      <div className="flex min-h-[420px] items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" /> Preparing call evidence…
      </div>
    );
  }

  if (!preview) {
    return (
      <div className="rounded-lg border p-8 text-center">
        <XCircle className="mx-auto mb-3 h-8 w-8 text-red-400" />
        <h2 className="text-lg font-semibold">Call evidence is unavailable</h2>
        <p className="mt-1 text-sm text-muted-foreground">The Call History record or retained logs could not be loaded.</p>
        <button onClick={onChooseAnotherCall} className="mt-4 rounded-md border px-3 py-2 text-sm hover:bg-muted">
          Choose another call
        </button>
      </div>
    );
  }

  const sourceRow = (
    key: 'include_local_ai_server' | 'include_admin_ui',
    label: string,
    sourceKey: 'local_ai_server' | 'admin_ui',
  ) => {
    const source = preview.sources[sourceKey];
    return (
      <label className="flex cursor-pointer items-start gap-3 rounded-md p-1.5 hover:bg-muted/30">
        <input
          type="checkbox"
          checked={options[key]}
          onChange={event => setOptions(current => ({ ...current, [key]: event.target.checked }))}
          className="mt-1"
        />
        <span>
          <span className="flex items-center gap-2 text-sm font-medium">
            {label}
            {source?.recommended && <span className="rounded border border-blue-800 bg-blue-500/10 px-1.5 py-0.5 text-[10px] text-blue-300">Recommended</span>}
          </span>
          <span className="block text-xs text-muted-foreground">{source?.reason}</span>
        </span>
      </label>
    );
  };

  return (
    <div className="space-y-4 pb-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="mb-2 text-xs text-muted-foreground">Admin&nbsp;&nbsp;›&nbsp;&nbsp;Call History&nbsp;&nbsp;›&nbsp;&nbsp;Troubleshoot</div>
          <h1 className="text-3xl font-bold tracking-tight">Troubleshoot Call</h1>
          <p className="mt-1 text-muted-foreground">Review what happened and download a safe support package.</p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => navigate('/history')} className="inline-flex h-9 items-center gap-2 rounded-md border px-3 text-sm hover:bg-muted">
            <ArrowLeft className="h-4 w-4" /> Back to Call History
          </button>
          <button onClick={downloadPackage} disabled={downloading} className="inline-flex h-9 items-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60">
            {downloading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            Download Support Package
          </button>
        </div>
      </div>

      <div className="grid gap-3 rounded-lg border bg-background p-4 sm:grid-cols-3 lg:grid-cols-7">
        <div><div className="text-xs text-muted-foreground">Call ID</div><div className="mt-1 flex items-center gap-2 font-mono text-sm font-semibold">{preview.call.call_id}<button onClick={() => { navigator.clipboard.writeText(preview.call.call_id); toast.success('Call ID copied'); }} title="Copy Call ID"><Copy className="h-3.5 w-3.5 text-blue-400" /></button></div></div>
        <div><div className="text-xs text-muted-foreground">Date</div><div className="mt-1 text-sm">{preview.call.start_time ? new Date(preview.call.start_time).toLocaleString() : 'Unknown'}</div></div>
        <div><div className="text-xs text-muted-foreground">Duration</div><div className="mt-1 text-sm font-medium">{Math.round(preview.call.duration_seconds)}s</div></div>
        <div><div className="text-xs text-muted-foreground">Provider</div><div className="mt-1 text-sm font-medium">{preview.call.provider_name}</div></div>
        <div><div className="text-xs text-muted-foreground">Agent</div><div className="mt-1 truncate text-sm font-medium">{preview.call.agent || preview.call.pipeline_name || 'Default'}</div></div>
        <div><div className="text-xs text-muted-foreground">Outcome</div><span className={`mt-1 inline-flex rounded border px-2 py-0.5 text-xs ${outcomeClass}`}>{preview.call.outcome}</span></div>
        <div><div className="text-xs text-muted-foreground">Evidence</div><div className="mt-1 flex flex-wrap gap-1"><span className="rounded border px-1.5 py-0.5 text-[10px]">{preview.log_evidence.matching_events} events</span><span className="rounded border px-1.5 py-0.5 text-[10px] uppercase">{preview.log_evidence.observed_levels.join('/') || 'unknown'}</span><span className="rounded border px-1.5 py-0.5 text-[10px] uppercase">{preview.log_evidence.format}</span></div></div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.65fr_1fr]">
        <div className="space-y-3 rounded-lg border bg-background p-4">
          <div className="flex items-center justify-between"><h2 className="text-lg font-semibold">Diagnostic Summary</h2><button onClick={() => setTab('Technical Logs')} className="text-xs text-blue-400 hover:underline">View evidence →</button></div>
          <div className={`rounded-lg border p-4 ${statusClass}`}>
            <div className="flex items-start gap-3"><AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" /><div><div className="font-semibold">{preview.analysis.headline}</div><div className="mt-1 text-sm opacity-80">{preview.analysis.recommendation}</div></div></div>
          </div>
          {preview.analysis.findings.slice(0, 3).map((finding, index) => (
            <div key={`${finding.message}-${index}`} className="flex gap-3 rounded-lg border p-3">
              <AlertTriangle className={`mt-0.5 h-5 w-5 shrink-0 ${finding.severity === 'error' ? 'text-red-400' : 'text-amber-400'}`} />
              <div><div className="text-sm font-medium">{finding.message}</div>{finding.component && <div className="mt-1 text-xs text-muted-foreground">{finding.component}</div>}</div>
            </div>
          ))}
          {!preview.analysis.findings.length && preview.analysis.lifecycle.slice(0, 4).map(stage => (
            <div key={stage.name} className="flex items-center gap-3 rounded-lg border p-3">
              {stage.captured ? <CheckCircle2 className="h-5 w-5 text-emerald-400" /> : <XCircle className="h-5 w-5 text-amber-400" />}
              <span className="text-sm font-medium">{stage.name}</span>
            </div>
          ))}
        </div>

        <div className="rounded-lg border bg-background p-4">
          <div className="flex items-center gap-2"><FileArchive className="h-5 w-5" /><h2 className="text-lg font-semibold">Support Package</h2></div>
          <p className="mb-3 text-xs text-muted-foreground">Ready to share on Discord or attach to a GitHub issue.</p>
          <div className="space-y-1">
            <div className="flex items-start gap-3 p-1.5"><span className="mt-0.5 flex h-4 w-4 items-center justify-center rounded bg-blue-500"><Check className="h-3 w-3 text-white" /></span><span><span className="flex items-center gap-2 text-sm font-medium">AI Engine logs <span className="rounded border border-blue-800 bg-blue-500/10 px-1.5 py-0.5 text-[10px] text-blue-300">Required</span></span><span className="block text-xs text-muted-foreground">Core lifecycle and call-correlated evidence.</span></span></div>
            {sourceRow('include_local_ai_server', 'Local AI Server logs', 'local_ai_server')}
            {sourceRow('include_admin_ui', 'Admin UI logs', 'admin_ui')}
            {([
              ['include_transcript', 'Sanitized transcript', 'Conversation text only (no recordings)'],
              ['include_tools', 'Tool executions — pre / in-call / post', `${toolTotal} tool execution${toolTotal === 1 ? '' : 's'} captured`],
              ['include_settings', 'Effective call settings', 'Provider, Audio Profile, transport, codec, VAD and streaming'],
            ] as const).map(([key, label, helper]) => (
              <label key={key} className="flex cursor-pointer items-start gap-3 rounded-md p-1.5 hover:bg-muted/30"><input type="checkbox" checked={options[key]} onChange={event => setOptions(current => ({ ...current, [key]: event.target.checked }))} className="mt-1" /><span><span className="block text-sm font-medium">{label}</span><span className="block text-xs text-muted-foreground">{helper}</span></span></label>
            ))}
          </div>
          <div className="my-3 flex gap-2 rounded-md border p-3 text-xs text-muted-foreground"><ShieldCheck className="h-4 w-4 shrink-0 text-blue-400" />No recordings, phone numbers, API keys or secrets.</div>
          <button onClick={downloadPackage} disabled={downloading} className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60">{downloading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}Download Support Package (.zip)</button>
          <div className="mt-2 text-center text-xs text-muted-foreground">AI Engine evidence: {formatBytes(preview.log_evidence.original_bytes)}</div>
        </div>
      </div>

      <div className="rounded-lg border bg-background">
        <div className="flex overflow-x-auto border-b px-2">
          {tabs.map(item => <button key={item} onClick={() => setTab(item)} className={`border-b-2 px-4 py-3 text-sm ${tab === item ? 'border-primary font-medium text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'}`}>{item}</button>)}
        </div>
        <div className="p-4">
          {tab === 'Summary' && <div className="grid gap-3 md:grid-cols-2"><div className="rounded-md border p-3"><div className="text-xs text-muted-foreground">Call result</div><div className="mt-1 font-medium">{preview.call.outcome}</div></div><div className="rounded-md border p-3"><div className="text-xs text-muted-foreground">Evidence coverage</div><div className="mt-1 font-medium">{preview.analysis.lifecycle.filter(stage => stage.captured).length} of {preview.analysis.lifecycle.length} lifecycle stages</div></div></div>}
          {tab === 'Lifecycle' && <div className="space-y-2">{preview.analysis.lifecycle.map(stage => <div key={stage.name} className="flex items-center gap-3 text-sm">{stage.captured ? <CheckCircle2 className="h-4 w-4 text-emerald-400" /> : <XCircle className="h-4 w-4 text-amber-400" />}<span>{stage.name}</span><span className="text-xs text-muted-foreground">{stage.captured ? 'Captured' : 'Not found in retained logs'}</span></div>)}</div>}
          {tab === 'Settings' && <pre className="max-h-[420px] overflow-auto whitespace-pre-wrap text-xs text-muted-foreground">{Object.keys(preview.settings).length ? JSON.stringify(preview.settings, null, 2) : 'A call-time settings snapshot is not available for this historical call.'}</pre>}
          {tab === 'Tools' && <div className="grid gap-3 sm:grid-cols-3">{Object.entries(preview.tool_counts).map(([phase, count]) => <div key={phase} className="rounded-md border p-3"><div className="text-xs capitalize text-muted-foreground">{phase.replace('_', ' ')}</div><div className="mt-1 text-xl font-semibold">{count}</div></div>)}</div>}
          {tab === 'Technical Logs' && <div className="max-h-[480px] space-y-1 overflow-auto font-mono text-xs">{events.map((event, index) => <div key={`${event.ts}-${index}`} className="rounded px-2 py-1 hover:bg-muted/30"><span className="mr-2 text-muted-foreground">{event.ts ? new Date(event.ts).toLocaleTimeString() : '--:--:--'}</span><span className={`mr-2 uppercase ${event.level === 'error' ? 'text-red-400' : event.level === 'warning' ? 'text-amber-400' : 'text-blue-300'}`}>{event.level}</span><span>{event.msg}</span></div>)}{!events.length && <div className="text-muted-foreground">No retained technical events were found for this call.</div>}</div>}
        </div>
      </div>
    </div>
  );
};

export default CallTroubleshootView;
