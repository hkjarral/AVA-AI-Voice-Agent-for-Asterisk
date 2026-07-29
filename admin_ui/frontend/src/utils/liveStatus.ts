export interface LiveStatusComponentLike {
    state?: string;
    freshness?: string;
    details?: Record<string, any>;
}

export interface LocalAILiveStatus {
    connected: boolean;
    degraded: boolean;
    state: string;
    details: Record<string, any>;
}

export const localAIStatusFromLiveSnapshot = (snapshot: any): LocalAILiveStatus | null => {
    const component: LiveStatusComponentLike | undefined =
        snapshot?.components?.local_ai_server || snapshot?.local_ai_server;
    if (!component || component.freshness === 'expired') return null;

    const state = String(component.state || 'unknown');
    const connected = state === 'ready' || state === 'degraded';
    if (!connected && !['error', 'unreachable'].includes(state)) return null;

    return {
        connected,
        degraded: state === 'degraded',
        state,
        details: component.details || {},
    };
};
