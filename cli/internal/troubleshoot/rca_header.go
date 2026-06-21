package troubleshoot

import (
	"strconv"
	"strings"
)

// RCAHeader is a log-derived snapshot emitted by ai_engine (RCA_CALL_START).
// It is intentionally flat so it can be parsed from both JSON and console logs.
type RCAHeader struct {
	CallID       string `json:"call_id"`
	CallerName   string `json:"caller_name,omitempty"`
	CallerNumber string `json:"caller_number,omitempty"`
	CalledNumber string `json:"called_number,omitempty"`

	ContextName  string `json:"context_name,omitempty"`
	ProviderName string `json:"provider_name,omitempty"`
	PipelineName string `json:"pipeline_name,omitempty"`

	AudioTransport string `json:"audio_transport,omitempty"`
	DownstreamMode string `json:"downstream_mode,omitempty"`

	TransportProfileEncoding   string `json:"tp_encoding,omitempty"`
	TransportProfileSampleRate int    `json:"tp_sample_rate,omitempty"`
	TransportProfileSource     string `json:"tp_source,omitempty"`

	AudioSocketFormat          string `json:"audiosocket_format,omitempty"`
	AudioSocketHost            string `json:"audiosocket_host,omitempty"`
	AudioSocketPort            int    `json:"audiosocket_port,omitempty"`
	ExternalMediaCodec         string `json:"external_media_codec,omitempty"`
	ExternalMediaRTPHost       string `json:"external_media_rtp_host,omitempty"`
	ExternalMediaRTPPort       int    `json:"external_media_rtp_port,omitempty"`
	ExternalMediaAdvertiseHost string `json:"external_media_advertise_host,omitempty"`

	StreamingSampleRate     int `json:"streaming_sample_rate,omitempty"`
	StreamingJitterBufferMs int `json:"streaming_jitter_buffer_ms,omitempty"`
	StreamingMinStartMs     int `json:"streaming_min_start_ms,omitempty"`
	StreamingLowWatermarkMs int `json:"streaming_low_watermark_ms,omitempty"`

	VADWebRTCAggressiveness int     `json:"vad_webrtc_aggressiveness,omitempty"`
	VADConfidenceThreshold  float64 `json:"vad_confidence_threshold,omitempty"`
	VADEnergyThreshold      int     `json:"vad_energy_threshold,omitempty"`
	VADEnhancedEnabled      bool    `json:"vad_enhanced_enabled,omitempty"`

	BargeInPostTTSEndProtectionMs int `json:"barge_in_post_tts_end_protection_ms,omitempty"`

	// Provider audio settings snapshot (provider config, log-derived).
	ProviderInputEncoding             string `json:"provider_input_encoding,omitempty"`
	ProviderInputSampleRateHz         int    `json:"provider_input_sample_rate_hz,omitempty"`
	ProviderProviderInputEncoding     string `json:"provider_provider_input_encoding,omitempty"`
	ProviderProviderInputSampleRateHz int    `json:"provider_provider_input_sample_rate_hz,omitempty"`
	ProviderOutputEncoding            string `json:"provider_output_encoding,omitempty"`
	ProviderOutputSampleRateHz        int    `json:"provider_output_sample_rate_hz,omitempty"`
	ProviderTargetEncoding            string `json:"provider_target_encoding,omitempty"`
	ProviderTargetSampleRateHz        int    `json:"provider_target_sample_rate_hz,omitempty"`
}

func ExtractRCAHeader(logData string) *RCAHeader {
	lines := strings.Split(logData, "\n")
	for _, line := range lines {
		_, event, fields, ok := parseLogLine(line)
		if !ok {
			continue
		}
		if strings.TrimSpace(event) != "RCA_CALL_START" {
			continue
		}
		h := &RCAHeader{}
		h.CallID = fields["call_id"]
		h.CallerNumber = fields["caller_number"]
		h.CalledNumber = fields["called_number"]
		h.CallerName = fields["caller_name"]
		h.ContextName = fields["context_name"]
		h.ProviderName = fields["provider_name"]
		h.PipelineName = fields["pipeline_name"]
		h.AudioTransport = fields["audio_transport"]
		h.DownstreamMode = fields["downstream_mode"]
		h.TransportProfileEncoding = fields["tp_encoding"]
		h.TransportProfileSource = fields["tp_source"]
		h.AudioSocketFormat = fields["audiosocket_format"]
		h.AudioSocketHost = fields["audiosocket_host"]
		h.ExternalMediaCodec = fields["external_media_codec"]
		h.ExternalMediaRTPHost = fields["external_media_rtp_host"]
		h.ExternalMediaAdvertiseHost = fields["external_media_advertise_host"]

		h.TransportProfileSampleRate = atoi(fields["tp_sample_rate"])
		h.StreamingSampleRate = atoi(fields["streaming_sample_rate"])
		h.StreamingJitterBufferMs = atoi(fields["streaming_jitter_buffer_ms"])
		h.StreamingMinStartMs = atoi(fields["streaming_min_start_ms"])
		h.StreamingLowWatermarkMs = atoi(fields["streaming_low_watermark_ms"])
		h.AudioSocketPort = atoi(fields["audiosocket_port"])
		h.ExternalMediaRTPPort = atoi(fields["external_media_rtp_port"])

		h.VADWebRTCAggressiveness = atoi(fields["vad_webrtc_aggressiveness"])
		h.VADConfidenceThreshold = atof(fields["vad_confidence_threshold"])
		h.VADEnergyThreshold = atoi(fields["vad_energy_threshold"])
		h.VADEnhancedEnabled = atob(fields["vad_enhanced_enabled"])

		h.BargeInPostTTSEndProtectionMs = atoi(fields["barge_in_post_tts_end_protection_ms"])

		h.ProviderInputEncoding = fields["provider_input_encoding"]
		h.ProviderInputSampleRateHz = atoi(fields["provider_input_sample_rate_hz"])
		h.ProviderProviderInputEncoding = fields["provider_provider_input_encoding"]
		h.ProviderProviderInputSampleRateHz = atoi(fields["provider_provider_input_sample_rate_hz"])
		h.ProviderOutputEncoding = fields["provider_output_encoding"]
		h.ProviderOutputSampleRateHz = atoi(fields["provider_output_sample_rate_hz"])
		h.ProviderTargetEncoding = fields["provider_target_encoding"]
		h.ProviderTargetSampleRateHz = atoi(fields["provider_target_sample_rate_hz"])
		return h
	}
	return nil
}

func atoi(s string) int {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0
	}
	i, _ := strconv.Atoi(s)
	return i
}

func atof(s string) float64 {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0
	}
	f, _ := strconv.ParseFloat(s, 64)
	return f
}

func atob(s string) bool {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "1", "true", "yes", "y", "on":
		return true
	default:
		return false
	}
}
