package check

import (
	"encoding/json"
	"fmt"
	"io"
	"time"

	"github.com/fatih/color"
)

type Status string

const (
	StatusPass Status = "pass"
	StatusWarn Status = "warn"
	StatusFail Status = "fail"
	StatusSkip Status = "skip"
)

type Item struct {
	Name        string `json:"name"`
	Status      Status `json:"status"`
	Message     string `json:"message"`
	Details     string `json:"details,omitempty"`
	Remediation string `json:"remediation,omitempty"`
}

type Report struct {
	Version   string    `json:"version"`
	BuildTime string    `json:"build_time"`
	Timestamp time.Time `json:"timestamp"`

	Items []Item `json:"items"`

	PassCount int `json:"pass_count"`
	WarnCount int `json:"warn_count"`
	FailCount int `json:"fail_count"`
	SkipCount int `json:"skip_count"`
	Total     int `json:"total"`
}

func (r *Report) finalizeCounts() {
	r.PassCount, r.WarnCount, r.FailCount, r.SkipCount = 0, 0, 0, 0
	for _, item := range r.Items {
		switch item.Status {
		case StatusPass:
			r.PassCount++
		case StatusWarn:
			r.WarnCount++
		case StatusFail:
			r.FailCount++
		case StatusSkip:
			r.SkipCount++
		}
	}
	r.Total = len(r.Items)
}

func (r *Report) OutputJSON(w io.Writer) error {
	r.finalizeCounts()
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	return enc.Encode(r)
}

func (r *Report) OutputText(w io.Writer) {
	r.finalizeCounts()

	green := color.New(color.FgGreen, color.Bold).SprintFunc()
	yellow := color.New(color.FgYellow, color.Bold).SprintFunc()
	red := color.New(color.FgRed, color.Bold).SprintFunc()
	blue := color.New(color.FgBlue, color.Bold).SprintFunc()
	gray := color.New(color.FgHiBlack).SprintFunc()

	fmt.Fprintln(w)
	headerVersion := r.Version
	if headerVersion == "" {
		headerVersion = "unknown"
	}
	fmt.Fprintln(w, blue(fmt.Sprintf("Asterisk AI Voice Agent - agent check (%s)", headerVersion)))
	fmt.Fprintln(w, gray("══════════════════════════════════════════"))
	fmt.Fprintf(w, "%s %s\n", gray("Timestamp:"), r.Timestamp.Format(time.RFC3339))
	if r.Version != "" {
		fmt.Fprintf(w, "%s %s\n", gray("CLI Version:"), r.Version)
	}
	if r.BuildTime != "" && r.BuildTime != "unknown" {
		fmt.Fprintf(w, "%s %s\n", gray("Build:"), r.BuildTime)
	}
	fmt.Fprintln(w)

	for i, item := range r.Items {
		var icon string
		var paint func(a ...interface{}) string
		switch item.Status {
		case StatusPass:
			icon = "✅"
			paint = green
		case StatusWarn:
			icon = "⚠️ "
			paint = yellow
		case StatusFail:
			icon = "❌"
			paint = red
		case StatusSkip:
			icon = "⏭️ "
			paint = blue
		default:
			icon = "•"
			paint = blue
		}

		fmt.Fprintf(w, "[%d/%d] %-26s %s %s\n", i+1, r.Total, item.Name+"...", icon, paint(item.Message))
		if item.Details != "" {
			fmt.Fprintf(w, "      %s\n", gray(item.Details))
		}
		if item.Remediation != "" && (item.Status == StatusFail || item.Status == StatusWarn) {
			fmt.Fprintf(w, "      %s %s\n", yellow("Remediation:"), item.Remediation)
		}
	}

	fmt.Fprintln(w)
	fmt.Fprintln(w, gray("══════════════════════════════════════════"))
	fmt.Fprintln(w, blue("Summary"))
	fmt.Fprintf(w, "%s %d  %s %d  %s %d  %s %d  (%d total)\n",
		green("PASS"), r.PassCount,
		yellow("WARN"), r.WarnCount,
		red("FAIL"), r.FailCount,
		blue("SKIP"), r.SkipCount,
		r.Total,
	)

	if r.FailCount > 0 {
		fmt.Fprintln(w, red("Overall: FAIL (critical issues detected)"))
	} else if r.WarnCount > 0 {
		fmt.Fprintln(w, yellow("Overall: WARN (system operational with warnings)"))
	} else {
		fmt.Fprintln(w, green("Overall: PASS (system looks healthy)"))
	}
	fmt.Fprintln(w)
}
