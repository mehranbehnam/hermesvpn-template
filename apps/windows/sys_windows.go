//go:build windows

package main

import (
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"unsafe"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

const isWindows = true

const createNoWindow = 0x08000000

func hideWindow(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
}

func gracefulStop(cmd *exec.Cmd) { _ = cmd.Process.Kill() }

func processImagePath(pid uint32) string {
	h, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, pid)
	if err != nil {
		return ""
	}
	defer windows.CloseHandle(h)
	buf := make([]uint16, 1024)
	n := uint32(len(buf))
	if err := windows.QueryFullProcessImageName(h, 0, &buf[0], &n); err != nil {
		return ""
	}
	return windows.UTF16ToString(buf[:n])
}

// killStaleCore kills an xray left over from a crashed session, but only if
// it is *our* xray.exe (same folder) — never another app's.
func killStaleCore(dir string) {
	kill := func(pid int) {
		path := processImagePath(uint32(pid))
		if path == "" || !strings.EqualFold(filepath.Clean(filepath.Dir(path)), filepath.Clean(dir)) {
			return
		}
		if p, err := os.FindProcess(pid); err == nil {
			_ = p.Kill()
			_, _ = p.Wait()
		}
	}
	if b, err := os.ReadFile(filepath.Join(dir, "xray.pid")); err == nil {
		if pid, err := strconv.Atoi(strings.TrimSpace(string(b))); err == nil {
			kill(pid)
		}
	}
	// also any other leftover xray.exe from our folder (e.g. app was force-closed)
	snap, err := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPPROCESS, 0)
	if err != nil {
		return
	}
	defer windows.CloseHandle(snap)
	var pe windows.ProcessEntry32
	pe.Size = uint32(unsafe.Sizeof(pe))
	for err = windows.Process32First(snap, &pe); err == nil; err = windows.Process32Next(snap, &pe) {
		if strings.EqualFold(windows.UTF16ToString(pe.ExeFile[:]), "xray.exe") {
			kill(int(pe.ProcessID))
		}
	}
}

const inetKey = `Software\Microsoft\Windows\CurrentVersion\Internet Settings`

var (
	wininet              = windows.NewLazySystemDLL("wininet.dll")
	procInternetSetOptio = wininet.NewProc("InternetSetOptionW")
	user32               = windows.NewLazySystemDLL("user32.dll")
	procFindWindow       = user32.NewProc("FindWindowW")
	procSetAffinity      = user32.NewProc("SetWindowDisplayAffinity")
)

func refreshInternetSettings() {
	procInternetSetOptio.Call(0, 39, 0, 0) // INTERNET_OPTION_SETTINGS_CHANGED
	procInternetSetOptio.Call(0, 37, 0, 0) // INTERNET_OPTION_REFRESH
}

func setSystemProxy(addr string) *proxyState {
	k, err := registry.OpenKey(registry.CURRENT_USER, inetKey, registry.QUERY_VALUE|registry.SET_VALUE)
	if err != nil {
		return nil
	}
	defer k.Close()
	st := &proxyState{}
	if v, _, err := k.GetIntegerValue("ProxyEnable"); err == nil {
		st.Enable = uint32(v)
	}
	st.Server, _, _ = k.GetStringValue("ProxyServer")
	st.Override, _, _ = k.GetStringValue("ProxyOverride")
	if v, _, err := k.GetStringValue("AutoConfigURL"); err == nil {
		st.AutoURL, st.HadAuto = v, true
		_ = k.DeleteValue("AutoConfigURL")
	}
	_ = k.SetDWordValue("ProxyEnable", 1)
	_ = k.SetStringValue("ProxyServer", addr)
	_ = k.SetStringValue("ProxyOverride", "localhost;127.*;10.*;172.16.*;172.17.*;172.18.*;172.19.*;172.2*;172.30.*;172.31.*;192.168.*;<local>")
	refreshInternetSettings()
	return st
}

func restoreSystemProxy(st *proxyState) {
	k, err := registry.OpenKey(registry.CURRENT_USER, inetKey, registry.SET_VALUE)
	if err != nil {
		return
	}
	defer k.Close()
	_ = k.SetDWordValue("ProxyEnable", st.Enable)
	if st.Server == "" {
		_ = k.DeleteValue("ProxyServer")
	} else {
		_ = k.SetStringValue("ProxyServer", st.Server)
	}
	if st.Override != "" {
		_ = k.SetStringValue("ProxyOverride", st.Override)
	}
	if st.HadAuto {
		_ = k.SetStringValue("AutoConfigURL", st.AutoURL)
	}
	refreshInternetSettings()
}

// setScreenshotProtection = FLAG_SECURE on Android: the window shows black in
// screenshots / screen recordings (WDA_EXCLUDEFROMCAPTURE, Windows 10 2004+).
func setScreenshotProtection(title string, on bool) {
	t, _ := windows.UTF16PtrFromString(title)
	hwnd, _, _ := procFindWindow.Call(0, uintptr(unsafe.Pointer(t)))
	if hwnd == 0 {
		return
	}
	var aff uintptr
	if on {
		aff = 0x11
	}
	r, _, _ := procSetAffinity.Call(hwnd, aff)
	if r == 0 && on {
		procSetAffinity.Call(hwnd, 0x01) // WDA_MONITOR fallback on older builds
	}
}

const taskName = "HermesVPN Connect"

func runHidden(name string, args ...string) error {
	c := exec.Command(name, args...)
	hideWindow(c)
	return c.Run()
}

// The app runs elevated (TUN needs it); the Run key cannot start elevated apps,
// so autostart uses a logon task with highest privileges instead.
func setAutostart(on bool) error {
	if !on {
		return runHidden("schtasks", "/Delete", "/TN", taskName, "/F")
	}
	return createTask(taskName, "--autostart", true)
}

func autostartEnabled() bool { return runHidden("schtasks", "/Query", "/TN", taskName) == nil }

// registerProtocol makes hermesvpn://… links (activation links) open this app.
func registerProtocol() {
	exe, err := os.Executable()
	if err != nil {
		return
	}
	k, _, err := registry.CreateKey(registry.CURRENT_USER, `Software\Classes\hermesvpn`, registry.ALL_ACCESS)
	if err != nil {
		return
	}
	_ = k.SetStringValue("", "URL:HermesVPN")
	_ = k.SetStringValue("URL Protocol", "")
	k.Close()
	k2, _, err := registry.CreateKey(registry.CURRENT_USER, `Software\Classes\hermesvpn\shell\open\command`, registry.ALL_ACCESS)
	if err != nil {
		return
	}
	_ = k2.SetStringValue("", `"`+exe+`" "%1"`)
	k2.Close()
}

func openURL(u string) error {
	return exec.Command("rundll32", "url.dll,FileProtocolHandler", u).Start()
}

func listProcesses() []ProcInfo {
	snap, err := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPPROCESS, 0)
	if err != nil {
		return nil
	}
	defer windows.CloseHandle(snap)
	var pe windows.ProcessEntry32
	pe.Size = uint32(unsafe.Sizeof(pe))
	seen := map[string]bool{}
	out := []ProcInfo{}
	for err = windows.Process32First(snap, &pe); err == nil; err = windows.Process32Next(snap, &pe) {
		name := windows.UTF16ToString(pe.ExeFile[:])
		ln := strings.ToLower(name)
		if seen[ln] || pe.ProcessID <= 4 {
			continue
		}
		seen[ln] = true
		path := processImagePath(pe.ProcessID)
		if path == "" || strings.HasPrefix(strings.ToLower(path), `c:\windows\`) {
			continue
		}
		out = append(out, ProcInfo{Name: name, Path: path})
	}
	return out
}

func isAdmin() bool { return windows.GetCurrentProcessToken().IsElevated() }

func relaunchAsAdmin() error {
	exe, _ := os.Executable()
	verb, _ := windows.UTF16PtrFromString("runas")
	file, _ := windows.UTF16PtrFromString(exe)
	args, _ := windows.UTF16PtrFromString(strings.Join(os.Args[1:], " "))
	return windows.ShellExecute(0, verb, file, args, nil, windows.SW_NORMAL)
}

// tunCounters reads the HermesVPN Wintun adapter's byte counters. Xray's own
// stats miss XTLS-Vision direct-copy traffic, the adapter sees everything.
func tunCounters() (down, up int64, ok bool) {
	ifc, err := net.InterfaceByName("HermesVPN")
	if err != nil {
		return 0, 0, false
	}
	var row windows.MibIfRow2
	row.InterfaceIndex = uint32(ifc.Index)
	if windows.GetIfEntry2Ex(0, &row) != nil {
		return 0, 0, false
	}
	return int64(row.InOctets), int64(row.OutOctets), true
}
