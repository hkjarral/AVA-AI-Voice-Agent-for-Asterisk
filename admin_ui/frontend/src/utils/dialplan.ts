// Dialplan snippet generation for the setup wizard's "Done" step.
// v7+ form: AI_AGENT selects the operator-managed agent by slug; AI_PROVIDER is
// an optional per-call provider override. Mirrors the Go CLI generator
// (cli/internal/dialplan/generator.go). The legacy AI_CONTEXT variable is still
// accepted by the engine but is no longer emitted here.

const SUPPORTED_PROVIDERS = new Set([
    'google_live',
    'openai_realtime',
    'deepgram',
    'local_hybrid',
    'local',
    'elevenlabs_agent',
    'grok',
]);

export function getDialplanProviderOverride(provider: string): string {
    return SUPPORTED_PROVIDERS.has(provider) ? provider : 'openai_realtime';
}

export function buildAgentDialplan(provider: string): string {
    const providerOverride = getDialplanProviderOverride(provider);
    return `; extensions_custom.conf
[from-ai-agent]
exten => s,1,NoOp(AI Agent Call)
 same => n,Set(AI_AGENT=default)
 same => n,Set(AI_PROVIDER=${providerOverride})   ; optional per-call provider override
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()`;
}
