package main

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

func coreDir() string {
	base, err := os.UserCacheDir() // %LOCALAPPDATA% on Windows
	if err != nil || base == "" {
		base = os.TempDir()
	}
	d := filepath.Join(base, "HermesVPN", "core")
	_ = os.MkdirAll(d, 0o755)
	return d
}

func xrayExeName() string {
	if isWindows {
		return "xray.exe"
	}
	return "xray"
}

// extractCore writes xray + wintun + geo files next to each other (wintun.dll
// must sit beside xray.exe). Files are only rewritten when their hash changes.
func extractCore() error {
	dir := coreDir()
	var files []string
	for _, sub := range []string{"resources/common", coreSubdir} {
		entries, err := coreFS.ReadDir(sub)
		if err != nil {
			return err
		}
		for _, e := range entries {
			files = append(files, sub+"/"+e.Name())
		}
	}
	for _, f := range files {
		name := strings.TrimSuffix(filepath.Base(f), ".gz")
		gz, _ := coreFS.ReadFile(f)
		zr, err := gzip.NewReader(bytes.NewReader(gz))
		if err != nil {
			return err
		}
		data, err := io.ReadAll(zr)
		if err != nil {
			return err
		}
		sum := sha256.Sum256(data)
		want := hex.EncodeToString(sum[:])
		dst := filepath.Join(dir, name)
		if cur, err := os.ReadFile(dst); err == nil {
			s := sha256.Sum256(cur)
			if hex.EncodeToString(s[:]) == want {
				continue
			}
		}
		tmp := dst + ".new"
		if err := os.WriteFile(tmp, data, 0o755); err != nil {
			return err
		}
		_ = os.Remove(dst)
		if err := os.Rename(tmp, dst); err != nil {
			return err
		}
	}
	return nil
}

type Core struct {
	mu          sync.Mutex
	cmd         *exec.Cmd
	running     bool
	mode        string
	startedAt   int64
	logBuf      *ringLog
	exited      chan struct{}
	onExit      func(err string)
	lastUp      int64
	lastDown    int64
	proxyBackup *proxyState
	pingClient  *http.Client
	pingPort    int
}

type ringLog struct {
	mu    sync.Mutex
	lines []string
	part  string
}

func (r *ringLog) Write(p []byte) (int, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	s := r.part + string(p)
	parts := strings.Split(s, "\n")
	r.part = parts[len(parts)-1]
	for _, l := range parts[:len(parts)-1] {
		l = strings.TrimRight(l, "\r")
		if l == "" {
			continue
		}
		r.lines = append(r.lines, l)
		if len(r.lines) > 400 {
			r.lines = r.lines[len(r.lines)-400:]
		}
	}
	return len(p), nil
}

// Errors returns the last n lines that are not warnings (what to show the user on failure).
func (r *ringLog) Errors(n int) []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := []string{}
	for i := len(r.lines) - 1; i >= 0 && len(out) < n; i-- {
		l := r.lines[i]
		if strings.Contains(l, "[Warning]") || strings.Contains(l, "deprecated") || strings.HasPrefix(l, "----") || strings.HasPrefix(l, "Xray ") || strings.HasPrefix(l, "A unified platform") {
			continue
		}
		out = append([]string{l}, out...)
	}
	return out
}

func (r *ringLog) Tail(n int) []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	if n > len(r.lines) {
		n = len(r.lines)
	}
	out := make([]string, n)
	copy(out, r.lines[len(r.lines)-n:])
	return out
}

func NewCore() *Core { return &Core{logBuf: &ringLog{}} }

func (c *Core) Running() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.running
}

func (c *Core) StartedAt() int64 {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.startedAt
}

// Start launches xray with the given config and waits until it is either up
// (its local SOCKS port answers) or has exited with an error.
func (c *Core) Start(config string, mode string) error {
	c.Stop()
	if err := extractCore(); err != nil {
		return fmt.Errorf("extract core: %v", err)
	}
	dir := coreDir()
	cfgPath := filepath.Join(dataDir(), "xray-config.json")
	if err := os.WriteFile(cfgPath, []byte(config), 0o600); err != nil {
		return err
	}
	killStaleCore(dir)
	cmd := exec.Command(filepath.Join(dir, xrayExeName()), "run", "-c", cfgPath)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "XRAY_LOCATION_ASSET="+dir)
	cmd.Stdout = c.logBuf
	cmd.Stderr = c.logBuf
	hideWindow(cmd)
	c.logBuf.Write([]byte(fmt.Sprintf("---- %s start (%s)\n", time.Now().Format("15:04:05"), mode)))
	if err := cmd.Start(); err != nil {
		return err
	}
	_ = os.WriteFile(filepath.Join(dir, "xray.pid"), []byte(fmt.Sprint(cmd.Process.Pid)), 0o644)
	exited := make(chan struct{})
	var waitErr error
	go func() {
		waitErr = cmd.Wait()
		close(exited)
		c.mu.Lock()
		wasRunning := c.running && c.cmd == cmd
		if c.cmd == cmd {
			c.running = false
			c.cmd = nil
		}
		cb := c.onExit
		c.mu.Unlock()
		if wasRunning {
			c.restoreProxy()
			if cb != nil {
				cb("core stopped")
			}
		}
	}()

	deadline := time.Now().Add(12 * time.Second)
	for time.Now().Before(deadline) {
		select {
		case <-exited:
			tail := strings.Join(c.logBuf.Errors(3), "\n")
			if waitErr != nil && tail == "" {
				tail = waitErr.Error()
			}
			return errors.New(tail)
		default:
		}
		if portOpen(socksPort) {
			// TUN setup (adapter, routes) happens before inbounds listen; give it a moment.
			time.Sleep(400 * time.Millisecond)
			select {
			case <-exited:
				return errors.New(strings.Join(c.logBuf.Errors(3), "\n"))
			default:
			}
			c.mu.Lock()
			c.cmd = cmd
			c.exited = exited
			c.running = true
			c.mode = mode
			c.startedAt = nowMs()
			c.lastUp, c.lastDown = -1, -1
			c.mu.Unlock()
			if mode == "proxy" {
				c.mu.Lock()
				c.proxyBackup = setSystemProxy(fmt.Sprintf("127.0.0.1:%d", httpPort))
				c.mu.Unlock()
			}
			return nil
		}
		time.Sleep(150 * time.Millisecond)
	}
	_ = cmd.Process.Kill()
	return errors.New("timeout starting core\n" + strings.Join(c.logBuf.Tail(6), "\n"))
}

func (c *Core) restoreProxy() {
	c.mu.Lock()
	b := c.proxyBackup
	c.proxyBackup = nil
	c.mu.Unlock()
	if b != nil {
		restoreSystemProxy(b)
	}
}

func (c *Core) Stop() {
	c.mu.Lock()
	cmd := c.cmd
	exited := c.exited
	c.running = false
	c.cmd = nil
	c.mu.Unlock()
	c.restoreProxy()
	c.resetPing()
	if cmd != nil && cmd.Process != nil {
		gracefulStop(cmd)
		select {
		case <-exited:
		case <-time.After(4 * time.Second):
			_ = cmd.Process.Kill()
			<-exited
		}
	}
	_ = os.Remove(filepath.Join(coreDir(), "xray.pid"))
}

// Speed returns bytes/sec since last call, from Xray's metrics endpoint.
func (c *Core) Speed() (down, up int64) {
	if !c.Running() {
		return 0, 0
	}
	c.mu.Lock()
	mode := c.mode
	c.mu.Unlock()
	if mode == "tun" {
		if td, tu, ok := tunCounters(); ok {
			return c.delta(td, tu)
		}
	}
	cl := http.Client{Timeout: 900 * time.Millisecond}
	resp, err := cl.Get(fmt.Sprintf("http://127.0.0.1:%d/debug/vars", apiPort))
	if err != nil {
		return 0, 0
	}
	defer resp.Body.Close()
	var v struct {
		Stats struct {
			Inbound map[string]struct {
				Uplink   int64 `json:"uplink"`
				Downlink int64 `json:"downlink"`
			} `json:"inbound"`
		} `json:"stats"`
	}
	if json.NewDecoder(resp.Body).Decode(&v) != nil {
		return 0, 0
	}
	var tu, td int64
	for _, s := range v.Stats.Inbound {
		tu += s.Uplink
		td += s.Downlink
	}
	return c.delta(td, tu)
}

func (c *Core) delta(td, tu int64) (down, up int64) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.lastUp >= 0 {
		up = max(0, tu-c.lastUp)
		down = max(0, td-c.lastDown)
	}
	c.lastUp, c.lastDown = tu, td
	return down, up
}

// MeasureDelay = CoreController.measureDelay: a real request through the tunnel.
// MeasureDelay = the "ping" on the home screen: real round-trip through the
// tunnel. The test connection is kept alive (like v2rayN's real-delay test), so
// the number is the latency the user feels, not TCP+TLS+VLESS setup time.
func (c *Core) MeasureDelay() int64 {
	if !c.Running() {
		return -1
	}
	c.mu.Lock()
	if c.pingClient == nil || c.pingPort != httpPort {
		pu, _ := url.Parse(fmt.Sprintf("http://127.0.0.1:%d", httpPort))
		tr := &http.Transport{Proxy: http.ProxyURL(pu), MaxIdleConns: 2, MaxIdleConnsPerHost: 2, IdleConnTimeout: 2 * time.Minute, ForceAttemptHTTP2: true}
		c.pingClient = &http.Client{Timeout: 8 * time.Second, Transport: tr}
		c.pingPort = httpPort
	}
	cl := c.pingClient
	c.mu.Unlock()
	get := func() (int64, error) {
		start := time.Now()
		resp, err := cl.Get("https://www.gstatic.com/generate_204")
		if err != nil {
			return -1, err
		}
		_, _ = io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
		return time.Since(start).Milliseconds(), nil
	}
	first, err := get() // opens / reuses the tunnel connection
	if err != nil {
		c.resetPing()
		return -1
	}
	second, err := get() // warm connection = real latency
	if err != nil {
		return first
	}
	return min(first, second)
}

func (c *Core) resetPing() {
	c.mu.Lock()
	if c.pingClient != nil {
		c.pingClient.CloseIdleConnections()
	}
	c.pingClient = nil
	c.mu.Unlock()
}

func (c *Core) LogTail(n int) []string { return c.logBuf.Tail(n) }
