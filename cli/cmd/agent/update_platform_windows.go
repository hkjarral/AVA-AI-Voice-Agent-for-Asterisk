//go:build windows

package main

import (
	"errors"
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

// Windows lacks O_NOFOLLOW. Validate the opened object's identity before any
// read, and reject reparse/symlink entries rather than reading their targets.
func openPendingDockerJournal(path string) (*os.File, error) {
	before, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if !before.Mode().IsRegular() {
		return nil, errors.New("pending Docker journal must be a regular file")
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	after, err := file.Stat()
	if err != nil || !after.Mode().IsRegular() || !os.SameFile(before, after) {
		file.Close()
		return nil, errors.New("pending Docker journal changed while opening")
	}
	return file, nil
}

// Directory fsync is supported on Unix updater hosts. Windows retains the
// flushed-file/rename behavior; Go's directory handles cannot be flushed there.
func syncUpdateDirectory(path string) error { return nil }

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
