import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { 
  CheckCircle2, 
  AlertTriangle, 
  XCircle, 
  RefreshCw, 
  Copy, 
  ExternalLink,
  Server,
  HardDrive,
  Shield,
  Box,
  Globe,
  Tag
} from 'lucide-react';
import { ConfigCard } from './ui/ConfigCard';
import axios from 'axios';
import { describeApiError } from '../utils/apiErrors';

// Types
interface PlatformCheck {
  id: string;
  status: 'ok' | 'warning' | 'error';
  message: string;
  blocking: boolean;
  action?: {
    type: 'command' | 'link' | 'modal';
    label: string;
    value: string;
    rootless_value?: string;
    docs_url?: string;
    docs_label?: string;
  };
}

interface PlatformInfo {
  project?: {
    version: string;
    source?: string;
  };
  os: {
    id: string;
    version: string;
    family: string;
    arch: string;
    is_eol: boolean;
    in_container: boolean;
  };
  docker: {
    installed: boolean;
    version: string | null;
    mode: string;
    status: string;
    message: string | null;
  };
  compose: {
    installed: boolean;
    version: string | null;
    type: string;
    status: string;
    message: string | null;
  };
  selinux?: {
    present: boolean;
    mode: string | null;
    tools_installed: boolean;
  };
  directories: {
    media: {
      path: string;
      exists: boolean;
      writable: boolean;
      status: string;
    };
  };
  asterisk?: {
    detected: boolean;
    version: string | null;
    config_dir: string | null;
    freepbx: {
      detected: boolean;
      version: string | null;
    };
  };
}

interface PlatformResponse {
  platform: PlatformInfo;
  checks: PlatformCheck[];
  summary: {
    total_checks: number;
    passed: number;
    warnings: number;
    errors: number;
    blocking_errors: number;
    ready: boolean;
  };
}

// Status icon component
const StatusIcon = ({ status }: { status: string }) => {
  switch (status) {
    case 'ok':
      return <CheckCircle2 className="w-4 h-4 text-primary" />;
    case 'warning':
      return <AlertTriangle className="w-4 h-4 text-yellow-500" />;
    case 'error':
      return <XCircle className="w-4 h-4 text-destructive" />;
    default:
      return null;
  }
};

// Copy button component
const CopyButton = ({ text }: { text: string }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  return (
    <button
      onClick={handleCopy}
      className="flex items-center gap-1 px-2 py-1 text-xs bg-accent hover:bg-accent/80 rounded transition-colors text-accent-foreground"
      title="Copy command"
    >
      <Copy className="w-3 h-3" />
      {copied ? 'Copied!' : 'Copy'}
    </button>
  );
};

// Check row component
const CheckRow = ({ check, isRootless }: { check: PlatformCheck; isRootless: boolean }) => {
  const [expanded, setExpanded] = useState(false);

  const actionValue = isRootless && check.action?.rootless_value 
    ? check.action.rootless_value 
    : check.action?.value;

  const docsUrl = check.action?.docs_url;
  const docsLabel = check.action?.docs_label || 'Docs';

  return (
    <div className={`border-b border-border last:border-0 ${check.blocking ? 'bg-destructive/10' : ''}`}>
      <div 
        className="flex items-center justify-between p-3 cursor-pointer hover:bg-accent/50"
        onClick={() => check.action && setExpanded(!expanded)}
      >
        <div className="flex items-center gap-3">
          <StatusIcon status={check.status} />
          <span className={`text-sm ${check.status === 'error' ? 'text-destructive' : 'text-foreground'}`}>
            {check.message}
          </span>
          {check.blocking && (
            <span className="px-2 py-0.5 text-xs bg-destructive text-destructive-foreground rounded">
              Blocking
            </span>
          )}
        </div>
        {check.action && (
          <span className="text-xs text-muted-foreground">
            {expanded ? '▲' : '▼'}
          </span>
        )}
      </div>
      
      {expanded && check.action && (
        <div className="px-3 pb-3 bg-muted/30">
          {check.action.type === 'command' && (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <code className="flex-1 px-3 py-2 text-xs bg-muted text-primary rounded font-mono overflow-x-auto whitespace-pre-wrap">
                  {actionValue}
                </code>
                <CopyButton text={actionValue || ''} />
              </div>
              {docsUrl && (
                <a
                  href={docsUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2 text-primary hover:text-primary/80 text-sm"
                >
                  <ExternalLink className="w-4 h-4" />
                  {docsLabel}
                </a>
              )}
            </div>
          )}
          {check.action.type === 'link' && (
            <a
              href={check.action.value}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 text-primary hover:text-primary/80 text-sm"
            >
              <ExternalLink className="w-4 h-4" />
              {check.action.label}
            </a>
          )}
        </div>
      )}
    </div>
  );
};

// Main component
export const SystemStatus = () => {
  const [data, setData] = useState<PlatformResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errorDetails, setErrorDetails] = useState<string | null>(null);

  const fetchPlatform = async () => {
    try {
      const res = await axios.get('/api/system/platform');
      setData(res.data);
      setError(null);
      setErrorDetails(null);
    } catch (err) {
      const info = describeApiError(err, '/api/system/platform');
      console.error('Failed to fetch platform status:', info);
      setError('Failed to load system status');
      setErrorDetails(`${info.status ? `HTTP ${info.status}` : info.kind}${info.detail ? ` - ${info.detail}` : ''}`);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchPlatform();
    // Refresh every 30 seconds
    const interval = setInterval(fetchPlatform, 30000);
    return () => clearInterval(interval);
  }, []);

  const handleRefresh = () => {
    setRefreshing(true);
    fetchPlatform();
  };

  const isRootless = data?.platform?.docker?.mode === 'rootless';

  if (loading) {
    return (
      <ConfigCard title="System Status" icon={<Server className="w-5 h-5" />}>
        <div className="flex items-center justify-center p-8">
          <RefreshCw className="w-6 h-6 animate-spin text-gray-400" />
        </div>
      </ConfigCard>
    );
  }

  if (error) {
    return (
      <ConfigCard title="System Status" icon={<Server className="w-5 h-5" />}>
        <div className="p-4 text-center text-red-400">
          {error}
          {errorDetails && <div className="mt-1 text-xs text-muted-foreground break-words">{errorDetails}</div>}
          <button 
            onClick={handleRefresh}
            className="ml-2 text-blue-400 hover:text-blue-300"
          >
            Retry
          </button>
        </div>
      </ConfigCard>
    );
  }

  if (!data) return null;

  const { platform, checks, summary } = data;

  return (
    <ConfigCard 
      title="System Status" 
      icon={<Server className="w-5 h-5" />}
      action={
        <div className="flex items-center gap-2">
          <Link
            to="/updates"
            className="px-2 py-1 text-xs rounded-md border border-border hover:bg-accent transition-colors"
            title="Updates"
          >
            Updates
          </Link>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="p-1.5 hover:bg-accent rounded-lg transition-colors"
            title="Refresh"
          >
            <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
        </div>
      }
    >
      {/* Summary Banner */}
      <div className={`px-4 py-3 ${summary.ready ? 'bg-primary/5' : 'bg-destructive/10'} border-b border-border`}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            {summary.ready ? (
              <CheckCircle2 className="w-5 h-5 text-primary" />
            ) : (
              <XCircle className="w-5 h-5 text-destructive" />
            )}
            <span className={`font-medium ${summary.ready ? 'text-foreground' : 'text-destructive'}`}>
              {summary.ready ? 'System Ready' : 'Action Required'}
            </span>
          </div>
          <div className="flex gap-4 text-sm text-muted-foreground">
            <span>{summary.passed} passed</span>
            {summary.warnings > 0 && <span className="text-yellow-500">{summary.warnings} warnings</span>}
            {summary.errors > 0 && <span className="text-destructive">{summary.errors} errors</span>}
          </div>
        </div>
      </div>

      {/* Platform Info Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 p-4 border-b border-border">
        <div className="flex items-center gap-2">
          <Globe className="w-4 h-4 text-muted-foreground" />
          <div>
            <div className="text-xs text-muted-foreground">OS</div>
            <div className="text-sm text-foreground">
              {platform.os.id} {platform.os.version}
              {platform.os.is_eol && <span className="ml-1 text-yellow-500">(EOL)</span>}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Tag className="w-4 h-4 text-muted-foreground" />
          <div>
            <div className="text-xs text-muted-foreground">AAVA</div>
            <div className="text-sm text-foreground" title={platform.project?.source ? `source: ${platform.project.source}` : undefined}>
              {platform.project?.version || 'Unknown'}
            </div>
            <div className="text-xs text-muted-foreground">Update status: not checked</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Box className="w-4 h-4 text-muted-foreground" />
          <div>
            <div className="text-xs text-muted-foreground">Docker</div>
            <div className="text-sm text-foreground">
              {platform.docker.version || 'Not installed'}
              {platform.docker.mode === 'rootless' && <span className="ml-1 text-primary">(rootless)</span>}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <HardDrive className="w-4 h-4 text-muted-foreground" />
          <div>
            <div className="text-xs text-muted-foreground">Compose</div>
            <div className="text-sm text-foreground">
              {platform.compose.version || 'Not installed'}
            </div>
          </div>
        </div>
        {platform.selinux?.present && (
          <div className="flex items-center gap-2">
            <Shield className="w-4 h-4 text-muted-foreground" />
            <div>
              <div className="text-xs text-muted-foreground">SELinux</div>
              <div className="text-sm text-foreground capitalize">
                {platform.selinux.mode || 'disabled'}
              </div>
            </div>
          </div>
        )}
        {platform.asterisk?.detected && (
          <div className="flex items-center gap-2">
            <Server className="w-4 h-4 text-muted-foreground" />
            <div>
              <div className="text-xs text-muted-foreground">Asterisk</div>
              <div className="text-sm text-foreground">
                {platform.asterisk.version || 'Detected'}
                {platform.asterisk.freepbx?.detected && (
                  <span className="ml-1 text-primary">(FreePBX)</span>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Checks List */}
      <div className="divide-y divide-border">
        {checks.map((check) => (
          <CheckRow key={check.id} check={check} isRootless={isRootless} />
        ))}
      </div>
    </ConfigCard>
  );
};

export default SystemStatus;
