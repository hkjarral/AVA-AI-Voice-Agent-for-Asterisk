package troubleshoot

import (
	"strings"
	"testing"
)

func TestDetectBaselineUsesResolvedProviderNotUnrelatedLogText(t *testing.T) {
	if got := detectBaseline(&RCAHeader{ProviderName: "google_live"}); got != "streaming_performance" {
		t.Fatalf("google_live baseline = %q, want streaming_performance", got)
	}
	if got := detectBaseline(&RCAHeader{ProviderName: "deepgram"}); got != "deepgram_standard" {
		t.Fatalf("deepgram baseline = %q", got)
	}
	if got := detectBaseline(&RCAHeader{ProviderName: "openai_realtime"}); got != "openai_realtime" {
		t.Fatalf("openai baseline = %q", got)
	}
}

func TestApplyCallContextIgnoresTinySegments(t *testing.T) {
	m := &CallMetrics{StreamingSummaries: []StreamingSummary{
		{BytesSent: 320, EffectiveSeconds: 0.04, DriftPct: 30},
		{BytesSent: 48000, EffectiveSeconds: 6, DriftPct: -2.5},
	}}
	m.ApplyCallContext(&RCAHeader{ProviderName: "elevenlabs_agent"})
	if m.WorstDriftPct != -2.5 {
		t.Fatalf("worst drift = %v, want -2.5", m.WorstDriftPct)
	}
}

func TestApplyCallContextSkipsPipelineDrift(t *testing.T) {
	m := &CallMetrics{StreamingSummaries: []StreamingSummary{{BytesSent: 58560, EffectiveSeconds: 7.32, DriftPct: -37.6}}}
	m.ApplyCallContext(&RCAHeader{ProviderName: "pipeline", PipelineName: "local_hybrid"})
	if m.WorstDriftPct != 0 || !strings.Contains(m.DriftAssessmentSkipped, "pipeline") {
		t.Fatalf("pipeline drift was not skipped: %+v", m)
	}
}

func TestIsolatedUnderflowsAreNotHighDeviation(t *testing.T) {
	m := &CallMetrics{
		UnderflowCount:     1,
		StreamingSummaries: []StreamingSummary{{BytesSent: 160000, EffectiveSeconds: 20}},
	}
	comparison := CompareToBaseline(m, "streaming_performance")
	for _, d := range comparison.Deviations {
		if d.Parameter == "underflow_count" {
			t.Fatalf("isolated underflow was promoted to deviation: %+v", d)
		}
	}
}
