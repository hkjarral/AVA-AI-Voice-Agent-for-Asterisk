package main

// getContextName returns the Asterisk dialplan context name for a given provider
func getContextName(provider string) string {
	contexts := map[string]string{
		"openai_realtime": "from-ai-agent-openai",
		"deepgram":        "from-ai-agent-deepgram",
		"local_hybrid":    "from-ai-agent-hybrid",
		"google_live":     "from-ai-agent-google",
	}

	if ctx, ok := contexts[provider]; ok {
		return ctx
	}
	return "from-ai-agent"
}
