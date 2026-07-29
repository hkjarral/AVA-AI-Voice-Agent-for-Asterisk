package main

import (
	"testing"

	"github.com/hkjarral/ava-ai-voice-agent-for-asterisk/cli/internal/check"
)

func TestReportHasTransientStartupWarning(t *testing.T) {
	tests := []struct {
		name   string
		report *check.Report
		want   bool
	}{
		{name: "nil", report: nil, want: false},
		{
			name: "ARI registering",
			report: &check.Report{Items: []check.Item{{
				Name: "ARI", Status: check.StatusWarn, Message: "reachable but app not registered",
			}}},
			want: true,
		},
		{
			name: "persistent unrelated warning",
			report: &check.Report{Items: []check.Item{{
				Name: "Dialplan", Status: check.StatusWarn, Message: "verify dialplan routes into Stasis",
			}}},
			want: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := reportHasTransientStartupWarning(tt.report); got != tt.want {
				t.Fatalf("reportHasTransientStartupWarning() = %v, want %v", got, tt.want)
			}
		})
	}
}
