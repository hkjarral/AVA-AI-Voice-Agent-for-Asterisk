package troubleshoot

import "strings"

// ProviderRuntimeAudio captures provider-reported/used audio rates discovered from logs.
// This complements the RCA header, which is a configured snapshot (RCA_CALL_START).
type ProviderRuntimeAudio struct {
	ProviderName string `json:"provider_name,omitempty"`

	ConfiguredOutputSampleRateHz       int `json:"configured_output_sample_rate_hz,omitempty"`
	ProviderReportedOutputSampleRateHz int `json:"provider_reported_output_sample_rate_hz,omitempty"`
	UsedOutputSampleRateHz             int `json:"used_output_sample_rate_hz,omitempty"`
}

func ExtractProviderRuntimeAudio(logData string) *ProviderRuntimeAudio {
	lines := strings.Split(logData, "\n")
	for _, line := range lines {
		_, _, fields, ok := parseLogLine(line)
		if !ok || len(fields) == 0 {
			continue
		}

		used := atoi(fields["used_output_sample_rate_hz"])
		if used <= 0 {
			continue
		}

		pr := &ProviderRuntimeAudio{
			ProviderName:                       strings.TrimSpace(fields["provider"]),
			ConfiguredOutputSampleRateHz:       atoi(fields["configured_output_sample_rate_hz"]),
			ProviderReportedOutputSampleRateHz: atoi(fields["provider_reported_output_sample_rate_hz"]),
			UsedOutputSampleRateHz:             used,
		}
		return pr
	}
	return nil
}
