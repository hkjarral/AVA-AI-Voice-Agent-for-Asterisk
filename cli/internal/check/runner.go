package check

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"time"
)

type Runner struct {
	Verbose   bool
	Version   string
	BuildTime string
}

func NewRunner(verbose bool, version, buildTime string) *Runner {
	return &Runner{Verbose: verbose, Version: version, BuildTime: buildTime}
}

func (r *Runner) Run() (*Report, error) {
	rep := &Report{
		Version:   r.Version,
		BuildTime: r.BuildTime,
		Timestamp: time.Now(),
		Items:     []Item{},
	}

	// Host context (best-effort).
	rep.Items = append(rep.Items, r.checkHost())

	// Docker prerequisites.
	if item := r.checkDockerCLI(); item.Status == StatusFail {
		rep.Items = append(rep.Items, item)
		rep.finalizeCounts()
		return rep, errors.New("docker not available")
	} else {
		rep.Items = append(rep.Items, item)
	}
	rep.Items = append(rep.Items, r.checkDockerDaemon())
	rep.Items = append(rep.Items, r.checkCompose())

	// Container must exist for docker-exec probes.
	inspect, inspectItem := r.inspectContainer("ai_engine")
	rep.Items = append(rep.Items, inspectItem)
	if inspectItem.Status == StatusFail {
		rep.finalizeCounts()
		return rep, errors.New("ai_engine container not available")
	}

	rep.Items = append(rep.Items, r.checkNetworkMode(inspect))
	rep.Items = append(rep.Items, r.checkMounts(inspect))

	// Local AI server status (always reported; WARN if not running).
	localAIInspect, localAIItem := r.inspectOptionalContainer("local_ai_server")
	rep.Items = append(rep.Items, localAIItem)
	rep.Items = append(rep.Items, r.checkModelsMount(inspect, localAIInspect))

	// In-container probes (python-only; no curl).
	rep.Items = append(rep.Items, r.checkInContainerPaths())
	rep.Items = append(rep.Items, r.checkCallHistorySQLite())

	cfg, cfgItem := r.readEffectiveConfig()
	rep.Items = append(rep.Items, cfgItem)

	env, envItem := r.readEnvSummary()
	rep.Items = append(rep.Items, envItem)

	rep.Items = append(rep.Items, r.checkTransportCompatibility(cfg))
	rep.Items = append(rep.Items, r.checkAdvertiseHosts(cfg, env, inspect))

	ari, ariItem := r.probeARI(cfg, env)
	rep.Items = append(rep.Items, ariItem)
	rep.Items = append(rep.Items, r.dialplanGuidance(cfg, env, ari))

	rep.Items = append(rep.Items, r.bestEffortNetwork(env))

	rep.finalizeCounts()
	if rep.FailCount > 0 {
		return rep, errors.New("agent check failed")
	}
	if rep.WarnCount > 0 {
		return rep, nil
	}
	return rep, nil
}

func (r *Runner) checkHost() Item {
	host, _ := os.Hostname()
	kernel := runtime.GOOS + "/" + runtime.GOARCH
	if out, err := exec.Command("uname", "-r").Output(); err == nil {
		kernel = strings.TrimSpace(string(out))
	}

	return Item{
		Name:    "Host",
		Status:  StatusPass,
		Message: "Host context collected",
		Details: fmt.Sprintf("hostname=%s\nkernel=%s", host, kernel),
	}
}

func (r *Runner) checkDockerCLI() Item {
	if _, err := exec.LookPath("docker"); err != nil {
		return Item{
			Name:        "Docker CLI",
			Status:      StatusFail,
			Message:     "docker not found",
			Remediation: "Install Docker (see docs/INSTALLATION.md)",
		}
	}
	return Item{Name: "Docker CLI", Status: StatusPass, Message: "docker found"}
}

func (r *Runner) checkDockerDaemon() Item {
	cmd := exec.Command("docker", "info")
	if out, err := cmd.CombinedOutput(); err != nil {
		return Item{
			Name:        "Docker Daemon",
			Status:      StatusFail,
			Message:     "docker daemon not reachable",
			Details:     strings.TrimSpace(string(out)),
			Remediation: "Start Docker and re-run agent check",
		}
	}

	versionOut, _ := exec.Command("docker", "version", "--format", "{{.Server.Version}}").Output()
	version := strings.TrimSpace(string(versionOut))
	if version == "" {
		version = "unknown"
	}
	return Item{Name: "Docker Daemon", Status: StatusPass, Message: "running", Details: "server_version=" + version}
}

func (r *Runner) checkCompose() Item {
	out, err := exec.Command("docker", "compose", "version", "--short").CombinedOutput()
	if err != nil {
		return Item{
			Name:        "Docker Compose",
			Status:      StatusFail,
			Message:     "docker compose not available",
			Details:     strings.TrimSpace(string(out)),
			Remediation: "Install Docker Compose v2 plugin (see docs/INSTALLATION.md)",
		}
	}
	ver := strings.TrimSpace(string(out))
	return Item{Name: "Docker Compose", Status: StatusPass, Message: "available", Details: "version=" + strings.TrimPrefix(ver, "v")}
}

type containerInspect struct {
	ID   string `json:"Id"`
	Name string `json:"Name"`

	Config struct {
		Image  string            `json:"Image"`
		Labels map[string]string `json:"Labels"`
	} `json:"Config"`

	State struct {
		Status  string `json:"Status"`
		Running bool   `json:"Running"`
		Health  *struct {
			Status string `json:"Status"`
		} `json:"Health"`
	} `json:"State"`

	HostConfig struct {
		NetworkMode string `json:"NetworkMode"`
	} `json:"HostConfig"`

	Mounts []struct {
		Source      string `json:"Source"`
		Destination string `json:"Destination"`
		Mode        string `json:"Mode"`
		RW          bool   `json:"RW"`
	} `json:"Mounts"`
}

func (r *Runner) inspectContainer(name string) (*containerInspect, Item) {
	out, err := exec.Command("docker", "inspect", name).CombinedOutput()
	if err != nil {
		return nil, Item{
			Name:        "Container " + name,
			Status:      StatusFail,
			Message:     "not found (is docker compose up running?)",
			Details:     strings.TrimSpace(string(out)),
			Remediation: "Run: docker compose -p asterisk-ai-voice-agent up -d " + name,
		}
	}
	var arr []containerInspect
	if err := json.Unmarshal(out, &arr); err != nil || len(arr) == 0 {
		return nil, Item{Name: "Container " + name, Status: StatusFail, Message: "inspect parse failed", Details: errString(err)}
	}
	ci := arr[0]
	msg := "running"
	st := StatusPass
	if !ci.State.Running {
		msg = "not running"
		st = StatusFail
	}

	health := ""
	if ci.State.Health != nil && ci.State.Health.Status != "" {
		health = ci.State.Health.Status
	}

	details := []string{
		fmt.Sprintf("image=%s", ci.Config.Image),
		fmt.Sprintf("status=%s", ci.State.Status),
		fmt.Sprintf("network_mode=%s", ci.HostConfig.NetworkMode),
	}
	if health != "" {
		details = append(details, "health="+health)
	}

	return &ci, Item{
		Name:    "Container " + name,
		Status:  st,
		Message: msg,
		Details: strings.Join(details, "\n"),
	}
}

func (r *Runner) inspectOptionalContainer(name string) (*containerInspect, Item) {
	out, err := exec.Command("docker", "inspect", name).CombinedOutput()
	if err != nil {
		return nil, Item{
			Name:        "Container " + name,
			Status:      StatusWarn,
			Message:     "not found (optional)",
			Details:     strings.TrimSpace(string(out)),
			Remediation: "Start it when needed: docker compose -p asterisk-ai-voice-agent up -d " + name,
		}
	}
	var arr []containerInspect
	if err := json.Unmarshal(out, &arr); err != nil || len(arr) == 0 {
		return nil, Item{Name: "Container " + name, Status: StatusWarn, Message: "inspect parse failed", Details: errString(err)}
	}
	ci := arr[0]
	msg := "running"
	st := StatusPass
	remediation := ""
	if !ci.State.Running {
		msg = "not running (optional)"
		st = StatusWarn
		remediation = "Start it when needed: docker compose -p asterisk-ai-voice-agent up -d " + name
	}

	health := ""
	if ci.State.Health != nil && ci.State.Health.Status != "" {
		health = ci.State.Health.Status
	}

	details := []string{
		fmt.Sprintf("image=%s", ci.Config.Image),
		fmt.Sprintf("status=%s", ci.State.Status),
		fmt.Sprintf("network_mode=%s", ci.HostConfig.NetworkMode),
	}
	if health != "" {
		details = append(details, "health="+health)
	}

	return &ci, Item{
		Name:        "Container " + name,
		Status:      st,
		Message:     msg,
		Details:     strings.Join(details, "\n"),
		Remediation: remediation,
	}
}

func (r *Runner) checkNetworkMode(ci *containerInspect) Item {
	mode := strings.TrimSpace(ci.HostConfig.NetworkMode)
	if mode == "" {
		mode = "unknown"
	}
	if mode == "host" {
		return Item{Name: "Network Mode", Status: StatusPass, Message: "host networking", Details: "NetworkMode=host"}
	}
	// FYI: Non-host networking can be valid, but it requires correct port publishing and
	// an ASTERISK_HOST that is reachable from inside the container. Treat as skip (not warn)
	// and rely on ARI probe + mount checks for actionable failures.
	return Item{
		Name:        "Network Mode",
		Status:      StatusSkip,
		Message:     "non-host networking detected",
		Details:     "NetworkMode=" + mode,
		Remediation: "If using bridge networking, ensure required ports are published and ASTERISK_HOST is reachable from the container.",
	}
}

func (r *Runner) checkMounts(ci *containerInspect) Item {
	need := map[string]bool{
		"/app/data":           false,
		"/mnt/asterisk_media": false,
	}
	var details []string
	for _, m := range ci.Mounts {
		if _, ok := need[m.Destination]; ok {
			need[m.Destination] = true
		}
		if strings.HasPrefix(m.Destination, "/app/data") || strings.HasPrefix(m.Destination, "/mnt/asterisk_media") {
			details = append(details, fmt.Sprintf("%s <- %s (rw=%t)", m.Destination, m.Source, m.RW))
		}
	}
	missing := []string{}
	for dst, ok := range need {
		if !ok {
			missing = append(missing, dst)
		}
	}
	if len(missing) > 0 {
		return Item{
			Name:        "Mounts",
			Status:      StatusFail,
			Message:     "required mounts missing",
			Details:     strings.Join(details, "\n"),
			Remediation: "Ensure docker-compose mounts ./data:/app/data and ./asterisk_media:/mnt/asterisk_media",
		}
	}
	return Item{Name: "Mounts", Status: StatusPass, Message: "required mounts present", Details: strings.Join(details, "\n")}
}

func (r *Runner) checkModelsMount(engine *containerInspect, localAI *containerInspect) Item {
	hostRoot := ""
	for _, m := range engine.Mounts {
		if m.Destination == "/app/data" && strings.TrimSpace(m.Source) != "" {
			hostRoot = filepath.Dir(m.Source)
			break
		}
	}

	details := []string{}
	warnings := []string{}

	remoteDocker := dockerHostIsRemote()
	if remoteDocker {
		details = append(details, "host_models_validation=skipped (remote docker host)")
	} else if hostRoot == "" {
		warnings = append(warnings, "Could not infer host project root from ai_engine mounts")
	} else {
		modelsHost := filepath.Join(hostRoot, "models")
		if fi, err := os.Stat(modelsHost); err != nil || !fi.IsDir() {
			warnings = append(warnings, "Host models directory missing: "+modelsHost)
		} else {
			details = append(details, "host_models="+modelsHost)
			subDirs := []string{"stt", "tts", "llm", "kroko"}
			missing := []string{}
			for _, sub := range subDirs {
				p := filepath.Join(modelsHost, sub)
				if fi, err := os.Stat(p); err != nil || !fi.IsDir() {
					missing = append(missing, p)
				}
			}
			if len(missing) > 0 {
				warnings = append(warnings, "Missing subdirectories: "+strings.Join(missing, ", "))
			}
		}
	}

	engineHas := false
	engineSrc := ""
	for _, m := range engine.Mounts {
		if m.Destination == "/app/models" {
			engineHas = true
			engineSrc = m.Source
			break
		}
	}
	if engineHas {
		details = append(details, fmt.Sprintf("ai_engine_models=/app/models <- %s", engineSrc))
	} else {
		warnings = append(warnings, "ai_engine is missing /app/models mount")
	}

	if localAI != nil {
		localHas := false
		localSrc := ""
		for _, m := range localAI.Mounts {
			if m.Destination == "/app/models" {
				localHas = true
				localSrc = m.Source
				break
			}
		}
		if localHas {
			details = append(details, fmt.Sprintf("local_ai_server_models=/app/models <- %s", localSrc))
		} else {
			warnings = append(warnings, "local_ai_server is missing /app/models mount")
		}
	} else {
		details = append(details, "local_ai_server_models=(container not found)")
	}

	// In-container view: verify /app/models exists for ai_engine (best-effort; WARN on failure).
	script := `
import json, os
paths=["/app/models","/app/models/stt","/app/models/tts","/app/models/llm","/app/models/kroko"]
out=[]
for p in paths:
    out.append({"path": p, "exists": os.path.isdir(p)})
print(json.dumps({"paths": out}))
`
	raw, err := r.dockerExecPython(script)
	if err != nil {
		warnings = append(warnings, "ai_engine /app/models probe failed: "+err.Error())
	} else {
		var res struct {
			Paths []struct {
				Path   string `json:"path"`
				Exists bool   `json:"exists"`
			} `json:"paths"`
		}
		if e := json.Unmarshal(bytes.TrimSpace(raw), &res); e == nil {
			for _, p := range res.Paths {
				details = append(details, fmt.Sprintf("%s exists=%t", p.Path, p.Exists))
			}
			for _, p := range res.Paths {
				if !p.Exists {
					warnings = append(warnings, "ai_engine missing directory: "+p.Path)
				}
			}
		}
	}

	if len(warnings) > 0 {
		all := append([]string{}, warnings...)
		if len(details) > 0 {
			all = append(all, details...)
		}
		return Item{
			Name:        "Local AI Models",
			Status:      StatusWarn,
			Message:     "models mount has warnings",
			Details:     strings.Join(all, "\n"),
			Remediation: "Run: sudo ./preflight.sh --apply-fixes (creates ./models/* and fixes permissions).",
		}
	}

	return Item{Name: "Local AI Models", Status: StatusPass, Message: "models mount looks ok", Details: strings.Join(details, "\n")}
}

func dockerHostIsRemote() bool {
	host := strings.TrimSpace(os.Getenv("DOCKER_HOST"))
	if host == "" {
		return false
	}
	if strings.HasPrefix(host, "unix://") {
		return host != "unix:///var/run/docker.sock"
	}
	return true
}

func (r *Runner) dockerExecPython(script string) ([]byte, error) {
	cmd := exec.Command("docker", "exec", "-i", "ai_engine", "python", "-")
	cmd.Stdin = strings.NewReader(script)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return out, fmt.Errorf("docker exec python failed: %w\n%s", err, strings.TrimSpace(string(out)))
	}
	return out, nil
}

func (r *Runner) checkInContainerPaths() Item {
	script := `
import json, os, tempfile

def test_path(p):
    res = {"path": p, "exists": os.path.exists(p), "writable": False, "error": None}
    if not res["exists"]:
        return res
    try:
        fd, tmp = tempfile.mkstemp(prefix="agent_check_", dir=p)
        os.close(fd)
        os.remove(tmp)
        res["writable"] = True
    except Exception as e:
        res["error"] = str(e)
    return res

paths = [
    "/app/data",
    "/mnt/asterisk_media",
    "/mnt/asterisk_media/ai-generated",
]
print(json.dumps({"paths": [test_path(p) for p in paths]}))
`
	out, err := r.dockerExecPython(script)
	if err != nil {
		return Item{Name: "In-Container Paths", Status: StatusFail, Message: "probe failed", Details: err.Error()}
	}
	var res struct {
		Paths []struct {
			Path     string  `json:"path"`
			Exists   bool    `json:"exists"`
			Writable bool    `json:"writable"`
			Error    *string `json:"error"`
		} `json:"paths"`
	}
	if err := json.Unmarshal(bytes.TrimSpace(out), &res); err != nil {
		return Item{Name: "In-Container Paths", Status: StatusFail, Message: "invalid probe output", Details: string(out)}
	}
	var bad []string
	var details []string
	for _, p := range res.Paths {
		line := fmt.Sprintf("%s exists=%t writable=%t", p.Path, p.Exists, p.Writable)
		if p.Error != nil {
			line += " error=" + *p.Error
		}
		details = append(details, line)
		if !p.Exists || (p.Path == "/app/data" && !p.Writable) || (strings.Contains(p.Path, "/mnt/asterisk_media") && !p.Writable) {
			bad = append(bad, p.Path)
		}
	}
	if len(bad) > 0 {
		return Item{
			Name:        "In-Container Paths",
			Status:      StatusFail,
			Message:     "paths missing or not writable",
			Details:     strings.Join(details, "\n"),
			Remediation: "Run ./preflight.sh --apply-fixes (ensures ./data and media directories exist and are writable).",
		}
	}
	return Item{Name: "In-Container Paths", Status: StatusPass, Message: "paths look ok", Details: strings.Join(details, "\n")}
}

func (r *Runner) checkCallHistorySQLite() Item {
	script := `
import json, os, sqlite3

data_dir = "/app/data"
path = os.path.join(data_dir, ".call_history_sqlite_test.db")
res = {"db_dir": data_dir, "test_path": path, "ok": False, "error": None}
try:
    conn = sqlite3.connect(path, timeout=1.0)
    conn.execute("CREATE TABLE IF NOT EXISTS __agent_check (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    os.remove(path)
    res["ok"] = True
except Exception as e:
    res["error"] = str(e)

print(json.dumps(res))
`
	out, err := r.dockerExecPython(script)
	if err != nil {
		return Item{Name: "Call History DB", Status: StatusFail, Message: "sqlite test failed", Details: err.Error()}
	}
	var res struct {
		DBDir    string  `json:"db_dir"`
		TestPath string  `json:"test_path"`
		OK       bool    `json:"ok"`
		Error    *string `json:"error"`
	}
	if err := json.Unmarshal(bytes.TrimSpace(out), &res); err != nil {
		return Item{Name: "Call History DB", Status: StatusFail, Message: "invalid probe output", Details: string(out)}
	}
	if !res.OK {
		d := fmt.Sprintf("dir=%s\ntest=%s", res.DBDir, res.TestPath)
		if res.Error != nil {
			d += "\nerror=" + *res.Error
		}
		return Item{
			Name:        "Call History DB",
			Status:      StatusFail,
			Message:     "not writable (SQLite test failed)",
			Details:     d,
			Remediation: "Ensure ./data is mounted to /app/data and writable (see preflight.sh guidance).",
		}
	}
	return Item{
		Name:    "Call History DB",
		Status:  StatusPass,
		Message: "writable (SQLite test passed)",
		Details: "canonical_path=/app/data/call_history.db",
	}
}

type configSummary struct {
	AppName        string `json:"app_name"`
	AudioTransport string `json:"audio_transport"`
	ActivePipeline string `json:"active_pipeline"`
	DownstreamMode string `json:"downstream_mode"`
	AudioSocket    struct {
		Host   string `json:"host"`
		Port   int    `json:"port"`
		Format string `json:"format"`
	} `json:"audiosocket"`
	ExternalMedia struct {
		RTPHost    string   `json:"rtp_host"`
		RTPPort    int      `json:"rtp_port"`
		PortRange  string   `json:"port_range"`
		AllowedIPs []string `json:"allowed_remote_hosts"`
	} `json:"external_media"`
}

func (r *Runner) readEffectiveConfig() (*configSummary, Item) {
	script := `
import json, os
import yaml

path = "/app/config/ai-agent.yaml"
out = {"ok": False, "error": None, "summary": {}}
try:
    with open(path, "r") as f:
        cfg = yaml.safe_load(f) or {}
    asterisk = cfg.get("asterisk") or {}
    audiosocket = cfg.get("audiosocket") or {}
    external_media = cfg.get("external_media") or {}
    out["summary"] = {
        "app_name": (asterisk.get("app_name") or ""),
        "audio_transport": (cfg.get("audio_transport") or ""),
        "active_pipeline": (cfg.get("active_pipeline") or ""),
        "downstream_mode": (cfg.get("downstream_mode") or ""),
        "audiosocket": {
            "host": (audiosocket.get("host") or ""),
            "port": int(audiosocket.get("port") or 0),
            "format": (audiosocket.get("format") or ""),
        },
        "external_media": {
            "rtp_host": (external_media.get("rtp_host") or ""),
            "rtp_port": int(external_media.get("rtp_port") or 0),
            "port_range": (external_media.get("port_range") or ""),
            "allowed_remote_hosts": list(external_media.get("allowed_remote_hosts") or []),
        },
    }
    out["ok"] = True
except Exception as e:
    out["error"] = str(e)

print(json.dumps(out))
`
	raw, err := r.dockerExecPython(script)
	if err != nil {
		return nil, Item{Name: "Config", Status: StatusFail, Message: "cannot read /app/config/ai-agent.yaml", Details: err.Error()}
	}
	var res struct {
		OK      bool          `json:"ok"`
		Error   *string       `json:"error"`
		Summary configSummary `json:"summary"`
	}
	if err := json.Unmarshal(bytes.TrimSpace(raw), &res); err != nil {
		return nil, Item{Name: "Config", Status: StatusFail, Message: "invalid probe output", Details: string(raw)}
	}
	if !res.OK {
		msg := "failed to parse config"
		details := ""
		if res.Error != nil {
			details = *res.Error
		}
		return &res.Summary, Item{Name: "Config", Status: StatusFail, Message: msg, Details: details, Remediation: "Fix YAML syntax in config/ai-agent.yaml"}
	}
	details := []string{
		"app_name=" + emptyTo(res.Summary.AppName, "asterisk-ai-voice-agent"),
		"audio_transport=" + emptyTo(res.Summary.AudioTransport, "(unset)"),
		"active_pipeline=" + emptyTo(res.Summary.ActivePipeline, "(none)"),
		"downstream_mode=" + emptyTo(res.Summary.DownstreamMode, "(unset)"),
	}
	return &res.Summary, Item{Name: "Config", Status: StatusPass, Message: "loaded", Details: strings.Join(details, "\n")}
}

type envSummary struct {
	AsteriskHost             string `json:"ASTERISK_HOST"`
	AsteriskARIPort          string `json:"ASTERISK_ARI_PORT"`
	AsteriskARIScheme        string `json:"ASTERISK_ARI_SCHEME"`
	AsteriskARISSLVerify     string `json:"ASTERISK_ARI_SSL_VERIFY"`
	AsteriskAppName          string `json:"ASTERISK_APP_NAME"`
	CallHistoryDBPath        string `json:"CALL_HISTORY_DB_PATH"`
	CallHistoryEnabled       string `json:"CALL_HISTORY_ENABLED"`
	ExternalAdvertiseHost    string `json:"EXTERNAL_MEDIA_ADVERTISE_HOST"`
	AudioSocketAdvertiseHost string `json:"AUDIOSOCKET_ADVERTISE_HOST"`
	LocalWSURL               string `json:"LOCAL_WS_URL"`
}

func (r *Runner) readEnvSummary() (*envSummary, Item) {
	script := `
import json, os

keys = [
  "ASTERISK_HOST",
  "ASTERISK_ARI_PORT",
  "ASTERISK_ARI_SCHEME",
  "ASTERISK_ARI_SSL_VERIFY",
  "ASTERISK_APP_NAME",
  "CALL_HISTORY_DB_PATH",
  "CALL_HISTORY_ENABLED",
  "EXTERNAL_MEDIA_ADVERTISE_HOST",
  "AUDIOSOCKET_ADVERTISE_HOST",
  "LOCAL_WS_URL",
]

out = {k: (os.getenv(k, "") or "") for k in keys}
print(json.dumps(out))
`
	raw, err := r.dockerExecPython(script)
	if err != nil {
		return nil, Item{Name: "Env", Status: StatusWarn, Message: "cannot read env from container", Details: err.Error()}
	}
	var env envSummary
	if err := json.Unmarshal(bytes.TrimSpace(raw), &env); err != nil {
		return nil, Item{Name: "Env", Status: StatusWarn, Message: "invalid env probe output", Details: string(raw)}
	}

	details := []string{
		"ASTERISK_HOST=" + emptyTo(env.AsteriskHost, "(unset)"),
		"ASTERISK_ARI_PORT=" + emptyTo(env.AsteriskARIPort, "(unset)"),
		"ASTERISK_ARI_SCHEME=" + emptyTo(env.AsteriskARIScheme, "(unset)"),
		"ASTERISK_APP_NAME=" + emptyTo(env.AsteriskAppName, "(unset)"),
		"CALL_HISTORY_DB_PATH=" + emptyTo(env.CallHistoryDBPath, "(unset)"),
		"EXTERNAL_MEDIA_ADVERTISE_HOST=" + emptyTo(env.ExternalAdvertiseHost, "(unset)"),
		"AUDIOSOCKET_ADVERTISE_HOST=" + emptyTo(env.AudioSocketAdvertiseHost, "(unset)"),
		"LOCAL_WS_URL=" + emptyTo(env.LocalWSURL, "(unset)"),
	}
	return &env, Item{Name: "Env", Status: StatusPass, Message: "loaded (values redacted by design)", Details: strings.Join(details, "\n")}
}

func (r *Runner) checkTransportCompatibility(cfg *configSummary) Item {
	if cfg == nil {
		return Item{Name: "Transport Compatibility", Status: StatusSkip, Message: "config unavailable"}
	}

	transport := strings.ToLower(strings.TrimSpace(cfg.AudioTransport))
	pipeline := strings.TrimSpace(cfg.ActivePipeline)
	downstream := strings.ToLower(strings.TrimSpace(cfg.DownstreamMode))

	var warnings []string
	var fyi []string
	if pipeline != "" && pipeline != "null" && downstream == "stream" {
		fyi = append(fyi, "pipelines + downstream_mode=stream is supported but not most validated (prefer downstream_mode=file for GA stability)")
	}
	if strings.TrimSpace(cfg.AudioSocket.Format) != "" && strings.ToLower(strings.TrimSpace(cfg.AudioSocket.Format)) != "slin" {
		warnings = append(warnings, fmt.Sprintf("audiosocket.format=%q (validated baseline is slin)", cfg.AudioSocket.Format))
	}
	if transport != "" && transport != "audiosocket" && transport != "externalmedia" {
		return Item{
			Name:        "Transport Compatibility",
			Status:      StatusFail,
			Message:     "invalid audio_transport",
			Details:     fmt.Sprintf("audio_transport=%q", cfg.AudioTransport),
			Remediation: "Set audio_transport to audiosocket or externalmedia (see docs/Transport-Mode-Compatibility.md).",
		}
	}

	if len(warnings) == 0 && len(fyi) > 0 {
		return Item{
			Name:        "Transport Compatibility",
			Status:      StatusSkip,
			Message:     "FYI recommendations",
			Details:     strings.Join(fyi, "\n"),
			Remediation: "See docs/Transport-Mode-Compatibility.md",
		}
	}
	if len(warnings) > 0 {
		if len(fyi) > 0 {
			warnings = append(warnings, fyi...)
		}
		return Item{
			Name:        "Transport Compatibility",
			Status:      StatusWarn,
			Message:     "configuration has warnings",
			Details:     strings.Join(warnings, "\n"),
			Remediation: "See docs/Transport-Mode-Compatibility.md",
		}
	}
	return Item{Name: "Transport Compatibility", Status: StatusPass, Message: "looks compatible"}
}

func (r *Runner) checkAdvertiseHosts(cfg *configSummary, env *envSummary, ci *containerInspect) Item {
	if cfg == nil || env == nil || ci == nil {
		return Item{Name: "Advertise Hosts", Status: StatusSkip, Message: "insufficient data"}
	}

	var notes []string
	var remediation []string

	ariHost := strings.TrimSpace(env.AsteriskHost)
	networkMode := strings.TrimSpace(ci.HostConfig.NetworkMode)

	isLocalARI := ariHost == "" || ariHost == "127.0.0.1" || ariHost == "localhost"
	if !isLocalARI && strings.ToLower(networkMode) != "host" {
		if strings.TrimSpace(env.ExternalAdvertiseHost) == "" && strings.ToLower(strings.TrimSpace(cfg.AudioTransport)) == "externalmedia" {
			notes = append(notes, "EXTERNAL_MEDIA_ADVERTISE_HOST is not set (recommended for NAT/remote Asterisk)")
			remediation = append(remediation, "Set EXTERNAL_MEDIA_ADVERTISE_HOST to an IP Asterisk can reach (VPN/LAN/public IP).")
		}
		if strings.TrimSpace(env.AudioSocketAdvertiseHost) == "" && strings.ToLower(strings.TrimSpace(cfg.AudioTransport)) == "audiosocket" {
			notes = append(notes, "AUDIOSOCKET_ADVERTISE_HOST is not set (recommended for NAT/remote Asterisk)")
			remediation = append(remediation, "Set AUDIOSOCKET_ADVERTISE_HOST to an IP Asterisk can reach (VPN/LAN/public IP).")
		}
	}

	if !isLocalARI && strings.TrimSpace(cfg.ExternalMedia.RTPHost) == "127.0.0.1" {
		notes = append(notes, "external_media.rtp_host=127.0.0.1 may break remote Asterisk RTP (bind host should usually be 0.0.0.0)")
		remediation = append(remediation, "Set external_media.rtp_host: 0.0.0.0 and use EXTERNAL_MEDIA_ADVERTISE_HOST for remote reachability.")
	}

	if len(notes) == 0 {
		return Item{Name: "Advertise Hosts", Status: StatusPass, Message: "looks ok"}
	}
	return Item{
		Name:        "Advertise Hosts",
		Status:      StatusWarn,
		Message:     "NAT/remote alignment warnings",
		Details:     strings.Join(notes, "\n"),
		Remediation: strings.Join(remediation, " "),
	}
}

type ariProbe struct {
	OK              bool   `json:"ok"`
	Error           string `json:"error,omitempty"`
	URL             string `json:"url,omitempty"`
	StatusCode      int    `json:"status_code,omitempty"`
	AsteriskVersion string `json:"asterisk_version,omitempty"`
	AppName         string `json:"app_name,omitempty"`
	AppRegistered   bool   `json:"app_registered,omitempty"`
}

func (r *Runner) probeARI(cfg *configSummary, env *envSummary) (*ariProbe, Item) {
	if env == nil {
		return nil, Item{Name: "ARI", Status: StatusSkip, Message: "env unavailable"}
	}

	expectedApp := ""
	if cfg != nil && strings.TrimSpace(cfg.AppName) != "" {
		expectedApp = strings.TrimSpace(cfg.AppName)
	} else if strings.TrimSpace(env.AsteriskAppName) != "" {
		expectedApp = strings.TrimSpace(env.AsteriskAppName)
	} else {
		expectedApp = "asterisk-ai-voice-agent"
	}

	script := fmt.Sprintf(`
import base64, json, os, ssl, urllib.request

host = os.getenv("ASTERISK_HOST", "127.0.0.1").strip() or "127.0.0.1"
port = os.getenv("ASTERISK_ARI_PORT", "8088").strip() or "8088"
scheme = (os.getenv("ASTERISK_ARI_SCHEME", "http") or "http").strip()
ssl_verify_raw = (os.getenv("ASTERISK_ARI_SSL_VERIFY", "") or "").strip().lower()
ssl_verify = True
if ssl_verify_raw in ("0", "false", "no"):
    ssl_verify = False
user = os.getenv("ASTERISK_ARI_USERNAME", "").strip()
pw = os.getenv("ASTERISK_ARI_PASSWORD", "").strip()
expected_app = %q

ctx = None
if scheme == "https" and not ssl_verify:
    ctx = ssl._create_unverified_context()

def req(url):
    headers = {}
    if user and pw:
        token = base64.b64encode((user + ":" + pw).encode("utf-8")).decode("ascii")
        headers["Authorization"] = "Basic " + token
    r = urllib.request.Request(url, headers=headers, method="GET")
    return urllib.request.urlopen(r, context=ctx, timeout=3)

base = f"{scheme}://{host}:{port}"
out = {"ok": False, "url": base, "status_code": None, "asterisk_version": None, "app_name": expected_app, "app_registered": False, "error": None}
try:
    with req(base + "/ari/asterisk/info") as resp:
        out["status_code"] = resp.getcode()
        data = resp.read()
        try:
            j = json.loads(data.decode("utf-8"))
            out["asterisk_version"] = j.get("system", {}).get("version")
        except Exception:
            pass

    with req(base + "/ari/applications") as resp:
        data = resp.read()
        try:
            apps = json.loads(data.decode("utf-8"))
            for app in apps:
                if (app.get("name") or "") == expected_app:
                    out["app_registered"] = True
        except Exception:
            pass

    out["ok"] = True
except Exception as e:
    out["error"] = str(e)

print(json.dumps(out))
`, expectedApp)

	raw, err := r.dockerExecPython(script)
	if err != nil {
		return nil, Item{Name: "ARI", Status: StatusFail, Message: "probe failed", Details: err.Error()}
	}
	var probe ariProbe
	if err := json.Unmarshal(bytes.TrimSpace(raw), &probe); err != nil {
		return nil, Item{Name: "ARI", Status: StatusFail, Message: "invalid probe output", Details: string(raw)}
	}

	if !probe.OK {
		return &probe, Item{
			Name:        "ARI",
			Status:      StatusFail,
			Message:     "unreachable or auth failed (from inside ai_engine)",
			Details:     fmt.Sprintf("url=%s\nerror=%s", probe.URL, probe.Error),
			Remediation: "Check ASTERISK_HOST/ASTERISK_ARI_PORT/ASTERISK_ARI_USERNAME/ASTERISK_ARI_PASSWORD and network mode assumptions.",
		}
	}

	msg := "reachable"
	details := []string{"url=" + probe.URL}
	if probe.AsteriskVersion != "" {
		details = append(details, "asterisk_version="+probe.AsteriskVersion)
	}
	if probe.AppRegistered {
		details = append(details, "ari_app_registered=true")
	} else {
		details = append(details, "ari_app_registered=false")
		msg = "reachable but app not registered"
		return &probe, Item{
			Name:        "ARI",
			Status:      StatusWarn,
			Message:     msg,
			Details:     strings.Join(details, "\n"),
			Remediation: "If calls do not enter the agent, ensure dialplan routes to Stasis(" + expectedApp + ") and that ai_engine is connected to ARI.",
		}
	}

	return &probe, Item{Name: "ARI", Status: StatusPass, Message: msg, Details: strings.Join(details, "\n")}
}

func (r *Runner) dialplanGuidance(cfg *configSummary, env *envSummary, ari *ariProbe) Item {
	app := "asterisk-ai-voice-agent"
	if cfg != nil && strings.TrimSpace(cfg.AppName) != "" {
		app = strings.TrimSpace(cfg.AppName)
	} else if env != nil && strings.TrimSpace(env.AsteriskAppName) != "" {
		app = strings.TrimSpace(env.AsteriskAppName)
	}

	if ari == nil {
		return Item{Name: "Dialplan", Status: StatusSkip, Message: "ARI probe not available"}
	}

	if ari.AppRegistered {
		return Item{
			Name:    "Dialplan",
			Status:  StatusPass,
			Message: "ARI shows app is registered",
			Details: "expected_stasis_app=" + app,
		}
	}

	// We cannot read dialplan files directly when Asterisk is remote (or not mounted).
	// Provide copy/paste commands for the operator to run on the PBX.
	cmds := []string{
		"asterisk -rx \"ari show apps\"",
		fmt.Sprintf("grep -R \"Stasis(%s\" /etc/asterisk | head", app),
	}
	return Item{
		Name:        "Dialplan",
		Status:      StatusWarn,
		Message:     "verify dialplan routes into Stasis",
		Details:     "expected_stasis_app=" + app,
		Remediation: "On the PBX, run and paste output:\n  " + strings.Join(cmds, "\n  "),
	}
}

func (r *Runner) bestEffortNetwork(env *envSummary) Item {
	script := `
import json, socket, time

targets = [
  ("deb.debian.org", 443),
  ("api.openai.com", 443),
  ("api.deepgram.com", 443),
]

results = []
for host, port in targets:
    item = {"host": host, "port": port, "dns_ok": False, "tcp_ok": False, "error": None, "ms": None}
    try:
        socket.getaddrinfo(host, port)
        item["dns_ok"] = True
        t0 = time.time()
        s = socket.create_connection((host, port), timeout=2.0)
        s.close()
        item["tcp_ok"] = True
        item["ms"] = int((time.time() - t0) * 1000)
    except Exception as e:
        item["error"] = str(e)
    results.append(item)

print(json.dumps({"targets": results}))
`
	raw, err := r.dockerExecPython(script)
	if err != nil {
		return Item{Name: "Internet/DNS", Status: StatusSkip, Message: "probe unavailable", Details: err.Error()}
	}
	var res struct {
		Targets []struct {
			Host  string  `json:"host"`
			Port  int     `json:"port"`
			DNSOK bool    `json:"dns_ok"`
			TCPOK bool    `json:"tcp_ok"`
			MS    *int    `json:"ms"`
			Error *string `json:"error"`
		} `json:"targets"`
	}
	if err := json.Unmarshal(bytes.TrimSpace(raw), &res); err != nil {
		return Item{Name: "Internet/DNS", Status: StatusSkip, Message: "invalid probe output", Details: string(raw)}
	}

	var lines []string
	allOK := true
	for _, t := range res.Targets {
		line := fmt.Sprintf("%s:%d dns=%t tcp=%t", t.Host, t.Port, t.DNSOK, t.TCPOK)
		if t.MS != nil {
			line += fmt.Sprintf(" ms=%d", *t.MS)
		}
		if t.Error != nil && *t.Error != "" {
			line += " error=" + *t.Error
		}
		lines = append(lines, line)
		if !t.TCPOK {
			allOK = false
		}
	}

	if allOK {
		return Item{Name: "Internet/DNS", Status: StatusPass, Message: "reachable (best-effort)", Details: strings.Join(lines, "\n")}
	}
	// FYI only: never block or warn on internet reachability.
	return Item{
		Name:        "Internet/DNS",
		Status:      StatusSkip,
		Message:     "some endpoints unreachable (best-effort)",
		Details:     strings.Join(lines, "\n"),
		Remediation: "If docker builds fail with DNS errors, see docs/TROUBLESHOOTING_GUIDE.md#0-docker-build-fails-apt-get--dns",
	}
}

func emptyTo(v, fallback string) string {
	v = strings.TrimSpace(v)
	if v == "" {
		return fallback
	}
	return v
}

func errString(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}
