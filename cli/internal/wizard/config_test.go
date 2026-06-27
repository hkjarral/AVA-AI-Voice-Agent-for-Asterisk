package wizard

import (
	"os"
	"path/filepath"
	"reflect"
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

// TestRequiredEnvKeys verifies that RequiredEnvKeys derives the correct set of
// cloud API key env vars from the selected pipeline's components, not from
// hardcoded pipeline names. This is the core fix for #438.
func TestRequiredEnvKeys(t *testing.T) {
	cases := []struct {
		name     string
		pipeline string
		comps    PipelineComponents
		provider string
		want     []string
	}{
		{
			name:     "local_hybrid uses openai_llm → needs OPENAI_API_KEY",
			pipeline: "local_hybrid",
			comps:    PipelineComponents{STT: "local_stt", LLM: "openai_llm", TTS: "local_tts"},
			want:     []string{"OPENAI_API_KEY"},
		},
		{
			name:     "local_only all local → no cloud keys",
			pipeline: "local_only",
			comps:    PipelineComponents{STT: "local_stt", LLM: "local_llm", TTS: "local_tts"},
			want:     []string{},
		},
		{
			name:     "hybrid_elevenlabs → ELEVENLABS_API_KEY + OPENAI_API_KEY (sorted)",
			pipeline: "hybrid_elevenlabs",
			comps:    PipelineComponents{STT: "local_stt", LLM: "openai_llm", TTS: "elevenlabs_tts"},
			want:     []string{"ELEVENLABS_API_KEY", "OPENAI_API_KEY"},
		},
		{
			name:     "hybrid_deepgram_openai → DEEPGRAM_API_KEY + OPENAI_API_KEY (sorted)",
			pipeline: "hybrid_deepgram_openai",
			comps:    PipelineComponents{STT: "local_stt", LLM: "openai_llm", TTS: "deepgram_tts"},
			want:     []string{"DEEPGRAM_API_KEY", "OPENAI_API_KEY"},
		},
		{
			name:     "custom pipeline with openai_llm + deepgram_tts → both keys (not hardcoded)",
			pipeline: "my_custom_pipeline",
			comps:    PipelineComponents{STT: "local_stt", LLM: "openai_llm", TTS: "deepgram_tts"},
			want:     []string{"DEEPGRAM_API_KEY", "OPENAI_API_KEY"},
		},
		{
			name:     "DefaultProvider openai_realtime → OPENAI_API_KEY",
			pipeline: "",
			provider: "openai_realtime",
			want:     []string{"OPENAI_API_KEY"},
		},
		{
			name:     "DefaultProvider elevenlabs_agent → ELEVENLABS_AGENT_ID + ELEVENLABS_API_KEY (sorted)",
			pipeline: "",
			provider: "elevenlabs_agent",
			want:     []string{"ELEVENLABS_AGENT_ID", "ELEVENLABS_API_KEY"},
		},
		{
			name:     "cloud_openai all openai adapters → single OPENAI_API_KEY (deduped)",
			pipeline: "cloud_openai",
			comps:    PipelineComponents{STT: "openai_stt", LLM: "openai_llm", TTS: "openai_tts"},
			want:     []string{"OPENAI_API_KEY"},
		},
		{
			name:     "groq_pipeline → GROQ_API_KEY only",
			pipeline: "groq_pipeline",
			comps:    PipelineComponents{STT: "groq_stt", LLM: "groq_llm", TTS: "groq_tts"},
			want:     []string{"GROQ_API_KEY"},
		},
		{
			name:     "cambai_pipeline → CAMB_API_KEY + DEEPGRAM_API_KEY",
			pipeline: "cambai_pipeline",
			comps:    PipelineComponents{STT: "deepgram_stt", LLM: "openai_llm", TTS: "cambai_tts"},
			want:     []string{"CAMB_API_KEY", "DEEPGRAM_API_KEY", "OPENAI_API_KEY"},
		},
		{
			name:     "no pipeline no provider → empty",
			pipeline: "",
			provider: "",
			want:     []string{},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cfg := &Config{
				ActivePipeline:  tc.pipeline,
				DefaultProvider: tc.provider,
				Pipelines:       make(map[string]PipelineComponents),
				Keys:            make(map[string]string),
			}
			if tc.pipeline != "" {
				cfg.Pipelines[tc.pipeline] = tc.comps
			}
			got := cfg.RequiredEnvKeys()
			// Normalise nil vs empty slice for comparison.
			if len(got) == 0 && len(tc.want) == 0 {
				return
			}
			if !reflect.DeepEqual(got, tc.want) {
				t.Errorf("RequiredEnvKeys() = %v, want %v", got, tc.want)
			}
		})
	}
}

// TestLoadAvailableTargetsPopulatesPipelines verifies that loadAvailableTargets
// reads the stt/llm/tts adapter names from the YAML and stores them in Pipelines.
func TestLoadAvailableTargetsPopulatesPipelines(t *testing.T) {
	base := `active_pipeline: local_hybrid
default_provider: local_hybrid
pipelines:
  local_hybrid: {stt: local_stt, llm: openai_llm, tts: local_tts}
providers:
  openai_realtime: {type: full, capabilities: [stt, llm, tts]}
`
	withTempProject(t, base, "", func() {
		cfg, err := LoadConfig()
		if err != nil {
			t.Fatal(err)
		}
		comps, ok := cfg.Pipelines["local_hybrid"]
		if !ok {
			t.Fatal("Pipelines[\"local_hybrid\"] not populated")
		}
		if comps.LLM != "openai_llm" {
			t.Errorf("LLM = %q, want %q", comps.LLM, "openai_llm")
		}
		if comps.STT != "local_stt" {
			t.Errorf("STT = %q, want %q", comps.STT, "local_stt")
		}
		if comps.TTS != "local_tts" {
			t.Errorf("TTS = %q, want %q", comps.TTS, "local_tts")
		}
		// RequiredEnvKeys should derive OPENAI_API_KEY from local_hybrid's openai_llm.
		keys := cfg.RequiredEnvKeys()
		if len(keys) != 1 || keys[0] != "OPENAI_API_KEY" {
			t.Errorf("RequiredEnvKeys() = %v, want [OPENAI_API_KEY]", keys)
		}
	})
}

// TestPartialLocalOverrideMergesPipelineComponents verifies that a local YAML
// that overrides only PART of a pipeline (e.g. only tts) preserves the base
// config's other components (stt, llm) rather than clobbering them with "".
// This is Bug 1 from issue #438.
func TestPartialLocalOverrideMergesPipelineComponents(t *testing.T) {
	base := `active_pipeline: p
pipelines:
  p: {stt: local_stt, llm: openai_llm, tts: local_tts}
providers: {}
`
	// Local override changes only tts; stt and llm are absent from this block.
	local := `pipelines:
  p: {tts: elevenlabs_tts}
`
	withTempProject(t, base, local, func() {
		cfg, err := LoadConfig()
		if err != nil {
			t.Fatal(err)
		}
		comps, ok := cfg.Pipelines["p"]
		if !ok {
			t.Fatal("Pipelines[\"p\"] not populated")
		}
		// stt and llm must be preserved from the base; only tts should be updated.
		if comps.STT != "local_stt" {
			t.Errorf("STT = %q, want %q (base value must be preserved)", comps.STT, "local_stt")
		}
		if comps.LLM != "openai_llm" {
			t.Errorf("LLM = %q, want %q (base value must be preserved)", comps.LLM, "openai_llm")
		}
		if comps.TTS != "elevenlabs_tts" {
			t.Errorf("TTS = %q, want %q (local override must win)", comps.TTS, "elevenlabs_tts")
		}
		// RequiredEnvKeys must include OPENAI_API_KEY (from openai_llm, preserved)
		// and ELEVENLABS_API_KEY (from the local tts override).
		keys := cfg.RequiredEnvKeys()
		wantKeys := []string{"ELEVENLABS_API_KEY", "OPENAI_API_KEY"}
		if !reflect.DeepEqual(keys, wantKeys) {
			t.Errorf("RequiredEnvKeys() = %v, want %v", keys, wantKeys)
		}
	})
}

// TestRequiredEnvKeysBothPipelineAndProvider verifies that when both
// ActivePipeline and DefaultProvider are set (as in golden configs like
// ai-agent.golden-google-live.yaml), RequiredEnvKeys returns keys from BOTH
// sources (union, not else-if). This is Bug 2 from issue #438.
func TestRequiredEnvKeysBothPipelineAndProvider(t *testing.T) {
	cfg := &Config{
		ActivePipeline:  "my_pipeline",
		DefaultProvider: "google_live",
		Pipelines: map[string]PipelineComponents{
			"my_pipeline": {STT: "local_stt", LLM: "openai_llm", TTS: "local_tts"},
		},
		Keys: make(map[string]string),
	}
	got := cfg.RequiredEnvKeys()
	// Must include OPENAI_API_KEY (from pipeline's openai_llm) AND
	// GOOGLE_API_KEY (from the google_live full-agent provider).
	wantKeys := []string{"GOOGLE_API_KEY", "OPENAI_API_KEY"}
	if !reflect.DeepEqual(got, wantKeys) {
		t.Errorf("RequiredEnvKeys() = %v, want %v (both pipeline and provider keys expected)", got, wantKeys)
	}
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
