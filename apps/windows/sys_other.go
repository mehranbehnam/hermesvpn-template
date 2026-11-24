//go:build !windows

package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
)

const isWindows = false

func hideWindow(cmd *exec.Cmd) {}

func gracefulStop(cmd *exec.Cmd) { _ = cmd.Process.Signal(syscall.SIGTERM) }

func killStaleCore(dir string) {
	b, err := os.ReadFile(filepath.Join(dir, "xray.pid"))
	if err != nil {
		return
	}
	if pid, err := strconv.Atoi(strings.TrimSpace(string(b))); err == nil {
		if p, err := os.FindProcess(pid); err == nil {
			_ = p.Kill()
		}
	}
}

func setSystemProxy(addr string) *proxyState    { return &proxyState{} }
func restoreSystemProxy(p *proxyState)           {}
func setScreenshotProtection(title string, on bool) {}
func setAutostart(on bool) error                 { return nil }
func autostartEnabled() bool                     { return false }
func registerProtocol()                          {}
func openURL(u string) error                     { return exec.Command("xdg-open", u).Start() }
func listProcesses() []ProcInfo                  { return nil }
func isAdmin() bool                              { return os.Geteuid() == 0 }
func relaunchAsAdmin() error                     { return nil }

func tunCounters() (down, up int64, ok bool) { return 0, 0, false }
