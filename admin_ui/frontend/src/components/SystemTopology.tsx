import { useState, useEffect, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Phone, Cpu, Server, Mic, MessageSquare, Volume2, Zap, Radio, CheckCircle2, XCircle, Layers, Loader2, AlertTriangle } from 'lucide-react';
import axios from 'axios';
import yaml from 'js-yaml';
import { FullscreenPanel } from './ui/FullscreenPanel';
import { isFullAgentProvider } from '../utils/providerNaming';
import { deriveTopologyHealth, type TopologyIssue, type TopologyWarning } from '../utils/topologyHealth';
import type { LiveStatusSnapshot } from '../hooks/useLiveStatus';

interface CallState {
  call_id: string;
  started_at: Date;
  provider?: string;
  pipeline?: string;
  state: 'arriving' | 'connected' | 'processing';
}

/** Per-provider readiness state — kept consistent with the ARI / AI Engine /
 *  Local AI tri-state so the dashboard never shows "not ready" red until
 *  we're sure (two consecutive failed reads). */
type ProviderReadyState = 'unknown' | 'ready' | 'not_ready';

interface ProviderConfig {
  name: string;
  displayName: string;
  subtitle: string;
  kind: string;
  enabled: boolean;
  // NOTE: provider readiness is derived from state.providerReady at render
  // time rather than stored on the config snapshot. This way, the 5-second
  // health poll updates dot colour immediately without waiting for the
  // next 10-second config refetch.
}

interface PipelineConfig {
  name: string;
  stt?: string;
  llm?: string;
  tts?: string;
}

interface LocalAIModels {
  stt?: { backend: string; loaded: boolean; path?: string; display?: string };
  llm?: { loaded: boolean; path?: string; display?: string };
  tts?: { backend: string; loaded: boolean; path?: string; display?: string };
}

interface TopologyState {
  aiEngineStatus: 'connected' | 'error' | 'unknown';
  aiEngineDegraded: boolean;
  // `null` = haven't checked yet (initial render); `true`/`false` = the most
  // recent confirmed state from /api/system/health. Distinguishing "unknown"
  // from "false" prevents the dashboard from asserting "ARI Disconnected"
  // in red during the brief window between mount and first fetch resolving.
  ariConnected: boolean | null;
  asteriskChannels: number;  // Pre-stasis + in-stasis calls (for Asterisk PBX indicator)
  localAIStatus: 'connected' | 'error' | 'unknown';
  localAIModels: LocalAIModels | null;
  providerHealth: Record<string, { ready: boolean; reason?: string }>;  // From health endpoint
  // Per-provider tri-state ready (derived from providerHealth with 2-strike
  // debounce). Indexed by provider key (YAML name).
  providerReady: Record<string, ProviderReadyState>;
  configuredProviders: ProviderConfig[];
  configuredPipelines: PipelineConfig[];
  defaultProvider: string | null;
  defaultPipeline: string | null;
  activePipeline: string | null;
  activeCalls: Map<string, CallState>;
}

interface SystemTopologyProps {
  liveStatusEnabled?: boolean;
  liveStatusSnapshot?: LiveStatusSnapshot | null;
}

/**
 * Derive a canonical "kind" string for display + DISPLAY_NAMES lookup.
 *
 * The previous local copy of this used a FULL_AGENT_PROVIDERS allowlist that
 * incorrectly mapped modular `local_stt` / `local_llm` / `local_tts` entries
 * (each with `type: 'local'` and a single capability) onto the 'local'
 * canonical kind, causing them to be misclassified as full agents on the
 * dashboard. The is-full-agent classification now defers to the shared
 * `isFullAgentProvider` utility (which checks capability count); this helper
 * only resolves a display kind.
 */
const getProviderKind = (name: string, config: any): string => {
  const type = typeof config?.type === 'string' ? config.type.toLowerCase() : '';
  if (!type || type === 'full') return name.toLowerCase();
  return type;
};

// Provider display name mapping
const PROVIDER_DISPLAY_NAMES: Record<string, string> = {
  'openai_realtime': 'OpenAI',
  'google_live': 'Google',
  'deepgram': 'Deepgram',
  'elevenlabs_agent': 'ElevenLabs',
};

// I6 — exponential polling backoff. A down backend used to be hammered at the
// fixed cadence (sessions 2s / health 5s / config 10s). On error each loop
// doubles its delay up to a cap; the next success resets to the base interval.
const POLL_BACKOFF_CAP_MS = 30000;
const nextPollDelay = (base: number, ok: boolean, current: number): number =>
  ok ? base : Math.min(current * 2, POLL_BACKOFF_CAP_MS);

export const SystemTopology = ({ liveStatusEnabled = true, liveStatusSnapshot = null }: SystemTopologyProps) => {
  const [state, setState] = useState<TopologyState>({
    aiEngineStatus: 'unknown',
    aiEngineDegraded: false,
    ariConnected: null,
    asteriskChannels: 0,
    localAIStatus: 'unknown',
    localAIModels: null,
    providerHealth: {},
    providerReady: {},
    configuredProviders: [],
    configuredPipelines: [],
    defaultProvider: null,
    defaultPipeline: null,
    activePipeline: null,
    activeCalls: new Map(),
  });
  const [loading, setLoading] = useState(true);
  const [showIssueDetails, setShowIssueDetails] = useState(false);
  const navigate = useNavigate();
  // Per-provider failure streak (cross-render) for the 2-strike debounce.
  // Kept in a ref so updates don't trigger re-renders; the debounced
  // state lands in `state.providerReady` which IS reactive.
  const providerStreaks = useRef<Map<string, number>>(new Map());
  const useLiveStatusSource = liveStatusEnabled && liveStatusSnapshot != null;

  useEffect(() => {
    if (!liveStatusSnapshot) return;

    const aiEngine = liveStatusSnapshot.components.ai_engine;
    const localAI = liveStatusSnapshot.components.local_ai_server;
    const sessions = liveStatusSnapshot.components.sessions;
    const asterisk = liveStatusSnapshot.components.asterisk;

    const isConnectedComponent = (component: typeof aiEngine | undefined): boolean =>
      component?.state === 'ready' || component?.state === 'degraded';

    const toTriState = (success: boolean): 'connected' | 'error' =>
      success ? 'connected' : 'error';

    const providerHealthData = aiEngine?.details?.providers || {};
    const sessionRows = Array.isArray(sessions?.details?.sessions)
      ? sessions.details.sessions
      : [];
    const calls = new Map<string, CallState>();
    for (const session of sessionRows) {
      if (!session?.call_id) continue;
      calls.set(session.call_id, {
        call_id: session.call_id,
        started_at: session.started_at ? new Date(session.started_at) : new Date(),
        provider: session.provider,
        pipeline: session.pipeline,
        state: session.conversation_state === 'greeting' ? 'arriving' : 'connected',
      });
    }

    setState(prev => {
      const nextProviderReady: Record<string, ProviderReadyState> = { ...prev.providerReady };
      for (const [name, info] of Object.entries(providerHealthData)) {
        const isReady = Boolean((info as any)?.ready);
        const streak = providerStreaks.current.get(name) || 0;
        const newStreak = isReady ? 0 : streak + 1;
        providerStreaks.current.set(name, newStreak);
        nextProviderReady[name] = isReady
          ? 'ready'
          : newStreak >= 2
            ? 'not_ready'
            : prev.providerReady[name] === 'ready'
              ? 'ready'
              : 'unknown';
      }

      const ariConnected =
        aiEngine?.details?.ari_connected ??
        aiEngine?.details?.asterisk?.connected ??
        asterisk?.details?.live?.ari_reachable ??
        prev.ariConnected;

      return {
        ...prev,
        aiEngineStatus: aiEngine ? toTriState(isConnectedComponent(aiEngine)) : prev.aiEngineStatus,
        aiEngineDegraded: aiEngine ? aiEngine.state === 'degraded' : prev.aiEngineDegraded,
        ariConnected: typeof ariConnected === 'boolean' ? ariConnected : prev.ariConnected,
        asteriskChannels: aiEngine?.details?.asterisk_channels ?? prev.asteriskChannels,
        localAIStatus: localAI ? toTriState(isConnectedComponent(localAI)) : prev.localAIStatus,
        localAIModels: localAI?.details?.models ?? prev.localAIModels,
        providerHealth: providerHealthData,
        providerReady: nextProviderReady,
        activeCalls: calls,
      };
    });
  }, [liveStatusSnapshot]);

  // Fetch health status with three-state + two-strike debounce per indicator.
  //
  // Three states: `unknown` (haven't reached a confirmed answer) → grey
  // "Checking…", `connected/true` → green, `error/false` → red. The unknown
  // state is sticky: a SINGLE negative read keeps the state at unknown
  // (still grey). Only the SECOND consecutive negative read flips to red.
  //
  // Why: after a docker compose recreate, the AI engine warms up over ~5–10s
  // (Asterisk reconnecting ARI, model loaders coming online). During that
  // window the backend legitimately reports `ari_connected=false`,
  // `local_ai_server.status=error`, etc. — but the user sees that as red
  // alarm spam that flips green on its own. By holding at "Checking…" for
  // one polling cycle, the dashboard only goes red when the issue persists
  // beyond a normal warmup. Genuinely broken systems take ~10s (2 polls) to
  // show red, which is the right trade.
  //
  // Any positive read clears the streak counter and immediately shows green.
  useEffect(() => {
    if (useLiveStatusSource) return;

    let ariFailStreak = 0;
    let aiEngineFailStreak = 0;
    let localAIFailStreak = 0;
    let mounted = true;

    // Map a raw boolean/error reading + the current state to a new value.
    // `prev` is the existing state ('unknown' | 'connected' | 'error'). On a
    // success read we go straight to connected. On a failure we hold at
    // unknown for the first miss, then flip to error on the second.
    const debouncedTri = (
      success: boolean,
      streak: number,
      prev: 'unknown' | 'connected' | 'error',
    ): 'unknown' | 'connected' | 'error' => {
      if (success) return 'connected';
      if (streak >= 2) return 'error';
      // Hold whatever the previous confirmed state was. If we were previously
      // 'connected', stay connected through one bad read (transient blip).
      // If we were 'unknown', stay unknown (still warming up).
      return prev === 'connected' ? 'connected' : 'unknown';
    };
    const debouncedBool = (
      success: boolean,
      streak: number,
      prev: boolean | null,
    ): boolean | null => {
      if (success) return true;
      if (streak >= 2) return false;
      return prev === true ? true : null;
    };

    const fetchHealth = async (): Promise<boolean> => {
      try {
        const res = await axios.get('/api/system/health');
        if (!mounted) return true;
        const aiEngineDetails = res.data.ai_engine?.details || {};
        const ariReported: boolean = Boolean(
          aiEngineDetails.ari_connected ?? aiEngineDetails.asterisk?.connected ?? false,
        );
        const aiEngineConnected: boolean = res.data.ai_engine?.status === 'connected';
        const localAIConnected: boolean = res.data.local_ai_server?.status === 'connected';

        ariFailStreak = ariReported ? 0 : ariFailStreak + 1;
        aiEngineFailStreak = aiEngineConnected ? 0 : aiEngineFailStreak + 1;
        localAIFailStreak = localAIConnected ? 0 : localAIFailStreak + 1;

        const providerHealthData = aiEngineDetails.providers || {};

        setState(prev => {
          // Same tri-state + 2-strike debounce, per provider, computed
          // against the LATEST prev (this is a functional setState — `prev`
          // here is fresh even if multiple polls landed close together).
          // Providers configured but missing from the health response keep
          // their previous debounced state (the for-loop only touches keys
          // present in the response).
          const nextProviderReady: Record<string, ProviderReadyState> = { ...prev.providerReady };
          for (const [name, info] of Object.entries(providerHealthData)) {
            const isReady = Boolean((info as any)?.ready);
            const streak = providerStreaks.current.get(name) || 0;
            const newStreak = isReady ? 0 : streak + 1;
            providerStreaks.current.set(name, newStreak);
            nextProviderReady[name] = isReady
              ? 'ready'
              : newStreak >= 2
                ? 'not_ready'
                : prev.providerReady[name] === 'ready'
                  ? 'ready'
                  : 'unknown';
          }
          // Keep the last known local AI model details on transient probe
          // failures. When the WebSocket probe to local_ai_server times out
          // the backend returns `local_ai_server.status: 'error'` with
          // `details: {error: "..."}` — no `models` field. Replacing the
          // models with `null` then flips the MODELS section to "Not
          // loaded" placeholders even though the server is healthy. Use ??
          // (not ||) so genuine empty/cleared model state still flows
          // through, but missing-data responses keep the previous snapshot.
          const newLocalAIModels = res.data.local_ai_server?.details?.models ?? prev.localAIModels;
          return {
            ...prev,
            aiEngineStatus: debouncedTri(aiEngineConnected, aiEngineFailStreak, prev.aiEngineStatus),
            ariConnected: debouncedBool(ariReported, ariFailStreak, prev.ariConnected),
            asteriskChannels: aiEngineDetails.asterisk_channels ?? 0,
            localAIStatus: debouncedTri(localAIConnected, localAIFailStreak, prev.localAIStatus),
            localAIModels: newLocalAIModels,
            providerHealth: providerHealthData,
            providerReady: nextProviderReady,
          };
        });
        // Back off when the backend is up (200) but reports the engine unreachable
        // (`ai_engine.status: 'error'`): that outage is exactly when the expensive
        // per-poll dependency probes keep running, and there's no fresh status to fetch.
        return res.data?.ai_engine?.status !== 'error';
      } catch {
        if (!mounted) return false;
        // Full request failure (network, 401, etc.) counts as a miss for all
        // four debounced indicators (ARI, AI Engine, Local AI, every known
        // provider). Two consecutive total failures flip each indicator red.
        ariFailStreak += 1;
        aiEngineFailStreak += 1;
        localAIFailStreak += 1;
        setState(prev => {
          const nextProviderReady: Record<string, ProviderReadyState> = { ...prev.providerReady };
          for (const name of Object.keys(prev.providerReady)) {
            const streak = (providerStreaks.current.get(name) || 0) + 1;
            providerStreaks.current.set(name, streak);
            nextProviderReady[name] =
              streak >= 2
                ? 'not_ready'
                : prev.providerReady[name] === 'ready'
                  ? 'ready'
                  : 'unknown';
          }
          return {
            ...prev,
            aiEngineStatus: debouncedTri(false, aiEngineFailStreak, prev.aiEngineStatus),
            ariConnected: debouncedBool(false, ariFailStreak, prev.ariConnected),
            asteriskChannels: 0,
            localAIStatus: debouncedTri(false, localAIFailStreak, prev.localAIStatus),
            providerReady: nextProviderReady,
          };
        });
        return false;
      }
    };
    // Self-scheduling poll with error backoff (I6): base 5s, doubling on
    // request failure up to the cap, resetting to 5s on the next success.
    const BASE_MS = 5000;
    let timer: ReturnType<typeof setTimeout>;
    let delay = BASE_MS;
    const schedule = () => {
      timer = setTimeout(async () => {
        const ok = await fetchHealth();
        if (!mounted) return;
        delay = nextPollDelay(BASE_MS, ok, delay);
        schedule();
      }, delay);
    };
    fetchHealth().then(ok => {
      if (!mounted) return;
      delay = nextPollDelay(BASE_MS, ok, delay);
      schedule();
    });
    return () => {
      mounted = false;
      clearTimeout(timer);
    };
  }, [useLiveStatusSource]);

  // Fetch config (providers, pipelines)
  useEffect(() => {
    let mounted = true;
    const fetchConfig = async (): Promise<boolean> => {
      try {
        const res = await axios.get('/api/config/yaml');
        const parsed = yaml.load(res.data.content) as any;

        // Extract only full agent providers (not modular pipeline components)
        const providers: ProviderConfig[] = [];
        if (parsed?.providers && typeof parsed.providers === 'object') {
          for (const [name, config] of Object.entries(parsed.providers)) {
            // Only include full agent providers. The shared utility correctly
            // excludes modular slots like local_stt / local_llm / local_tts
            // (each with `type: 'local'` and a single capability) — its
            // signature is `(provider, key)`, with the key used for canonical
            // legacy-form detection.
            if (isFullAgentProvider(config, name)) {
              const cfg = config as any;
              const kind = getProviderKind(name, cfg);
              // Check if enabled - defaults to true if not specified
              const enabled = cfg?.enabled !== false;
              providers.push({
                name,
                displayName: cfg?.display_name || cfg?.customer || PROVIDER_DISPLAY_NAMES[kind] || name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
                subtitle: `${name} · ${kind}${cfg?.customer ? ` · ${cfg.customer}` : ''}`,
                kind,
                enabled,
              });
            }
          }
        }

        // Extract pipelines - note: stt/llm/tts are direct string properties, not nested
        const pipelines: PipelineConfig[] = [];
        if (parsed?.pipelines && typeof parsed.pipelines === 'object') {
          for (const [name, config] of Object.entries(parsed.pipelines)) {
            const cfg = config as any;
            pipelines.push({
              name,
              stt: typeof cfg?.stt === 'string' ? cfg.stt : cfg?.stt?.provider,
              llm: typeof cfg?.llm === 'string' ? cfg.llm : cfg?.llm?.provider,
              tts: typeof cfg?.tts === 'string' ? cfg.tts : cfg?.tts?.provider,
            });
          }
        }

        setState(prev => {
          // configuredProviders no longer carries a `ready` field — it's
          // derived at render time from prev.providerReady so health-poll
          // updates (5s cadence) propagate without waiting for the next
          // config-fetch (10s cadence).
          const mergedProviders = providers;
          const contextDefaultProvider =
            typeof parsed?.contexts?.default?.provider === 'string'
              ? parsed.contexts.default.provider
              : null;
          const contextDefaultPipeline =
            typeof parsed?.contexts?.default?.pipeline === 'string'
              ? parsed.contexts.default.pipeline
              : null;
          const legacyDefaultProvider =
            typeof parsed?.default_provider === 'string' ? parsed.default_provider : null;
          const legacyActivePipeline =
            typeof parsed?.active_pipeline === 'string' ? parsed.active_pipeline : null;
          return {
            ...prev,
            configuredProviders: mergedProviders,
            configuredPipelines: pipelines,
            // Prefer contexts.default.provider (actual routing), fall back to legacy root default_provider.
            defaultProvider: contextDefaultProvider || legacyDefaultProvider,
            defaultPipeline: contextDefaultPipeline,
            activePipeline: legacyActivePipeline,
          };
        });
        setLoading(false);
        return true;
      } catch {
        setLoading(false);
        return false;
      }
    };
    // Self-scheduling poll with error backoff (I6): base 10s, doubling on
    // failure up to the cap, resetting on the next success.
    const BASE_MS = 10000;
    let timer: ReturnType<typeof setTimeout>;
    let delay = BASE_MS;
    const schedule = () => {
      timer = setTimeout(async () => {
        const ok = await fetchConfig();
        if (!mounted) return;
        delay = nextPollDelay(BASE_MS, ok, delay);
        schedule();
      }, delay);
    };
    fetchConfig().then(ok => {
      if (!mounted) return;
      delay = nextPollDelay(BASE_MS, ok, delay);
      schedule();
    });
    return () => {
      mounted = false;
      clearTimeout(timer);
    };
  }, []);

  // Poll for active calls from sessions API (more reliable than log parsing)
  useEffect(() => {
    if (useLiveStatusSource) return;

    let mounted = true;
    const fetchActiveSessions = async (): Promise<boolean> => {
      try {
        const res = await axios.get('/api/system/sessions');
        const sessions = res.data.sessions || [];

        const calls = new Map<string, CallState>();
        for (const session of sessions) {
          calls.set(session.call_id, {
            call_id: session.call_id,
            // NOTE: latent — `started_at` is reset to now() on every 2s poll, so
            // it can't drive a real per-call duration yet. Harmless today (no
            // consumer reads it); kept here so the field exists when wired up.
            started_at: new Date(),
            provider: session.provider,
            pipeline: session.pipeline,
            state: session.conversation_state === 'greeting' ? 'arriving' : 'connected',
          });
        }

        setState(prev => ({ ...prev, activeCalls: calls }));
        // The sessions proxy returns 200 with `reachable: false` and an empty list when
        // the engine is unreachable; treat that as a failed poll so the 2s loop backs off
        // instead of hammering /sessions/stats + the Docker fallback every 2s.
        return res.data?.reachable !== false;
      } catch (err) {
        console.error('Failed to fetch active sessions', err);
        return false;
      }
    };

    // Self-scheduling poll with error backoff (I6): base 2s, doubling on
    // failure up to the cap, resetting on the next success.
    const BASE_MS = 2000;
    let timer: ReturnType<typeof setTimeout>;
    let delay = BASE_MS;
    const schedule = () => {
      timer = setTimeout(async () => {
        const ok = await fetchActiveSessions();
        if (!mounted) return;
        delay = nextPollDelay(BASE_MS, ok, delay);
        schedule();
      }, delay);
    };
    fetchActiveSessions().then(ok => {
      if (!mounted) return;
      delay = nextPollDelay(BASE_MS, ok, delay);
      schedule();
    });
    return () => {
      mounted = false;
      clearTimeout(timer);
    };
  }, [useLiveStatusSource]);

  // Derive active providers/pipelines from calls
  const activeProviders = useMemo(() => {
    const counts = new Map<string, number>();
    for (const call of state.activeCalls.values()) {
      if (call.provider) {
        counts.set(call.provider, (counts.get(call.provider) || 0) + 1);
      }
    }
    return counts;
  }, [state.activeCalls]);

  const activePipelines = useMemo(() => {
    const counts = new Map<string, number>();
    for (const call of state.activeCalls.values()) {
      if (call.pipeline) {
        counts.set(call.pipeline, (counts.get(call.pipeline) || 0) + 1);
      }
    }
    return counts;
  }, [state.activeCalls]);

  const totalActiveCalls = state.activeCalls.size;
  const hasActiveCalls = totalActiveCalls > 0;
  const hasAsteriskChannels = state.asteriskChannels > 0;  // Pre-stasis + in-stasis

  /**
   * Group configured full-agent providers by `kind` so multi-instance
   * deployments (e.g. `grok` + `acme_grok` + `globex_grok`) collapse into a
   * single card with one row per instance — instead of N flat cards down the
   * page. Singletons render the same shape with a single row, so the visual
   * is consistent for both 1-tenant and multi-tenant configs.
   *
   * Ordering: stable insertion order from the YAML, kinds appear in the
   * order their first instance is encountered.
   */
  const providerGroups = useMemo(() => {
    const groups: Array<{ kind: string; kindLabel: string; providers: ProviderConfig[]; hasActive: boolean }> = [];
    const byKind = new Map<string, number>();
    for (const provider of state.configuredProviders) {
      const idx = byKind.get(provider.kind);
      if (idx === undefined) {
        byKind.set(provider.kind, groups.length);
        const kindLabel = PROVIDER_DISPLAY_NAMES[provider.kind]
          || provider.kind.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        groups.push({ kind: provider.kind, kindLabel, providers: [provider], hasActive: false });
      } else {
        groups[idx].providers.push(provider);
      }
    }
    for (const group of groups) {
      group.hasActive = group.providers.some(p => (activeProviders.get(p.name) || 0) > 0);
    }
    return groups;
  }, [state.configuredProviders, activeProviders]);

  // Determine which local models are being used by active pipelines
  const localUsageFromPipelines = useMemo(() => {
    const active = { stt: false, llm: false, tts: false };
    for (const [pipelineName] of activePipelines) {
      const pipeline = state.configuredPipelines.find(p => p.name === pipelineName);
      if (pipeline) {
        // Check if pipeline uses local components
        if (pipeline.stt?.toLowerCase().includes('local')) active.stt = true;
        if (pipeline.llm?.toLowerCase().includes('local')) active.llm = true;
        if (pipeline.tts?.toLowerCase().includes('local')) active.tts = true;
      }
    }
    return active;
  }, [activePipelines, state.configuredPipelines]);

  const localProviderActiveCount = activeProviders.get('local') || 0;
  const isLocalProviderActive = localProviderActiveCount > 0;
  const isLocalAIUsedByPipelines = localUsageFromPipelines.stt || localUsageFromPipelines.llm || localUsageFromPipelines.tts;

  // Local AI can be used either by a full local provider call (provider=local) or by local_* components in pipelines.
  const activeLocalModels = useMemo(() => {
    const active = { ...localUsageFromPipelines };
    if (isLocalProviderActive) {
      // Full-local provider always uses STT+TTS; LLM may be disabled depending on host capabilities.
      active.stt = true;
      active.tts = true;
      if (state.localAIModels?.llm?.loaded) active.llm = true;
    }
    return active;
  }, [localUsageFromPipelines, isLocalProviderActive, state.localAIModels]);

  const isLocalAIActive = isLocalProviderActive || isLocalAIUsedByPipelines;

  const topologyHealth = useMemo(() => deriveTopologyHealth({
    aiEngineStatus: state.aiEngineStatus,
    ariConnected: state.ariConnected,
    localAIStatus: state.localAIStatus,
    configuredProviders: state.configuredProviders.map(provider => ({
      name: provider.name,
      enabled: provider.enabled,
      kind: provider.kind,
    })),
    providerReady: state.providerReady,
    configuredPipelines: state.configuredPipelines,
    defaultProvider: state.defaultProvider,
    defaultPipeline: state.defaultPipeline,
    activePipeline: state.activePipeline,
    activeProviderNames: Array.from(activeProviders.keys()),
    activePipelineNames: Array.from(activePipelines.keys()),
  }), [
    state.aiEngineStatus,
    state.ariConnected,
    state.localAIStatus,
    state.configuredProviders,
    state.providerReady,
    state.configuredPipelines,
    state.defaultProvider,
    state.defaultPipeline,
    state.activePipeline,
    activeProviders,
    activePipelines,
  ]);
  const topologyDetailItems = [
    ...topologyHealth.issues.map(issue => ({ ...issue, severity: 'issue' as const })),
    ...topologyHealth.warnings.map(warning => ({ ...warning, severity: 'warning' as const })),
  ];

  useEffect(() => {
    if (topologyHealth.overallStatus !== 'issue' && topologyHealth.warnings.length === 0) {
      setShowIssueDetails(false);
    }
  }, [topologyHealth.overallStatus, topologyHealth.warnings.length]);

  const issueTargetLabel = (issue: TopologyIssue | TopologyWarning): string => {
    switch (issue.target) {
      case 'env':
        return 'Environment';
      case 'providers':
        return 'Providers';
      case 'models':
        return 'Models';
      default:
        return 'Open';
    }
  };

  const navigateIssueTarget = (issue: TopologyIssue | TopologyWarning) => {
    switch (issue.target) {
      case 'env':
        navigate(issue.key === 'ai_engine' ? '/env#ai-engine' : '/env');
        break;
      case 'providers':
        navigate('/providers');
        break;
      case 'models':
        navigate('/models');
        break;
    }
  };

  const localAIHasRequiredError = topologyHealth.localAIRequired && state.localAIStatus === 'error';
  const localAIHasOptionalWarning = topologyHealth.localAIOptionalUnavailable;
  const localAIIsActiveConnected = isLocalAIActive && state.localAIStatus === 'connected';
  const localAIIsOptionalInactive = !topologyHealth.localAIRequired && state.localAIStatus !== 'connected';

  // Get model display name
  const getModelDisplayName = (model: any, type: string): string => {
    if (!model) return type;
    if (model.display) return model.display;
    if (model.backend) return model.backend.charAt(0).toUpperCase() + model.backend.slice(1);
    return type;
  };

  if (loading) {
    return (
      <div className="rounded-lg border border-border bg-card p-6 mb-6">
        <div className="animate-pulse flex items-center gap-3">
          <div className="h-6 w-6 bg-muted rounded" />
          <div className="h-4 w-48 bg-muted rounded" />
        </div>
      </div>
    );
  }

  return (
    <FullscreenPanel
      className="mb-6"
      titleNode={
        <div className="flex items-center gap-2">
          <Radio className={`w-4 h-4 ${hasActiveCalls ? 'text-green-500 animate-pulse' : 'text-muted-foreground'}`} />
          <span className="text-sm font-medium">Live System Topology</span>
        </div>
      }
      headerRight={
        <div className="flex items-center gap-3 text-xs">
          <div className="flex items-center gap-1">
            <Phone className={`w-3.5 h-3.5 ${hasActiveCalls ? 'text-green-500' : 'text-muted-foreground'}`} />
            <span className={hasActiveCalls ? 'text-green-500 font-medium' : 'text-muted-foreground'}>
              {totalActiveCalls} call{totalActiveCalls !== 1 ? 's' : ''}
            </span>
          </div>
        </div>
      }
    >
      <div>
        {/* === SUMMARY STRIP === */}
        {/* Compact at-a-glance health row: one line tells operators if anything
            is wrong, without scanning the architecture diagram. */}
        {(() => {
          // Only enabled providers contribute to the ratio + health. Counting
          // disabled providers in totalProviders made the headline numbers
          // misleading, and ignoring providerReady let the strip say
          // "All systems healthy" while an enabled provider was not_ready
          // (CodeRabbit major on PR #396).
          const enabledProviders = state.configuredProviders.filter(p => p.enabled);
          const totalProviders = enabledProviders.length;
          const readyProviders = enabledProviders.filter(
            p => state.providerReady[p.name] === 'ready'
          ).length;
          // I9 — derive the denominator from the local components actually
          // present in the health response rather than hardcoding 3. An
          // STT+TTS-only box (no local LLM) reports two components and reads
          // "N/2", instead of a permanent "N/3" that looks like a fault.
          const localComponents = [
            state.localAIModels?.stt,
            state.localAIModels?.llm,
            state.localAIModels?.tts,
          ].filter((m): m is NonNullable<typeof m> => m != null);
          const totalModels = localComponents.length;
          const loadedModels = localComponents.filter(m => m.loaded).length;
          const overallStatus = topologyHealth.overallStatus;
          const statusColor = overallStatus === 'healthy'
            ? 'text-green-500'
            : overallStatus === 'issue'
              ? 'text-red-500'
              : 'text-muted-foreground';
          const statusLabel = overallStatus === 'healthy'
            ? 'All systems healthy'
            : overallStatus === 'issue'
              ? 'Issue detected'
              : 'Checking…';
          const warnings = topologyHealth.warnings;
          return (
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5 mb-3 rounded-lg bg-muted/30 border border-border/50 text-xs">
              <button
                type="button"
                disabled={overallStatus !== 'issue'}
                onClick={() => setShowIssueDetails(open => !open)}
                className={`flex items-center gap-1.5 font-medium ${statusColor} ${overallStatus === 'issue' ? 'cursor-pointer hover:underline' : 'cursor-default'}`}
                aria-expanded={overallStatus === 'issue' ? showIssueDetails : undefined}
                aria-controls={overallStatus === 'issue' ? 'topology-issue-details' : undefined}
              >
                {overallStatus === 'healthy' ? (
                  <CheckCircle2 className="w-3.5 h-3.5" />
                ) : overallStatus === 'issue' ? (
                  <XCircle className="w-3.5 h-3.5" />
                ) : (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                )}
                <span>{statusLabel}</span>
              </button>
              {warnings.length > 0 && (
                <button
                  type="button"
                  onClick={() => setShowIssueDetails(open => !open)}
                  className="flex items-center gap-1.5 font-medium text-amber-500 cursor-pointer hover:underline"
                  aria-expanded={showIssueDetails}
                  aria-controls="topology-issue-details"
                >
                  <AlertTriangle className="w-3.5 h-3.5" />
                  <span>{warnings.length === 1 ? warnings[0].label : `${warnings.length} optional warnings`}</span>
                </button>
              )}
              <div className="flex items-center gap-1.5 text-muted-foreground">
                <Phone className={`w-3.5 h-3.5 ${hasActiveCalls ? 'text-green-500' : ''}`} />
                <span>
                  <span className={hasActiveCalls ? 'text-green-500 font-medium' : 'text-foreground'}>
                    {totalActiveCalls}
                  </span>
                  {' '}call{totalActiveCalls !== 1 ? 's' : ''}
                </span>
              </div>
              <div className="flex items-center gap-1.5 text-muted-foreground">
                <Zap className="w-3.5 h-3.5" />
                <span>
                  <span className="text-foreground font-medium">{readyProviders}</span>
                  /{totalProviders} providers ready
                </span>
              </div>
              {(topologyHealth.localAIRelevant || totalModels > 0) && !topologyHealth.localAIOptionalUnavailable && (
                <div className="flex items-center gap-1.5 text-muted-foreground">
                  <Server className="w-3.5 h-3.5" />
                  <span>
                    <span className="text-foreground font-medium">{loadedModels}</span>
                    /{totalModels} local models loaded
                  </span>
                </div>
              )}
            </div>
          );
        })()}

        {showIssueDetails && (topologyHealth.issues.length > 0 || topologyHealth.warnings.length > 0) && (
          <div
            id="topology-issue-details"
            className={`mb-3 rounded-lg border px-4 py-3 ${topologyHealth.issues.length > 0
              ? 'border-red-500/30 bg-red-500/5'
              : 'border-amber-500/30 bg-amber-500/5'
              }`}
          >
            <div className="space-y-2">
              {topologyDetailItems.map(issue => {
                const isWarning = issue.severity === 'warning';
                return (
                <div key={issue.key} className="flex flex-wrap items-start justify-between gap-3">
                  <div className="flex min-w-0 flex-1 gap-2">
                    {isWarning ? (
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-amber-500" />
                    ) : (
                      <XCircle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-red-500" />
                    )}
                    <div className="min-w-0">
                      <div className="text-xs font-medium text-foreground">{issue.label}</div>
                      <div className="mt-0.5 text-[11px] text-muted-foreground">{issue.detail}</div>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => navigateIssueTarget(issue)}
                    className="rounded-md border border-border bg-background px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:text-primary"
                  >
                    {issueTargetLabel(issue)}
                  </button>
                </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Grid Layout — col 5 (providers/models) now flex-grows so it can
            absorb the canvas width that used to be wasted to the left of
            Asterisk. Cols 1-4 stay fixed-width so the SVG arrow geometry
            (which references x=80 for col 1 center, x=288 for col 3 center)
            stays exactly aligned with the actual columns. */}
        <div className="relative grid grid-cols-[160px_48px_160px_48px_minmax(420px,1fr)] gap-y-4 items-center py-4">

          {/* === ROW 1: Asterisk → AI Engine → Providers === */}

          {/* Asterisk PBX */}
          {/* self-stretch lets the card grow to match the row height — which
              is now driven by the Providers grid (col 5). As more providers
              are configured, the Providers grid gets taller and Asterisk +
              AI Engine grow alongside it. justify-center keeps the icon /
              label / status pills visually centered inside the now-taller card. */}
          <div
            onClick={() => navigate('/env')}
            title="Go to Asterisk Settings →"
            className={`self-stretch relative p-4 rounded-xl border backdrop-blur-sm transition-all duration-300 cursor-pointer hover:-translate-y-1 ${hasAsteriskChannels
              ? 'border-green-500/50 bg-green-500/10 shadow-[0_8px_30px_rgb(34,197,94,0.15)] ring-1 ring-green-500/50'
              : 'border-border/60 bg-card/60 hover:bg-card/80 hover:border-primary/40 shadow-sm'
              }`}>
            {hasAsteriskChannels && (
              <div className="absolute inset-0 rounded-lg border-2 border-green-500 animate-ping opacity-20" />
            )}
            <div className="flex flex-col items-center justify-center gap-2 h-full">
              <Phone className={`w-8 h-8 ${hasAsteriskChannels ? 'text-green-500' : 'text-muted-foreground'}`} />
              <div className="text-center">
                <div className={`font-semibold ${hasAsteriskChannels ? 'text-green-500' : 'text-foreground'}`}>Asterisk</div>
                <div className="text-xs text-muted-foreground">PBX</div>
              </div>
              <div className="w-full pt-2 mt-2 border-t border-border/50 space-y-1">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground">ARI</span>
                  {state.ariConnected === null ? (
                    <span className="flex items-center gap-1 text-muted-foreground">
                      <Loader2 className="w-3 h-3 animate-spin" /> Checking…
                    </span>
                  ) : state.ariConnected ? (
                    <span className="flex items-center gap-1 text-green-500">
                      <CheckCircle2 className="w-3 h-3" /> Connected
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-red-500">
                      <XCircle className="w-3 h-3" /> Disconnected
                    </span>
                  )}
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground">Calls</span>
                  <span className={`font-medium ${hasActiveCalls ? 'text-green-500' : 'text-foreground'}`}>
                    {totalActiveCalls}
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* Arrow */}
          <div className="flex items-center justify-center self-center w-full">
            <svg
              role="img"
              aria-label={`Asterisk to AI Engine flow: ${hasActiveCalls ? 'active' : 'idle'}`}
              className="w-full h-4 overflow-visible"
              viewBox="0 0 48 16"
              preserveAspectRatio="none"
            >
              <path
                d="M 0 8 L 40 8"
                stroke={hasActiveCalls ? '#22c55e' : '#e5e7eb'}
                strokeWidth="2"
                className={hasActiveCalls ? 'animate-flow-dash' : ''}
                strokeDasharray="4 4"
              />
              <polygon points="40,3 48,8 40,13" fill={hasActiveCalls ? '#22c55e' : '#e5e7eb'} />
            </svg>
          </div>

          {/* AI Engine Core */}
          {/* self-stretch + inner justify-center: same treatment as Asterisk so
              AI Engine grows with the Providers grid height. */}
          <div
            onClick={() => navigate('/env#ai-engine')}
            title="Go to AI Engine Settings →"
            className={`self-stretch relative p-4 rounded-xl border backdrop-blur-sm transition-all duration-300 cursor-pointer hover:-translate-y-1 ${state.aiEngineStatus === 'error'
              ? 'border-red-500/50 bg-red-500/10 ring-1 ring-red-500/50'
              : state.aiEngineDegraded
                ? 'border-amber-500/50 bg-amber-500/10 ring-1 ring-amber-500/30'
              : hasActiveCalls && state.aiEngineStatus === 'connected'
                ? 'border-green-500/50 bg-green-500/10 shadow-[0_8px_30px_rgb(34,197,94,0.15)] ring-1 ring-green-500/50'
                : 'border-border/60 bg-card/60 hover:bg-card/80 hover:border-primary/40 shadow-sm'
              }`}>
            {hasActiveCalls && state.aiEngineStatus === 'connected' && (
              <div className="absolute inset-0 rounded-lg border-2 border-green-500 animate-ping opacity-20" />
            )}
            <div className="flex flex-col items-center justify-center gap-2 h-full">
              <Cpu className={`w-8 h-8 ${state.aiEngineStatus === 'error' ? 'text-red-500' : state.aiEngineDegraded ? 'text-amber-500' : hasActiveCalls && state.aiEngineStatus === 'connected' ? 'text-green-500' : 'text-muted-foreground'
                }`} />
              <div className="text-center">
                <div className={`font-semibold ${state.aiEngineStatus === 'error' ? 'text-red-500' : state.aiEngineDegraded ? 'text-amber-500' : hasActiveCalls && state.aiEngineStatus === 'connected' ? 'text-green-500' : 'text-foreground'
                  }`}>AI Engine</div>
                <div className="text-xs text-muted-foreground">Core</div>
              </div>
              <div className="w-full pt-2 mt-2 border-t border-border/50">
                <div className="flex items-center justify-center text-xs">
                  {state.aiEngineStatus === 'unknown' ? (
                    <span className="flex items-center gap-1 text-muted-foreground">
                      <Loader2 className="w-3 h-3 animate-spin" /> Checking…
                    </span>
                  ) : state.aiEngineStatus === 'connected' && state.aiEngineDegraded ? (
                    <span className="flex items-center gap-1 text-amber-500">
                      <AlertTriangle className="w-3 h-3" /> Degraded
                    </span>
                  ) : state.aiEngineStatus === 'connected' ? (
                    <span className="flex items-center gap-1 text-green-500">
                      <CheckCircle2 className="w-3 h-3" /> Healthy
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-red-500">
                      <XCircle className="w-3 h-3" /> Error
                    </span>
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* Arrow */}
          <div className="flex items-center justify-center self-center w-full">
            <svg
              role="img"
              aria-label={`AI Engine to Providers flow: ${hasActiveCalls ? 'active' : 'idle'}`}
              className="w-full h-4 overflow-visible"
              viewBox="0 0 48 16"
              preserveAspectRatio="none"
            >
              <path
                d="M 0 8 L 40 8"
                stroke={hasActiveCalls ? '#22c55e' : '#e5e7eb'}
                strokeWidth="2"
                className={hasActiveCalls ? 'animate-flow-dash' : ''}
                strokeDasharray="4 4"
              />
              <polygon points="40,3 48,8 40,13" fill={hasActiveCalls ? '#22c55e' : '#e5e7eb'} />
            </svg>
          </div>

          {/* Providers (Full Agents Only) */}
          <div>
            <div className="flex justify-center">
              <div
                onClick={() => navigate('/providers')}
                title="Go to Providers →"
                className="inline-block px-3 py-1 mx-auto rounded-full bg-muted/40 border border-border/50 text-[10px] text-muted-foreground uppercase tracking-wider mb-3 text-center cursor-pointer hover:text-primary transition-colors"
              >Providers</div>
            </div>
            {/* Responsive provider grid: 1 col on narrow, 2 on tablet, 3 on
                desktop. Used to be a single column = ~540px of vertical scroll
                for 6 kinds; now ~180px in 2 rows of 3 on a desktop viewport.
                items-start so singleton cards (Deepgram, Google, OpenAI…) don't
                stretch to match a multi-instance card (e.g. Grok ×2) in the
                same row — each card keeps its natural height. */}
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2 items-start">
              {providerGroups.length === 0 ? (
                <div className="col-span-full p-3 rounded-lg border border-dashed border-border text-xs text-muted-foreground text-center">
                  No agents
                </div>
              ) : (
                providerGroups.map(group => {
                  const groupClass = group.hasActive
                    ? 'border-green-500/50 bg-green-500/10 shadow-[0_4px_15px_rgb(34,197,94,0.1)] ring-1 ring-green-500/30'
                    : 'border-border/60 bg-card/60 shadow-sm';
                  return (
                    <div key={group.kind} className={`rounded-xl border backdrop-blur-sm transition-all duration-300 ${groupClass}`}>
                      {/* Group header: provider kind + multi-instance badge */}
                      <div className="flex items-center justify-between gap-2 px-3 pt-2 pb-1">
                        <div className="flex items-center gap-2 min-w-0">
                          <Zap className={`w-3.5 h-3.5 flex-shrink-0 ${group.hasActive ? 'text-green-500' : 'text-muted-foreground'}`} />
                          <span className="text-xs font-semibold text-foreground truncate">{group.kindLabel}</span>
                        </div>
                        {group.providers.length > 1 && (
                          <span
                            className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground flex-shrink-0"
                            title={`${group.providers.length} configured instances of this provider kind`}
                          >
                            ×{group.providers.length}
                          </span>
                        )}
                      </div>
                      {/* Instance rows: one per configured provider of this kind */}
                      <div className="px-2 pb-2 pt-1 space-y-1">
                        {group.providers.map(provider => {
                          const activeCount = activeProviders.get(provider.name) || 0;
                          const isActive = activeCount > 0;
                          const isDefault = provider.name === state.defaultProvider;
                          // Derive ready state at render time from the debounced
                          // map. Default to 'unknown' (Checking…) for providers
                          // we haven't probed yet — same UX as ARI / AI Engine.
                          const readyState: ProviderReadyState =
                            state.providerReady[provider.name] ?? 'unknown';
                          const dotColor = !provider.enabled
                            ? 'bg-orange-500'
                            : readyState === 'ready'
                              ? 'bg-green-500'
                              : readyState === 'not_ready'
                                ? 'bg-red-500'
                                : 'bg-muted-foreground/50';
                          // Pulse the dot while we're in the Checking… state
                          // so it's visually distinct from a static colored dot.
                          const dotAnim = readyState === 'unknown' && provider.enabled
                            ? 'animate-pulse'
                            : '';
                          const dotTitle = !provider.enabled
                            ? 'Disabled'
                            : readyState === 'ready'
                              ? 'Ready'
                              : readyState === 'not_ready'
                                ? 'Not ready'
                                : 'Checking…';
                          // I8 — non-color text cue so the dot's state is legible
                          // without relying on hue (WCAG 1.4.1). "Ready" is the
                          // expected resting state so we omit its label to avoid
                          // noise; every other state shows a short word.
                          const dotCue = readyState === 'ready' ? '' : dotTitle;
                          // Sub-row: instance name + customer/subtitle. For singleton groups
                          // the displayName already matches the kindLabel header, but the
                          // sub-row still earns its keep by showing the YAML key, status
                          // dot, default star, and active-call badge.
                          const lineLabel = provider.name;
                          const lineSubtitle = provider.displayName !== group.kindLabel
                            ? provider.displayName
                            : (provider.subtitle.includes('·')
                                ? provider.subtitle.split('·').slice(1).join('·').trim() || ''
                                : '');
                          return (
                            <div
                              key={provider.name}
                              onClick={() => navigate('/providers')}
                              title={`Configure ${provider.displayName} (${provider.subtitle}) →`}
                              className="relative flex items-center gap-2 px-2 py-1 rounded-lg hover:bg-background/50 cursor-pointer transition-colors"
                            >
                              {isActive && (
                                <div className="absolute inset-0 rounded-lg border border-green-500 animate-ping opacity-20 pointer-events-none" />
                              )}
                              <div
                                role="status"
                                aria-label={`${provider.name}: ${dotTitle}`}
                                className={`w-2 h-2 rounded-full flex-shrink-0 ${dotColor} ${dotAnim}`}
                                title={dotTitle}
                              />
                              <div className="min-w-0 flex-1">
                                <div className={`text-xs font-medium truncate ${isActive ? 'text-green-500' : 'text-foreground'}`}>
                                  {lineLabel}
                                </div>
                                {lineSubtitle && (
                                  <div className="text-[10px] text-muted-foreground truncate">
                                    {lineSubtitle}
                                  </div>
                                )}
                              </div>
                              {dotCue && (
                                <span className="text-[10px] uppercase tracking-wide text-muted-foreground flex-shrink-0">
                                  {dotCue}
                                </span>
                              )}
                              {isDefault && (
                                <div
                                  role="img"
                                  aria-label="Default provider"
                                  className="w-2 h-2 rounded-full bg-yellow-500 flex-shrink-0"
                                  title="Default Provider"
                                />
                              )}
                              {isActive && (
                                <span className="px-1.5 py-0.5 rounded-full bg-green-500 text-white text-[10px] font-bold flex-shrink-0">
                                  {activeCount}
                                </span>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>

          {/* === ROW 2: SVG-based T-junction from AI Engine === */}

          {/* SVG spans only cols 1-4 (fixed widths summing to 416px). All
              T-junction paths reference x=80 (col 1 center) and x=288 (col 3
              center) — both within the 416 unit viewBox. Constraining the
              SVG to cols 1-4 means the arrow heads keep landing exactly on
              col-3 center (Local AI top) and col-1 center (Pipelines top)
              regardless of how wide col 5 grows. */}
          <div className="col-span-4 h-14 relative">
            <svg
              role="img"
              aria-label={`AI Engine to Local AI flow: ${isLocalAIActive ? 'active' : 'idle'}; AI Engine to Pipelines flow: ${activePipelines.size > 0 ? 'active' : 'idle'}`}
              className="absolute inset-0 w-full h-full"
              viewBox="0 0 416 56"
              preserveAspectRatio="xMidYMid meet"
            >
              {/* Cols 1-4 widths: 160 + 48 + 160 + 48 = 416 total. */}
              {/* Col 1 center: 80, Col 3 center: 160+48+80 = 288. */}

              {/* Center bezier path from AI Engine to Local AI using smooth corners */}
              <path
                d="M 288 0 L 288 48"
                stroke={isLocalAIActive ? '#22c55e' : '#e5e7eb'}
                strokeWidth="2"
                fill="none"
                strokeDasharray="4 4"
                className={isLocalAIActive ? 'animate-flow-dash' : ''}
              />
              <polygon
                points="288,56 282,46 294,46"
                fill={isLocalAIActive ? '#22c55e' : '#e5e7eb'}
              />

              {/* Left bezier path from AI Engine to Pipelines branching off */}
              <path
                d="M 288 12 Q 288 20 280 20 L 88 20 Q 80 20 80 28 L 80 48"
                stroke={activePipelines.size > 0 ? '#22c55e' : '#e5e7eb'}
                strokeWidth="2"
                fill="none"
                strokeDasharray="4 4"
                className={activePipelines.size > 0 ? 'animate-flow-dash' : ''}
              />
              <polygon
                points="80,56 74,46 86,46"
                fill={activePipelines.size > 0 ? '#22c55e' : '#e5e7eb'}
              />
            </svg>
          </div>

          {/* CRITICAL — explicit empty placeholder for row 2, col 5.
              Without this, CSS Grid's auto-placement sees the open slot and
              shoves the next item (Pipelines) into it, breaking the layout.
              Discovered the hard way in commit b0267916 (reverted). */}
          <div aria-hidden="true" />

          {/* === ROW 3: Pipelines ← Local AI Server → Models === */}

          {/* Pipelines with sub-components */}
          <div>
            <div className="flex justify-center">
              <div
                onClick={() => navigate('/pipelines')}
                title="Go to Pipelines →"
                className="inline-block px-3 py-1 mx-auto rounded-full bg-muted/40 border border-border/50 text-[10px] text-muted-foreground uppercase tracking-wider mb-3 text-center cursor-pointer hover:text-primary transition-colors"
              >Pipelines</div>
            </div>
            {state.configuredPipelines.length === 0 ? (
              <div className="p-3 rounded-lg border border-dashed border-border text-xs text-muted-foreground text-center">
                No pipelines
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                {state.configuredPipelines.map(pipeline => {
                  const activeCount = activePipelines.get(pipeline.name) || 0;
                  const isActive = activeCount > 0;
                  // Check active/default pipeline routes and defaultProvider since default_provider can be a pipeline name.
                  // AAVA-185: Also match pipeline variants (e.g. pipeline card "local_hybrid_groq"
                  // matches defaultProvider "local_hybrid"). Only forward direction — avoid marking
                  // the base pipeline card as default when a variant is the actual default.
                  const isDefault = pipeline.name === state.activePipeline
                    || pipeline.name === state.defaultPipeline
                    || pipeline.name === state.defaultProvider
                    || (state.activePipeline && pipeline.name.startsWith(state.activePipeline + '_'))
                    || (state.defaultPipeline && pipeline.name.startsWith(state.defaultPipeline + '_'))
                    || (state.defaultProvider && pipeline.name.startsWith(state.defaultProvider + '_'));
                  return (
                    <div key={pipeline.name} onClick={() => navigate('/pipelines')} title={`Configure ${pipeline.name.replace(/_/g, ' ')} →`} className="flex flex-col cursor-pointer hover:opacity-80">
                      {/* Pipeline name header */}
                      <div
                        className={`relative flex items-center gap-2 p-2 rounded-t-xl border border-b-0 backdrop-blur-sm transition-all ${isActive
                          ? 'border-green-500/50 bg-green-500/10 shadow-[0_-4px_15px_rgb(34,197,94,0.05)] ring-1 ring-green-500/30 ring-b-0'
                          : 'border-border/60 bg-card/70'
                          }`}
                      >
                        <Layers className={`w-4 h-4 flex-shrink-0 ${isActive ? 'text-green-500' : 'text-muted-foreground'}`} />
                        <span className={`text-xs font-medium truncate ${isActive ? 'text-green-500' : 'text-foreground'}`}>
                          {pipeline.name.replace(/_/g, ' ')}
                        </span>
                        {isDefault && <div role="img" aria-label="Default pipeline" className="w-2.5 h-2.5 rounded-full bg-yellow-500 ml-auto flex-shrink-0" title="Default Pipeline" />}
                      </div>
                      {/* Pipeline components (STT/LLM/TTS) */}
                      <div className={`flex flex-col gap-0.5 p-1.5 rounded-b-xl border backdrop-blur-sm transition-all ${isActive ? 'border-green-500/50 bg-green-500/5 ring-1 ring-green-500/30 ring-t-0 shadow-[0_4px_15px_rgb(34,197,94,0.05)]' : 'border-border/60 bg-muted/20'
                        }`}>
                        {/* STT */}
                        <div className={`flex items-center gap-1.5 px-1.5 py-0.5 rounded text-[10px] ${isActive ? 'text-green-500' : 'text-muted-foreground'
                          }`}>
                          <Mic className={`w-3 h-3 ${isActive ? 'text-green-500' : 'text-muted-foreground'}`} />
                          <span className="truncate">{pipeline.stt || 'N/A'}</span>
                        </div>
                        {/* LLM */}
                        <div className={`flex items-center gap-1.5 px-1.5 py-0.5 rounded text-[10px] ${isActive ? 'text-green-500' : 'text-muted-foreground'
                          }`}>
                          <MessageSquare className={`w-3 h-3 ${isActive ? 'text-green-500' : 'text-muted-foreground'}`} />
                          <span className="truncate">{pipeline.llm || 'N/A'}</span>
                        </div>
                        {/* TTS */}
                        <div className={`flex items-center gap-1.5 px-1.5 py-0.5 rounded text-[10px] ${isActive ? 'text-green-500' : 'text-muted-foreground'
                          }`}>
                          <Volume2 className={`w-3 h-3 ${isActive ? 'text-green-500' : 'text-muted-foreground'}`} />
                          <span className="truncate">{pipeline.tts || 'N/A'}</span>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Arrow: Pipelines ← Local AI */}
          <div className="flex items-center justify-center self-center w-full">
            <svg
              role="img"
              aria-label={`Local AI to Pipelines flow: ${isLocalAIUsedByPipelines ? 'active' : 'idle'}`}
              className="w-full h-4 overflow-visible"
              viewBox="0 0 48 16"
              preserveAspectRatio="none"
            >
              <path
                d="M 48 8 L 8 8"
                stroke={isLocalAIUsedByPipelines ? '#22c55e' : '#e5e7eb'}
                strokeWidth="2"
                className={isLocalAIUsedByPipelines ? 'animate-flow-dash' : ''}
                strokeDasharray="4 4"
              />
              <polygon points="8,3 0,8 8,13" fill={isLocalAIUsedByPipelines ? '#22c55e' : '#e5e7eb'} />
            </svg>
          </div>

          {/* Local AI Server (aligned with AI Engine above) */}
          <div className="flex flex-col h-full self-stretch py-10">
            <div className="flex justify-center mb-3 flex-shrink-0"><div className="inline-block px-3 py-1 rounded-full bg-muted/40 border border-border/50 text-[10px] text-muted-foreground uppercase tracking-wider text-center">Local AI Server</div></div>
            <div className="flex justify-center flex-1 h-full">
              <div
                onClick={() => navigate('/models')}
                title="Go to Models →"
                className={`flex flex-col justify-center relative w-full h-full p-4 rounded-xl border backdrop-blur-sm transition-all duration-300 cursor-pointer hover:-translate-y-1 ${localAIHasRequiredError
                  ? 'border-red-500/50 bg-red-500/10 ring-1 ring-red-500/50'
                  : localAIIsActiveConnected
                    ? 'border-green-500/50 bg-green-500/10 shadow-[0_8px_30px_rgb(34,197,94,0.15)] ring-1 ring-green-500/50'
                    : 'border-border/60 bg-card/60 hover:bg-card/80 hover:border-primary/40 shadow-sm'
                  }`}>
                {localAIIsActiveConnected && (
                  <div className="absolute inset-0 rounded-lg border-2 border-green-500 animate-ping opacity-20" />
                )}
                <div className="flex flex-col items-center gap-2">
                  <Server className={`w-8 h-8 ${localAIHasRequiredError ? 'text-red-500' : localAIHasOptionalWarning ? 'text-amber-500' : localAIIsActiveConnected ? 'text-green-500' : 'text-muted-foreground'
                    }`} />
                  <div className="text-center">
                    <div className={`font-semibold ${localAIHasRequiredError ? 'text-red-500' : localAIHasOptionalWarning ? 'text-amber-500' : localAIIsActiveConnected ? 'text-green-500' : 'text-foreground'
                      }`}>Local AI</div>
                    <div className="text-xs text-muted-foreground">Server</div>
                  </div>
                  <div className="w-full pt-2 mt-2 border-t border-border/50">
                    <div className="flex items-center justify-center text-xs">
                      {localAIHasOptionalWarning ? (
                        <span className="flex items-center gap-1 text-amber-500">
                          <AlertTriangle className="w-3 h-3" /> Optional offline
                        </span>
                      ) : localAIIsOptionalInactive ? (
                        <span className="flex items-center gap-1 text-muted-foreground">
                          <Server className="w-3 h-3" /> Optional
                        </span>
                      ) : state.localAIStatus === 'unknown' ? (
                        <span className="flex items-center gap-1 text-muted-foreground">
                          <Loader2 className="w-3 h-3 animate-spin" /> Checking…
                        </span>
                      ) : state.localAIStatus === 'connected' ? (
                        <span className="flex items-center gap-1 text-green-500">
                          <CheckCircle2 className="w-3 h-3" /> Connected
                        </span>
                      ) : (
                        <span className="flex items-center gap-1 text-red-500">
                          <XCircle className="w-3 h-3" /> Disconnected
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Arrow: Local AI → Models */}
          <div className="flex items-center justify-center self-center w-full">
            <svg
              role="img"
              aria-label={`Local AI to Models flow: ${isLocalAIActive ? 'active' : 'idle'}`}
              className="w-full h-4 overflow-visible"
              viewBox="0 0 48 16"
              preserveAspectRatio="none"
            >
              <path
                d="M 0 8 L 40 8"
                stroke={isLocalAIActive ? '#22c55e' : '#e5e7eb'}
                strokeWidth="2"
                className={isLocalAIActive ? 'animate-flow-dash' : ''}
                strokeDasharray="4 4"
              />
              <polygon points="40,3 48,8 40,13" fill={isLocalAIActive ? '#22c55e' : '#e5e7eb'} />
            </svg>
          </div>

          {/* STT / LLM / TTS Models */}
          <div>
            <div className="flex justify-center">
              <div
                onClick={() => navigate('/models')}
                title="Go to Models →"
                className="inline-block px-3 py-1 mx-auto rounded-full bg-muted/40 border border-border/50 text-[10px] text-muted-foreground uppercase tracking-wider mb-3 text-center cursor-pointer hover:text-primary transition-colors"
              >Models</div>
            </div>
            {/* Models row uses the same flex-grow col 5 width as the
                providers grid above. With STT/LLM/TTS sitting side-by-side
                the section is ~80px tall instead of ~210px. */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              {/* STT */}
              <div onClick={() => navigate('/models')} title="Go to Models →" className={`relative flex items-center gap-2 p-2 px-3 rounded-xl border backdrop-blur-sm transition-all duration-300 cursor-pointer hover:-translate-y-[1px] ${activeLocalModels.stt && state.localAIModels?.stt?.loaded
                ? 'border-green-500/50 bg-green-500/10 shadow-[0_4px_15px_rgb(34,197,94,0.1)] ring-1 ring-green-500/30'
                : state.localAIModels?.stt?.loaded ? 'border-border/60 bg-card/60 hover:bg-card/80 shadow-sm' : 'border-border/40 bg-muted/30'
                }`}>
                {activeLocalModels.stt && state.localAIModels?.stt?.loaded && (
                  <div className="absolute inset-0 rounded-lg border-2 border-green-500 animate-ping opacity-20" />
                )}
                <Mic className={`w-4 h-4 ${activeLocalModels.stt && state.localAIModels?.stt?.loaded ? 'text-green-500 animate-pulse' : state.localAIModels?.stt?.loaded ? 'text-green-500' : 'text-muted-foreground'}`} />
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium">STT</div>
                  <div className="text-[10px] text-muted-foreground" title={getModelDisplayName(state.localAIModels?.stt, 'Not loaded')}>
                    {getModelDisplayName(state.localAIModels?.stt, 'Not loaded')}
                  </div>
                </div>
                {state.localAIModels?.stt?.loaded ? (
                  <CheckCircle2 className="w-3.5 h-3.5 text-green-500 flex-shrink-0" />
                ) : (
                  <XCircle className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
                )}
              </div>

              {/* LLM */}
              <div onClick={() => navigate('/models')} title="Go to Models →" className={`relative flex items-center gap-2 p-2 px-3 rounded-xl border backdrop-blur-sm transition-all duration-300 cursor-pointer hover:-translate-y-[1px] ${activeLocalModels.llm && state.localAIModels?.llm?.loaded
                ? 'border-green-500/50 bg-green-500/10 shadow-[0_4px_15px_rgb(34,197,94,0.1)] ring-1 ring-green-500/30'
                : state.localAIModels?.llm?.loaded ? 'border-border/60 bg-card/60 hover:bg-card/80 shadow-sm' : 'border-border/40 bg-muted/30'
                }`}>
                {activeLocalModels.llm && state.localAIModels?.llm?.loaded && (
                  <div className="absolute inset-0 rounded-lg border-2 border-green-500 animate-ping opacity-20" />
                )}
                <MessageSquare className={`w-4 h-4 ${activeLocalModels.llm && state.localAIModels?.llm?.loaded ? 'text-green-500 animate-pulse' : state.localAIModels?.llm?.loaded ? 'text-green-500' : 'text-muted-foreground'}`} />
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium">LLM</div>
                  <div className="text-[10px] text-muted-foreground" title={getModelDisplayName(state.localAIModels?.llm, 'Not loaded')}>
                    {getModelDisplayName(state.localAIModels?.llm, 'Not loaded')}
                  </div>
                </div>
                {state.localAIModels?.llm?.loaded ? (
                  <CheckCircle2 className="w-3.5 h-3.5 text-green-500 flex-shrink-0" />
                ) : (
                  <XCircle className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
                )}
              </div>

              {/* TTS */}
              <div onClick={() => navigate('/models')} title="Go to Models →" className={`relative flex items-center gap-2 p-2 px-3 rounded-xl border backdrop-blur-sm transition-all duration-300 cursor-pointer hover:-translate-y-[1px] ${activeLocalModels.tts && state.localAIModels?.tts?.loaded
                ? 'border-green-500/50 bg-green-500/10 shadow-[0_4px_15px_rgb(34,197,94,0.1)] ring-1 ring-green-500/30'
                : state.localAIModels?.tts?.loaded ? 'border-border/60 bg-card/60 hover:bg-card/80 shadow-sm' : 'border-border/40 bg-muted/30'
                }`}>
                {activeLocalModels.tts && state.localAIModels?.tts?.loaded && (
                  <div className="absolute inset-0 rounded-lg border-2 border-green-500 animate-ping opacity-20" />
                )}
                <Volume2 className={`w-4 h-4 ${activeLocalModels.tts && state.localAIModels?.tts?.loaded ? 'text-green-500 animate-pulse' : state.localAIModels?.tts?.loaded ? 'text-green-500' : 'text-muted-foreground'}`} />
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium">TTS</div>
                  <div className="text-[10px] text-muted-foreground" title={getModelDisplayName(state.localAIModels?.tts, 'Not loaded')}>
                    {getModelDisplayName(state.localAIModels?.tts, 'Not loaded')}
                  </div>
                </div>
                {state.localAIModels?.tts?.loaded ? (
                  <CheckCircle2 className="w-3.5 h-3.5 text-green-500 flex-shrink-0" />
                ) : (
                  <XCircle className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Legend */}
        <div className="flex items-center justify-center gap-4 pt-4 mt-4 border-t border-border text-[10px] text-muted-foreground flex-wrap">
          <div className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full bg-green-500" />
            <span>Ready</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full bg-orange-500" />
            <span>Disabled</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full bg-red-500" />
            <span>Not Ready</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full bg-yellow-500" />
            <span>Default</span>
          </div>
        </div>
      </div>

      {/* CSS for flow animation */}
      <style>{`
        @keyframes flow-dash {
          to {
            stroke-dashoffset: -8;
          }
        }
        .animate-flow-dash {
          animation: flow-dash 0.5s linear infinite;
        }
      `}</style>
    </FullscreenPanel>
  );
};

export default SystemTopology;
