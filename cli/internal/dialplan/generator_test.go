package dialplan

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDefaultUsesDatabaseSelection(t *testing.T) {
	text := GenerateAgentSnippet("", "")
	if strings.Contains(text, "Set(AI_AGENT=") || strings.Contains(text, "Set(AI_PROVIDER=") {
		t.Fatal(text)
	}
	if !strings.Contains(text, "Stasis(asterisk-ai-voice-agent)") {
		t.Fatal(text)
	}
}

func TestExplicitAgentAndCustomApplication(t *testing.T) {
	text := GenerateAgentSnippet("receptionist", "", "custom-app")
	if !strings.Contains(text, "Set(AI_AGENT=receptionist)") || !strings.Contains(text, "Stasis(custom-app)") || strings.Contains(text, "AI_PROVIDER") {
		t.Fatal(text)
	}
	text = GenerateAgentSnippet("sales", "local_hybrid_groq", "custom-app")
	if !strings.Contains(text, "Set(AI_PROVIDER=local_hybrid_groq)") {
		t.Fatal(text)
	}
}

func TestAppUsesLocalOverride(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "ai-agent.yaml"), []byte("asterisk:\n  app_name: base\n"), 0600)
	os.WriteFile(filepath.Join(dir, "ai-agent.local.yaml"), []byte("asterisk:\n  app_name: custom\n"), 0600)
	app, err := ConfiguredAppName(dir)
	if err != nil || app != "custom" {
		t.Fatalf("app=%q err=%v", app, err)
	}
}

func TestRejectInjectedIdentifiers(t *testing.T) {
	for _, values := range [][3]string{{"sales\nHangup()", "", "app"}, {"sales", "x,y", "app"}, {"sales", "", "app)"}} {
		if ValidateIdentifiers(values[0], values[1], values[2]) == nil {
			t.Fatal(values)
		}
	}
}
