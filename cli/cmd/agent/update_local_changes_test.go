package main

import "testing"

func TestRequestedLocalChangesPolicyValidation(t *testing.T) {
	old := updateLocalChanges
	t.Cleanup(func() { updateLocalChanges = old })

	for _, tc := range []struct {
		value string
		want  localChangesPolicy
	}{
		{"", localChangesAsk},
		{"ask", localChangesAsk},
		{"retain", localChangesRetain},
		{"overwrite", localChangesOverwrite},
		{"abort", localChangesAbort},
		{" RETAIN ", localChangesRetain},
	} {
		updateLocalChanges = tc.value
		got, err := requestedLocalChangesPolicy()
		if err != nil {
			t.Fatalf("requestedLocalChangesPolicy(%q) unexpected error: %v", tc.value, err)
		}
		if got != tc.want {
			t.Fatalf("requestedLocalChangesPolicy(%q) = %q, want %q", tc.value, got, tc.want)
		}
	}
}

func TestRequestedLocalChangesPolicyRejectsUnknownValue(t *testing.T) {
	old := updateLocalChanges
	t.Cleanup(func() { updateLocalChanges = old })

	updateLocalChanges = "maybe"
	if _, err := requestedLocalChangesPolicy(); err == nil {
		t.Fatal("expected invalid local-changes value to fail")
	}
}
