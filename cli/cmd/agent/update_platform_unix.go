//go:build !windows

package main

import (
	"os"
	"syscall"
)

// lockUpdateFile takes a non-blocking exclusive lock on the update lock file.
func lockUpdateFile(f *os.File) error {
	return syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
}

func unlockUpdateFile(f *os.File) {
	_ = syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
}

// execReplace replaces the current process with the updated binary. On
// failure it returns so the caller continues running the current binary,
// matching the original syscall.Exec call-site behavior.
func execReplace(exePath string, args []string, env []string) {
	_ = syscall.Exec(exePath, args, env)
}
