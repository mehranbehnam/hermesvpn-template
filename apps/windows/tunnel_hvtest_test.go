//go:build hvtest

package main

import (
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// HV_TUNNEL_SPEC=<file> go test -tags hvtest -run TestTunnelE2E
func TestTunnelE2E(t *testing.T) {
	specFile := os.Getenv("HV_TUNNEL_SPEC")
	if specFile == "" {
		t.Skip("no spec")
	}
	spec, _ := os.ReadFile(specFile)
	s := NewStore(NewPrefs())
	r := s.addTunnel(string(spec), GroupLocal)
	if r.Added != 1 {
		t.Fatalf("add: %+v", r)
	}
	var e Entry
	for _, x := range s.All() {
		if strings.HasPrefix(x.URI, "hvpn://") {
			e = x
		}
	}
	t.Logf("entry %s %s %s", e.Name, e.Protocol, e.Address)
	direct, err := startTunnel(tunnelIDOf(e))
	if err != nil {
		t.Fatal(err)
	}
	defer stopTunnel()
	t.Logf("direct %v", direct)
	choosePorts()
	opts := BuildOptions{Mode: "proxy", RouteMode: "global", Tunnel: true, TunnelDirect: direct}
	cfg, _, err := BuildFromEntry(e, opts)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(cfg, `"hvpn"`) {
		t.Fatal("marker leaked into xray config")
	}
	dir := t.TempDir()
	p := filepath.Join(dir, "c.json")
	os.WriteFile(p, []byte(cfg), 0o600)
	x := exec.Command(os.Getenv("HV_XRAY"), "run", "-c", p)
	x.Env = append(os.Environ(), "XRAY_LOCATION_ASSET="+os.Getenv("HV_ASSETS"))
	x.Stdout, x.Stderr = os.Stderr, os.Stderr
	if err := x.Start(); err != nil {
		t.Fatal(err)
	}
	defer x.Process.Kill()
	time.Sleep(2 * time.Second)
	pu, _ := url.Parse("http://127.0.0.1:" + itoa(httpPort))
	cl := &http.Client{Timeout: 40 * time.Second, Transport: &http.Transport{Proxy: http.ProxyURL(pu)}}
	resp, err := cl.Get("https://api.ipify.org")
	if err != nil {
		t.Fatal(err)
	}
	b, _ := io.ReadAll(resp.Body)
	t.Logf("exit ip via xray→engine: %s", b)
}
