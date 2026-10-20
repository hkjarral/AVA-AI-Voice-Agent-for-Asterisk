package troubleshoot

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// TestCallAnthropicUsesCurrentModelAndReadsTextBlock verifies the request settings
// and extracts the diagnosis when a thinking block precedes the text response.
func TestCallAnthropicUsesCurrentModelAndReadsTextBlock(t *testing.T) {
	var body map[string]interface{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Errorf("decode request: %v", err)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"type":"message","content":[` +
			`{"type":"thinking","thinking":"","signature":"sig"},` +
			`{"type":"text","text":"ROOT CAUSE: codec mismatch"}]}`))
	}))
	defer server.Close()

	oldURL := anthropicMessagesURL
	anthropicMessagesURL = server.URL
	defer func() { anthropicMessagesURL = oldURL }()

	t.Setenv("TROUBLESHOOT_LLM_PROVIDER", "anthropic")
	t.Setenv("ANTHROPIC_API_KEY", "test-key")
	llm, err := NewLLMAnalyzer()
	if err != nil {
		t.Fatalf("NewLLMAnalyzer: %v", err)
	}

	text, err := llm.callAnthropic("diagnose")
	if err != nil {
		t.Fatalf("callAnthropic: %v", err)
	}
	if text != "ROOT CAUSE: codec mismatch" {
		t.Fatalf("text=%q", text)
	}
	// claude-3-haiku-20240307 was retired on 2026-04-20.
	if body["model"] != "claude-haiku-5-5" {
		t.Fatalf("model=%v", body["model"])
	}
	thinking, _ := body["thinking"].(map[string]interface{})
	if thinking["type"] != "disabled" {
		t.Fatalf("thinking=%v", body["thinking"])
	}
}
