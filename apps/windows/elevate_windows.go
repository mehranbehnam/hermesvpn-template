//go:build windows

package main

import (
	"encoding/xml"
	"errors"
	"os"
	"os/user"
	"path/filepath"
	"strings"
	"unicode/utf16"

	"golang.org/x/sys/windows"
)

// The exe itself runs "asInvoker" (no UAC shield on its icon). TUN needs
// admin rights, so the first elevated run registers an on-demand Task
// Scheduler task with highest privileges; every later launch just starts that
// task — elevated, with no UAC prompt. Same approach as many desktop VPN apps.

const launcherTask = "HermesVPN Connect Launcher"

func xmlEsc(s string) string {
	var b strings.Builder
	_ = xml.EscapeText(&b, []byte(s))
	return b.String()
}

// writeTaskXML writes a Task Scheduler definition (UTF-16 as schtasks expects).
// Unlike plain `schtasks /Create`, it removes the 72-hour run limit and the
// "don't start on battery" default, and keeps normal process priority.
func writeTaskXML(path, args string, logon bool) error {
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	triggers := ""
	if logon {
		uid := ""
		if u, err := user.Current(); err == nil {
			uid = "<UserId>" + xmlEsc(u.Username) + "</UserId>"
		}
		triggers = "<LogonTrigger><Enabled>true</Enabled>" + uid + "<Delay>PT5S</Delay></LogonTrigger>"
	}
	x := `<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>HermesVPN Connect</Description></RegistrationInfo>
  <Triggers>` + triggers + `</Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>Parallel</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>4</Priority>
  </Settings>
  <Actions Context="Author"><Exec><Command>` + xmlEsc(exe) + `</Command><Arguments>` + xmlEsc(args) + `</Arguments><WorkingDirectory>` + xmlEsc(filepath.Dir(exe)) + `</WorkingDirectory></Exec></Actions>
</Task>`
	u := utf16.Encode([]rune(x))
	buf := make([]byte, 2+len(u)*2)
	buf[0], buf[1] = 0xFF, 0xFE
	for i, v := range u {
		buf[2+i*2] = byte(v)
		buf[3+i*2] = byte(v >> 8)
	}
	return os.WriteFile(path, buf, 0o600)
}

func createTask(name, args string, logon bool) error {
	p := filepath.Join(dataDir(), "task.xml")
	if err := writeTaskXML(p, args, logon); err != nil {
		return err
	}
	defer os.Remove(p)
	return runHidden("schtasks", "/Create", "/TN", name, "/XML", p, "/F")
}

// ensureLauncherTask is called from an elevated instance; it (re)registers the
// launcher so it always points at the current exe path.
func ensureLauncherTask() {
	if isAdmin() {
		_ = createTask(launcherTask, "--elevated", false)
	}
}

func removeLauncherTask() { _ = runHidden("schtasks", "/Delete", "/TN", launcherTask, "/F") }

// launchElevated is called by a non-elevated start. It returns true when an
// elevated copy has been started (so this process should exit).
func launchElevated() bool {
	if runHidden("schtasks", "/Run", "/TN", launcherTask) == nil {
		return true
	}
	// no task yet (first run / portable exe): one UAC prompt
	err := relaunchAsAdmin()
	if err == nil {
		return true
	}
	if errors.Is(err, windows.ERROR_CANCELLED) {
		return false // user said no: run without admin (system-proxy mode)
	}
	return false
}
