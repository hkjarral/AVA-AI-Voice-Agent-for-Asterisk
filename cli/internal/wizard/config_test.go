package wizard

import (
	"os"
	"path/filepath"
	"testing"
)

func withTempProject(t *testing.T, base, local string, fn func()) {
	t.Helper()
	dir := t.TempDir()
	if err := os.Mkdir(filepath.Join(dir, "config"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, ".env"), []byte("ASTERISK_HOST=127.0.0.1\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "config", "ai-agent.yaml"), []byte(base), 0o644); err != nil {
		t.Fatal(err)
	}
	if local != "" {
		if err := os.WriteFile(filepath.Join(dir, "config", "ai-agent.local.yaml"), []byte(local), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	old, _ := os.Getwd()
	if err := os.Chdir(dir); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chdir(old) })
	fn()
}

func TestLoadConfigDiscoversCurrentTargetsAndMergesOverrides(t *testing.T) {
	base := `active_pipeline: null
default_provider: local_hybrid
pipelines:
  local_hybrid: {stt: local_stt, llm: openai_llm, tts: local_tts}
providers:
  local_stt: {capabilities: [stt]}
  openai_realtime: {type: full, capabilities: [stt, llm, tts]}
`
	local := `default_provider: google_live
providers:
  google_live: {type: full, capabilities: [stt, llm, tts]}
`
	withTempProject(t, base, local, func() {
		cfg, err := LoadConfig()
		if err != nil {
			t.Fatal(err)
		}
		if cfg.DefaultProvider != "google_live" || cfg.ActivePipeline != "" {
			t.Fatalf("merged selection = provider %q pipeline %q", cfg.DefaultProvider, cfg.ActivePipeline)
		}
		if len(cfg.AvailablePipelines) != 1 || cfg.AvailablePipelines[0] != "local_hybrid" {
			t.Fatalf("pipelines = %#v", cfg.AvailablePipelines)
		}
		if len(cfg.AvailableProviders) != 2 {
			t.Fatalf("providers = %#v", cfg.AvailableProviders)
		}
	})
}

func TestSaveYAMLClearsActivePipelineForFullAgent(t *testing.T) {
	base := "active_pipeline: local_hybrid\ndefault_provider: local_hybrid\nproviders: {}\npipelines: {}\n"
	local := "active_pipeline: local_hybrid\ndefault_provider: local_hybrid\n"
	withTempProject(t, base, local, func() {
		cfg, err := LoadConfig()
		if err != nil {
			t.Fatal(err)
		}
		cfg.ActivePipeline = ""
		cfg.DefaultProvider = "openai_realtime"
		if err := cfg.SaveYAML(""); err != nil {
			t.Fatal(err)
		}
		reloaded, err := LoadConfig()
		if err != nil {
			t.Fatal(err)
		}
		if reloaded.ActivePipeline != "" || reloaded.DefaultProvider != "openai_realtime" {
			t.Fatalf("saved selection = provider %q pipeline %q", reloaded.DefaultProvider, reloaded.ActivePipeline)
		}
	})
}
