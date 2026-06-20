package dialplan

import (
	"fmt"
	"strings"
)

// Context represents a dialplan context
type Context struct {
	Name        string
	Provider    string
	AIContext   string
	Description string
}

// GenerateSnippet generates dialplan snippet for a provider
func GenerateSnippet(provider string) string {
	return GenerateAgentSnippet("default", provider)
}

// GenerateAgentSnippet emits the v7 dialplan form. AI_AGENT selects the
// operator-managed agent; AI_PROVIDER is optional and only needed as an
// explicit per-call override.
func GenerateAgentSnippet(agent, provider string) string {
	ctx := getContextForProvider(provider)
	if strings.TrimSpace(agent) == "" {
		agent = "default"
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("; AI Voice Agent - %s\n", ctx.Description))
	sb.WriteString(fmt.Sprintf("[%s]\n", ctx.Name))
	sb.WriteString(fmt.Sprintf("exten => s,1,NoOp(%s)\n", ctx.Description))
	sb.WriteString(fmt.Sprintf(" same => n,Set(AI_AGENT=%s)\n", agent))
	if strings.TrimSpace(provider) != "" {
		sb.WriteString(fmt.Sprintf(" same => n,Set(AI_PROVIDER=%s)\n", ctx.Provider))
	}
	sb.WriteString(" same => n,Stasis(asterisk-ai-voice-agent)\n")
	sb.WriteString(" same => n,Hangup()\n")

	return sb.String()
}

// getContextForProvider returns context info for a provider
func getContextForProvider(provider string) Context {
	contexts := map[string]Context{
		"openai_realtime": {
			Name:        "from-ai-agent-openai",
			Provider:    "openai_realtime",
			AIContext:   "default",
			Description: "AI Agent - OpenAI Realtime",
		},
		"deepgram": {
			Name:        "from-ai-agent-deepgram",
			Provider:    "deepgram",
			AIContext:   "default",
			Description: "AI Agent - Deepgram",
		},
		"local_hybrid": {
			Name:        "from-ai-agent-hybrid",
			Provider:    "local_hybrid",
			AIContext:   "default",
			Description: "AI Agent - Local Hybrid",
		},
		"google_live": {
			Name:        "from-ai-agent-google",
			Provider:    "google_live",
			AIContext:   "default",
			Description: "AI Agent - Google Live",
		},
	}

	if ctx, ok := contexts[provider]; ok {
		return ctx
	}

	// Default/fallback
	return Context{
		Name:        "from-ai-agent",
		Provider:    provider,
		AIContext:   "default",
		Description: "AI Agent - " + provider,
	}
}

// GetProviderDisplayName returns friendly name for provider
func GetProviderDisplayName(provider string) string {
	names := map[string]string{
		"openai_realtime": "OpenAI Realtime",
		"deepgram":        "Deepgram Voice Agent",
		"local_hybrid":    "Local Hybrid",
		"google_live":     "Google Live API",
	}

	if name, ok := names[provider]; ok {
		return name
	}
	return provider
}
