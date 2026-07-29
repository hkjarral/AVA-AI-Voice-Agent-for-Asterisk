export type TopologyTriStatus = 'connected' | 'error' | 'unknown';
export type ProviderReadyState = 'unknown' | 'ready' | 'not_ready';
export type OverallTopologyStatus = 'healthy' | 'issue' | 'checking';

export interface TopologyProviderSummary {
    name: string;
    enabled: boolean;
    kind?: string;
}

export interface TopologyPipelineSummary {
    name: string;
    stt?: string;
    llm?: string;
    tts?: string;
}

export interface TopologyIssue {
    key: string;
    label: string;
    detail: string;
    target: 'env' | 'providers' | 'models';
}

export type TopologyWarning = TopologyIssue;

export interface TopologyHealthInput {
    aiEngineStatus: TopologyTriStatus;
    ariConnected: boolean | null;
    localAIStatus: TopologyTriStatus;
    configuredProviders: TopologyProviderSummary[];
    providerReady: Record<string, ProviderReadyState>;
    configuredPipelines: TopologyPipelineSummary[];
    defaultProvider: string | null;
    defaultPipeline: string | null;
    activePipeline: string | null;
    activeProviderNames: string[];
    activePipelineNames: string[];
}

export interface TopologyHealthSummary {
    localAIRequired: boolean;
    localAIRelevant: boolean;
    localAIOptionalUnavailable: boolean;
    issues: TopologyIssue[];
    warnings: TopologyWarning[];
    overallStatus: OverallTopologyStatus;
    providersAllKnown: boolean;
    providersAnyError: boolean;
}

const usesLocalComponent = (value?: string): boolean =>
    typeof value === 'string' && value.toLowerCase().includes('local');

const pipelineUsesLocalAI = (pipeline: TopologyPipelineSummary): boolean =>
    usesLocalComponent(pipeline.stt)
    || usesLocalComponent(pipeline.llm)
    || usesLocalComponent(pipeline.tts);

const providerIsLocal = (name: string | null | undefined): boolean =>
    (name || '').toLowerCase() === 'local';

const providerSummaryIsLocal = (provider: TopologyProviderSummary): boolean =>
    providerIsLocal(provider.name) || providerIsLocal(provider.kind);

const pipelineMatchesRoute = (pipelineName: string, routeName: string): boolean =>
    pipelineName === routeName || pipelineName.startsWith(`${routeName}_`);

const unique = (values: Array<string | null | undefined>): string[] => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const value of values) {
        if (!value || seen.has(value)) continue;
        seen.add(value);
        out.push(value);
    }
    return out;
};

export const deriveLocalAIRequired = (input: Pick<
    TopologyHealthInput,
    'configuredProviders' | 'configuredPipelines' | 'defaultProvider' | 'defaultPipeline' | 'activePipeline' | 'activeProviderNames' | 'activePipelineNames'
>): boolean => {
    const providerSummariesByName = new Map(input.configuredProviders.map(provider => [provider.name, provider]));
    const providerRouteIsLocal = (name: string | null | undefined): boolean => {
        if (providerIsLocal(name)) return true;
        if (!name) return false;
        const provider = providerSummariesByName.get(name);
        return provider ? providerSummaryIsLocal(provider) : false;
    };

    const configuredProviderNames = new Set(input.configuredProviders.map(provider => provider.name));
    const configuredPipelineNames = new Set(input.configuredPipelines.map(pipeline => pipeline.name));
    const defaultTargetIsProvider =
        typeof input.defaultProvider === 'string' && configuredProviderNames.has(input.defaultProvider);
    const defaultTargetIsPipeline =
        typeof input.defaultProvider === 'string' && configuredPipelineNames.has(input.defaultProvider);
    const defaultPipeline = input.defaultPipeline;
    const defaultPipelineTargetsConfiguredPipeline =
        typeof defaultPipeline === 'string'
        && input.configuredPipelines.some(pipeline => pipelineMatchesRoute(pipeline.name, defaultPipeline));

    if (input.activeProviderNames.some(providerRouteIsLocal)) return true;
    // contexts.default.pipeline is resolved before the provider route. If it
    // points to a configured pipeline, default_provider is only a fallback and
    // should not make Local AI required on its own.
    if (!defaultPipelineTargetsConfiguredPipeline && providerRouteIsLocal(input.defaultProvider)) return true;

    const routeNames = unique([
        // Active calls always reflect the actual selected pipeline.
        ...input.activePipelineNames,
        // contexts.default.pipeline is resolved before the provider route.
        input.defaultPipeline,
        // default_provider may point at a pipeline in pipeline-first setups.
        defaultTargetIsPipeline ? input.defaultProvider : null,
        // Cloud full-agent presets can keep a legacy active_pipeline configured
        // while contexts.default.provider routes calls to the cloud provider.
        // In that case, active_pipeline is not a Local AI requirement.
        !defaultTargetIsProvider ? input.activePipeline : null,
    ]);

    return input.configuredPipelines.some(pipeline =>
        routeNames.some(routeName => pipelineMatchesRoute(pipeline.name, routeName)) && pipelineUsesLocalAI(pipeline)
    );
};

export const deriveTopologyHealth = (input: TopologyHealthInput): TopologyHealthSummary => {
    const enabledProviders = input.configuredProviders.filter(p => p.enabled);
    const providersAllKnown =
        enabledProviders.length === 0
        || enabledProviders.every(p => (input.providerReady[p.name] ?? 'unknown') !== 'unknown');
    const providerIssues = enabledProviders
        .filter(p => input.providerReady[p.name] === 'not_ready')
        .map<TopologyIssue>(p => ({
            key: `provider:${p.name}`,
            label: `Provider ${p.name} is not ready`,
            detail: 'The enabled provider health check is failing.',
            target: 'providers',
        }));

    const localAIRequired = deriveLocalAIRequired(input);
    const localAIRelevant = localAIRequired || input.localAIStatus === 'connected';
    const localAIOptionalUnavailable = !localAIRequired && input.localAIStatus === 'error';

    const issues: TopologyIssue[] = [];
    const warnings: TopologyWarning[] = [];
    if (input.aiEngineStatus === 'error') {
        issues.push({
            key: 'ai_engine',
            label: 'AI Engine is unhealthy',
            detail: 'The AI Engine health endpoint is not reporting connected.',
            target: 'env',
        });
    }
    if (input.ariConnected === false) {
        issues.push({
            key: 'ari',
            label: 'Asterisk ARI is disconnected',
            detail: 'The AI Engine health payload reports ARI as disconnected.',
            target: 'env',
        });
    }
    issues.push(...providerIssues);
    if (localAIRequired && input.localAIStatus === 'error') {
        issues.push({
            key: 'local_ai_server',
            label: 'Local AI Server is disconnected',
            detail: 'The active or default route uses Local AI, but local_ai_server is not connected.',
            target: 'models',
        });
    }
    if (localAIOptionalUnavailable) {
        warnings.push({
            key: 'local_ai_server_optional',
            label: 'Optional Local AI Server is unavailable',
            detail: 'Calls can continue on the configured cloud provider, but local pipelines and local models are unavailable until local_ai_server reconnects.',
            target: 'models',
        });
    }

    const blockingUnknown =
        input.aiEngineStatus === 'unknown'
        || input.ariConnected === null
        || !providersAllKnown
        || (localAIRequired && input.localAIStatus === 'unknown');

    const overallStatus: OverallTopologyStatus =
        blockingUnknown ? 'checking' : issues.length > 0 ? 'issue' : 'healthy';

    return {
        localAIRequired,
        localAIRelevant,
        localAIOptionalUnavailable,
        issues,
        warnings,
        overallStatus,
        providersAllKnown,
        providersAnyError: providerIssues.length > 0,
    };
};
