package check

import (
	"strings"
	"testing"
)

func TestWebSocketSecretReferenceDefaultsAndOverrides(t *testing.T) {
	if websocketPasswordEnv(nil) != "ASTERISK_MEDIA_WS_PASSWORD" {
		t.Fatal("missing config must retain the default secret reference")
	}
	cfg := &configSummary{}
	cfg.WebSocketMedia.PasswordEnv = " CUSTOM_MEDIA_PASSWORD "
	if websocketPasswordEnv(cfg) != "CUSTOM_MEDIA_PASSWORD" {
		t.Fatal("custom secret reference was ignored")
	}
}

func TestWebSocketAdvertiseCheckUsesConfiguredSecretAndHonorsLocalAuthOptOut(t *testing.T) {
	runner := &Runner{}
	cfg := &configSummary{AudioTransport: "websocket"}
	cfg.WebSocketMedia.BindHost = "127.0.0.1"
	cfg.WebSocketMedia.AdvertiseHost = "127.0.0.1"
	cfg.WebSocketMedia.AuthRequired = true
	cfg.WebSocketMedia.PasswordEnv = "CUSTOM_MEDIA_PASSWORD"
	env := &envSummary{AsteriskHost: "127.0.0.1"}
	container := &containerInspect{}
	missing := runner.checkAdvertiseHosts(cfg, env, container)
	if missing.Status != StatusWarn || !strings.Contains(missing.Details, "CUSTOM_MEDIA_PASSWORD") {
		t.Fatalf("expected configured-secret warning: %+v", missing)
	}
	if strings.Contains(missing.Details, "ASTERISK_MEDIA_WS_PASSWORD") {
		t.Fatal("warning incorrectly reports the default secret")
	}
	env.AsteriskMediaWSSecretPresent = true
	if runner.checkAdvertiseHosts(cfg, env, container).Status != StatusPass {
		t.Fatal("present configured secret must pass")
	}
	cfg.WebSocketMedia.AuthRequired = false
	env.AsteriskMediaWSSecretPresent = false
	if runner.checkAdvertiseHosts(cfg, env, container).Status != StatusPass {
		t.Fatal("permitted local auth opt-out must not require a secret")
	}
}

func TestWebSocketTransportCompatibilityAcceptsEveryWireCodec(t *testing.T) {
	for _, codec := range []string{"ulaw", "alaw", "slin", "slin16"} {
		t.Run(codec, func(t *testing.T) {
			cfg := &configSummary{AudioTransport: "websocket"}
			cfg.WebSocketMedia.ConnectionMode = "asterisk_outbound"
			cfg.WebSocketMedia.ControlFormat = "json"
			cfg.WebSocketMedia.FallbackFormat = codec
			cfg.WebSocketMedia.BindHost = "127.0.0.1"
			cfg.WebSocketMedia.Port = 8787
			cfg.WebSocketMedia.Path = "/media"
			cfg.WebSocketMedia.AuthRequired = true
			cfg.WebSocketMedia.PasswordEnv = "ASTERISK_MEDIA_WS_PASSWORD"
			result := (&Runner{}).checkTransportCompatibility(cfg)
			if result.Status == StatusFail || result.Status == StatusWarn {
				t.Fatalf("valid WebSocket codec rejected: %+v", result)
			}
		})
	}
}
