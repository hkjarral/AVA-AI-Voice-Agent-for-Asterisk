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

// Open and validate the same object; never follow a swapped final symlink or
// block on a FIFO substituted for the regular journal file.
func openPendingDockerJournal(path string) (*os.File, error) {
	fd, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_CLOEXEC|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, &os.PathError{Op: "open pending Docker journal", Path: path, Err: err}
	}
	return os.NewFile(uintptr(fd), path), nil
}

func syncUpdateDirectory(path string) error {
	directory, err := os.Open(path)
	if err != nil {
		return err
	}
	defer directory.Close()
	return directory.Sync()
}

// execReplace replaces the current process with the updated binary. On
// failure it returns so the caller continues running the current binary,
// matching the original syscall.Exec call-site behavior.
func execReplace(exePath string, args []string, env []string) {
	_ = syscall.Exec(exePath, args, env)
}
