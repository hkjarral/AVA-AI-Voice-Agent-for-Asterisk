package health

import (
	"encoding/json"
	"fmt"
	"io"

	"github.com/fatih/color"
)

func (r *HealthResult) OutputJSON(w io.Writer) error {
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	return enc.Encode(r)
}

func (r *HealthResult) OutputText(w io.Writer) {
	// Color setup
	green := color.New(color.FgGreen, color.Bold).SprintFunc()
	yellow := color.New(color.FgYellow, color.Bold).SprintFunc()
	red := color.New(color.FgRed, color.Bold).SprintFunc()
	blue := color.New(color.FgBlue, color.Bold).SprintFunc()
	gray := color.New(color.FgHiBlack).SprintFunc()
	
	// Header
	fmt.Fprintln(w)
	fmt.Fprintln(w, blue("🩺 Asterisk AI Voice Agent - Health Check"))
	fmt.Fprintln(w, gray("══════════════════════════════════════════"))
	fmt.Fprintln(w)
	
	// Run checks and display
	for i, check := range r.Checks {
		// Status icon and color
		var statusIcon string
		var colorFn func(a ...interface{}) string
		
		switch check.Status {
		case StatusPass:
			statusIcon = "✅"
			colorFn = green
		case StatusWarn:
			statusIcon = "⚠️ "
			colorFn = yellow
		case StatusFail:
			statusIcon = "❌"
			colorFn = red
		case StatusInfo:
			statusIcon = "ℹ️ "
			colorFn = blue
		}
		
		// Print check result
		fmt.Fprintf(w, "[%d/%d] %-20s %s %s\n", 
			i+1, r.TotalCount, 
			check.Name+"...", 
			statusIcon, 
			colorFn(check.Message))
		
		// Print details if present
		if check.Details != "" {
			fmt.Fprintf(w, "     %s\n", gray(check.Details))
		}
		
		// Print remediation if present and failed/warned
		if check.Remediation != "" && (check.Status == StatusFail || check.Status == StatusWarn) {
			fmt.Fprintf(w, "     💡 %s\n", yellow(check.Remediation))
		}
	}
	
	// Summary separator
	fmt.Fprintln(w)
	fmt.Fprintln(w, gray("══════════════════════════════════════════"))
	
	// Summary
	fmt.Fprintln(w, blue("📊 HEALTH CHECK SUMMARY"))
	fmt.Fprintln(w, gray("══════════════════════════════════════════"))
	fmt.Fprintln(w)
	
	// Counts
	if r.CriticalCount > 0 {
		fmt.Fprintf(w, "%s FAILURES: %d\n", red("❌"), r.CriticalCount)
	}
	if r.WarnCount > 0 {
		fmt.Fprintf(w, "%s WARNINGS: %d\n", yellow("⚠️ "), r.WarnCount)
	}
	fmt.Fprintf(w, "%s PASS: %d/%d checks\n", green("✅"), r.PassCount, r.TotalCount)
	
	fmt.Fprintln(w)
	
	// Overall status
	if r.CriticalCount > 0 {
		fmt.Fprintln(w, red("❌ System has critical issues that need attention"))
	} else if r.WarnCount > 0 {
		fmt.Fprintln(w, yellow("⚠️  System is operational but has warnings"))
	} else {
		fmt.Fprintln(w, green("🎉 System is healthy and ready for calls!"))
	}
	
	fmt.Fprintln(w)
	
		// Next steps
		if r.CriticalCount > 0 || r.WarnCount > 0 {
			fmt.Fprintln(w, gray("Next steps:"))
			if r.CriticalCount > 0 {
				fmt.Fprintln(w, gray("  • Fix critical issues before making calls"))
				fmt.Fprintln(w, gray("  • Run: sudo ./preflight.sh --apply-fixes (to attempt auto-fix)"))
			}
			fmt.Fprintln(w, gray("  • Re-run: agent check"))
		}
	
	fmt.Fprintln(w)
}
