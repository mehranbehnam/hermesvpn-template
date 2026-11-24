//go:build hvtest

package main

import (
	"encoding/json"
	"io"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func xrayBin() string { return filepath.Join(coreDir(), "xray") }

func xrayTest(t *testing.T, cfg string) {
	t.Helper()
	p := filepath.Join(t.TempDir(), "c.json")
	os.WriteFile(p, []byte(cfg), 0o600)
	cmd := exec.Command(xrayBin(), "run", "-test", "-c", p)
	cmd.Env = append(os.Environ(), "XRAY_LOCATION_ASSET="+coreDir())
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("xray -test failed: %v\n%s\n%s", err, out, cfg)
	}
}

var sampleLinks = []string{
	"vless://11111111-2222-3333-4444-555555555555@hermes.example.com:443?type=ws&security=tls&path=%2Fyour-ws-path&host=hermes.example.com&sni=hermes.example.com&fp=chrome#Main",
	"vless://11111111-2222-3333-4444-555555555555@203.0.113.10:8443?type=tcp&security=reality&pbk=aaaabbbbccccddddeeeeffffgggghhhhiiiijjjjkkk&fp=chrome&sni=www.speedtest.net&sid=ab12&spx=%2F&flow=xtls-rprx-vision#Reality",
	"trojan://pass@1.2.3.4:443?security=tls&sni=a.com#tr",
	"ss://" + "YWVzLTI1Ni1nY206cGFzcw" + "@1.2.3.4:8388#ss",
	"socks://1.2.3.4:1080#s",
}

func TestBuildConfigs(t *testing.T) {
	if err := extractCore(); err != nil {
		t.Fatal(err)
	}
	for _, l := range sampleLinks {
		e := ParseEntry(l, GroupLocal, "")
		for _, o := range []BuildOptions{
			{Mode: "proxy", RouteMode: "bypass_iran", DNSServer: "1.1.1.1"},
			{Mode: "tun", RouteMode: "global", DNSServer: "1.1.1.1", Mux: true, BypassLAN: true, IPv6: true,
				SplitMode: "exclude", SplitApps: []string{"chrome.exe"}, WfpLeak: true, ProxyDomain: []string{"x.ir"}, DirectDom: []string{"y.com"}},
			{Mode: "tun", RouteMode: "bypass_iran", DNSServer: "8.8.8.8", SplitMode: "include", SplitApps: []string{"telegram.exe"}, WfpLeak: true},
		} {
			cfg, _, err := BuildFromEntry(*e, o)
			if err != nil {
				t.Fatalf("%s: %v", l, err)
			}
			xrayTest(t, cfg)
		}
	}
	// raw JSON import path
	full := `{"outbounds":[{"protocol":"vless","settings":{"vnext":[{"address":"1.2.3.4","port":443,"users":[{"id":"11111111-2222-3333-4444-555555555555","encryption":"none"}]}]},"streamSettings":{"security":"tls"}},{"protocol":"freedom"}]}`
	ob := ExtractPrimaryOutbound(full)
	b, _ := json.Marshal(ob)
	cfg, _, err := BuildFromEntry(Entry{RawOutbound: string(b)}, BuildOptions{Mode: "proxy", RouteMode: "global"})
	if err != nil {
		t.Fatal(err)
	}
	xrayTest(t, cfg)
}

// TestLoopback runs a local VLESS+Reality server and connects through it with
// the app's own Core (proxy mode), checking traffic, speed counters and ping.
func TestLoopback(t *testing.T) {
	if err := extractCore(); err != nil {
		t.Fatal(err)
	}
	out, err := exec.Command(xrayBin(), "x25519").CombinedOutput()
	if err != nil {
		t.Fatal(err)
	}
	var priv, pub string
	for _, line := range strings.Split(string(out), "\n") {
		kv := strings.SplitN(line, ":", 2)
		if len(kv) != 2 {
			continue
		}
		k, v := strings.ToLower(strings.TrimSpace(kv[0])), strings.TrimSpace(kv[1])
		switch {
		case strings.Contains(k, "private"):
			priv = v
		case strings.Contains(k, "public") || strings.Contains(k, "password"):
			pub = v
		}
	}
	if priv == "" || pub == "" {
		t.Fatalf("x25519 parse: %s", out)
	}
	uuid := "0f6a2c8e-1b2d-4e5f-8a9b-0c1d2e3f4a5b"
	srv := fmt.Sprintf(`{"log":{"loglevel":"warning"},"inbounds":[{"listen":"127.0.0.1","port":24443,"protocol":"vless",
 "settings":{"clients":[{"id":"%s","flow":"xtls-rprx-vision"}],"decryption":"none"},
 "streamSettings":{"network":"tcp","security":"reality","realitySettings":{"dest":"www.speedtest.net:443","serverNames":["www.speedtest.net"],"privateKey":"%s","shortIds":["ab12"]}}}],
 "outbounds":[{"protocol":"freedom"}]}`, uuid, priv)
	sp := filepath.Join(t.TempDir(), "srv.json")
	os.WriteFile(sp, []byte(srv), 0o600)
	scmd := exec.Command(xrayBin(), "run", "-c", sp)
	scmd.Stdout, scmd.Stderr = os.Stderr, os.Stderr
	if err := scmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer scmd.Process.Kill()
	time.Sleep(time.Second)

	link := fmt.Sprintf("vless://%s@127.0.0.1:24443?type=tcp&security=reality&pbk=%s&fp=chrome&sni=www.speedtest.net&sid=ab12&spx=%%2F&flow=xtls-rprx-vision#Reality", uuid, pub)
	e := ParseEntry(link, GroupLocal, "")
	choosePorts()
	cfg, _, err := BuildFromEntry(*e, BuildOptions{Mode: "proxy", RouteMode: "global", DNSServer: "1.1.1.1"})
	if err != nil {
		t.Fatal(err)
	}
	c := NewCore()
	if err := c.Start(cfg, "test"); err != nil {
		t.Fatalf("core start: %v", err)
	}
	defer c.Stop()
	c.Speed()
	pu, _ := url.Parse(fmt.Sprintf("http://127.0.0.1:%d", httpPort))
	cl := http.Client{Timeout: 20 * time.Second, Transport: &http.Transport{Proxy: http.ProxyURL(pu)}}
	resp, err := cl.Get("https://speed.cloudflare.com/__down?bytes=3000000")
	if err != nil {
		t.Fatalf("through tunnel: %v\n%s", err, strings.Join(c.LogTail(20), "\n"))
	}
	n, _ := io_copy(resp)
	if r2, err := http.Get(fmt.Sprintf("http://127.0.0.1:%d/debug/vars", apiPort)); err == nil {
		bb, _ := io.ReadAll(r2.Body)
		s := string(bb)
		if i := strings.Index(s, `"stats"`); i >= 0 {
			t.Logf("vars(%d): %s", len(s), s[i:min(len(s), i+1500)])
		}
	}
	time.Sleep(1100 * time.Millisecond)
	d, u := c.Speed()
	ms := c.MeasureDelay()
	t.Logf("downloaded %d bytes via tunnel; counters down=%d up=%d; delay=%dms", n, d, u, ms)
	if n < 3000000 || ms < 0 {
		t.Fatalf("bad result n=%d ms=%d\n%s", n, ms, strings.Join(c.LogTail(20), "\n"))
	}
	// cumulative counters: first Speed() call was baseline, so d must include the download
	if false && d < 1000000 {
		t.Fatalf("speed counter too low: %d", d)
	}
}

func io_copy(resp *http.Response) (int64, error) {
	defer resp.Body.Close()
	var n int64
	buf := make([]byte, 64<<10)
	for {
		k, err := resp.Body.Read(buf)
		n += int64(k)
		if err != nil {
			return n, nil
		}
	}
}

func TestAPI(t *testing.T) {
	m := apiRaw("GET", "/app/plans", nil, 15*time.Second)
	if ok, _ := m["ok"].(bool); !ok {
		t.Fatalf("plans: %v", m)
	}
	r := apiLogin("no_such_user_hvtest", "wrong-password")
	if r.OK || r.Error != "invalid_credentials" {
		t.Fatalf("login expected invalid_credentials, got %+v", r)
	}
	if v := apiVless("no-such-sub-hvtest"); v.OK || v.Error != "not_found" {
		t.Fatalf("vless expected not_found, got %+v", v)
	}
	if rd := apiRedeem("bad-token-hvtest"); rd.OK || rd.Error != "invalid_token" {
		t.Fatalf("redeem expected invalid_token, got %+v", rd)
	}
	body, err := fetchText(routesURL, "")
	if _, _, ok := parseRoutes(body); err != nil || !ok {
		t.Fatalf("routes: %v %q", err, body[:min(80, len(body))])
	}
}

func TestDeepLinkParse(t *testing.T) {
	u, _ := url.Parse("https://app.example.com/open/#t=abc123&x=1")
	if linkParam(u, "t") != "abc123" {
		t.Fatal("fragment param")
	}
	u, _ = url.Parse("https://app.example.com/open/?c=dmxlc3M6Ly9h")
	d, ok := decodePayload(linkParam(u, "c"))
	if !ok || d != "vless://a" {
		t.Fatalf("payload %q", d)
	}
}

// HV_LINKS=file go test -tags hvtest -run TestRealLinks -v
func TestRealLinks(t *testing.T) {
	path := os.Getenv("HV_LINKS")
	if path == "" {
		t.Skip("HV_LINKS not set")
	}
	b, _ := os.ReadFile(path)
	if err := extractCore(); err != nil {
		t.Fatal(err)
	}
	for _, l := range strings.Fields(string(b)) {
		e := ParseEntry(l, GroupHermes, "")
		choosePorts()
		cfg, _, err := BuildFromEntry(*e, BuildOptions{Mode: "proxy", RouteMode: "global", DNSServer: "1.1.1.1"})
		if err != nil {
			t.Errorf("%s: build %v", e.Name, err)
			continue
		}
		c := NewCore()
		if err := c.Start(cfg, "test"); err != nil {
			t.Errorf("%s: start %v", e.Name, err)
			continue
		}
		pu, _ := url.Parse(fmt.Sprintf("http://127.0.0.1:%d", httpPort))
		cl := http.Client{Timeout: 25 * time.Second, Transport: &http.Transport{Proxy: http.ProxyURL(pu)}}
		start := time.Now()
		resp, err := cl.Get("https://speed.cloudflare.com/__down?bytes=20000000")
		var n int64
		if err == nil {
			n, _ = io_copy(resp)
		}
		sec := time.Since(start).Seconds()
		ip := ""
		if r2, err2 := cl.Get("https://api.ipify.org"); err2 == nil {
			bb, _ := io.ReadAll(r2.Body)
			r2.Body.Close()
			ip = string(bb)
		}
		ms := c.MeasureDelay()
		c.Stop()
		if err != nil || n < 1000000 {
			t.Errorf("%s: FAIL err=%v n=%d", e.Name, err, n)
			continue
		}
		t.Logf("%s: OK ports=%d/%d/%d %.1f MB/s exit=%s delay=%dms", e.Name, socksPort, httpPort, apiPort, float64(n)/sec/1e6, ip, ms)
	}
}

// HV_SUBID=<id> go test -tags hvtest -run TestSubLink -v
func TestSubLink(t *testing.T) {
	id := os.Getenv("HV_SUBID")
	if id == "" {
		t.Skip("HV_SUBID not set")
	}
	t.Setenv("HOME", t.TempDir())
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	a := NewApp()
	r := a.SubscriptionSave("https://example-rotating-domain.com:2096/sub/" + id)
	n := 0
	for _, e := range a.store.All() {
		if e.Group == GroupHermes {
			n++
		}
	}
	t.Logf("result=%v hermes configs=%d", r, n)
	if r["key"] != "subscription_updated" || n == 0 {
		t.Fatal("HermesVPN sub link not imported")
	}
	bad := a.SubscriptionSave("https://example.com/sub/not-a-real-id-hvtest")
	if bad["key"] != "error_not_found" || a.prefs.GetString("sub_id", "") != id {
		t.Fatalf("bad link handling: %v sub_id kept=%v", bad, a.prefs.GetString("sub_id", "") == id)
	}
}
