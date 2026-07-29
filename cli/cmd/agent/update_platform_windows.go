//go:build windows

package main

import (
	"os"
	"os/exec"

	"golang.org/x/sys/windows"
)

// lockUpdateFile takes a non-blocking exclusive lock on the update lock file.
func lockUpdateFile(f *os.File) error {
	ol := new(windows.Overlapped)
	return windows.LockFileEx(windows.Handle(f.Fd()),
		windows.LOCKFILE_EXCLUSIVE_LOCK|windows.LOCKFILE_FAIL_IMMEDIATELY, 0, 1, 0, ol)
}

func unlockUpdateFile(f *os.File) {
	ol := new(windows.Overlapped)
	_ = windows.UnlockFileEx(windows.Handle(f.Fd()), 0, 1, 0, ol)
}

// execReplace approximates Unix exec semantics on Windows, which has no
// execve equivalent: run the updated binary as a child and exit with its
// status. If the child cannot be started, return so the caller continues
// running the current binary (same as a failed syscall.Exec).
func execReplace(exePath string, args []string, env []string) {
	cmd := exec.Command(exePath, args[1:]...)
	cmd.Env = env
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	err := cmd.Run()
	if err == nil {
		os.Exit(0)
	}
	if exitErr, ok := err.(*exec.ExitError); ok {
		os.Exit(exitErr.ExitCode())
	}
}
