package main

// Emergency routes (DNS tunnel, SSH with payload/SNI, SSH, locked .hvpn files).
// Same engine as Android (~/winapp/tunnel), linked in-process here. The engine
// exposes a local SOCKS5 port; the Xray config keeps its usual TUN/routing and
// only its "proxy" outbound points at that port.

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"hermesvpn/tunnel/engine"
)

const tunnelSocksPort = 10820
const tunnelMark = "hvpn"

func tunnelsDir() string {
	d := filepath.Join(dataDir(), "tunnels")
	os.MkdirAll(d, 0o700)
	return d
}

func tunnelSpecPath(id string) string { return filepath.Join(tunnelsDir(), id+".spec") }

// normalizeTunnel: HVPN1 file, hvpn:// link, or route JSON (with "mode").
func normalizeTunnel(raw string) string {
	t := strings.TrimSpace(raw)
	switch {
	case strings.HasPrefix(t, "HVPN1"):
		return t
	case strings.HasPrefix(strings.ToLower(t), "hvpn://"):
		b, err := base64.RawURLEncoding.DecodeString(strings.TrimRight(strings.TrimSpace(t[7:]), "="))
		if err == nil && (strings.HasPrefix(string(b), "HVPN1") || strings.HasPrefix(strings.TrimSpace(string(b)), "{")) {
			return string(b)
		}
	case strings.HasPrefix(t, "{"):
		var c engine.Config
		if json.Unmarshal([]byte(t), &c) == nil {
			switch c.Mode {
			case "dnstt", "ssh-ws", "ssh":
				return t
			}
		}
	}
	return ""
}

func loadTunnelSpec(spec string) (engine.Config, bool, error) {
	if engine.IsHVPN([]byte(spec)) {
		c, err := engine.OpenHVPN([]byte(spec))
		return c, true, err
	}
	var c engine.Config
	return c, false, json.Unmarshal([]byte(spec), &c)
}

func tunnelLabel(mode string) string {
	switch mode {
	case "dnstt":
		return "DNS"
	case "ssh-ws":
		return "SSH-WS"
	}
	return "SSH"
}

// addTunnel stores the route and adds it to the config list. Error codes match the UI strings.
func (s *Store) addTunnel(raw, group string) ImportResult {
	spec := normalizeTunnel(raw)
	if spec == "" {
		return ImportResult{Error: "tunnel_err_invalid"}
	}
	cfg, locked, err := loadTunnelSpec(spec)
	if err != nil {
		switch {
		case strings.Contains(err.Error(), "not made by HermesVPN"):
			return ImportResult{Error: "tunnel_err_foreign"}
		case strings.Contains(err.Error(), "expired"):
			return ImportResult{Error: "tunnel_err_expired"}
		}
		return ImportResult{Error: "tunnel_err_invalid"}
	}
	sum := sha256.Sum256([]byte(spec))
	uri := "hvpn://" + hex.EncodeToString(sum[:])[:16]
	for _, e := range s.All() {
		if e.URI == uri {
			return ImportResult{Skipped: 1}
		}
	}
	id := newUUID()
	if err := os.WriteFile(tunnelSpecPath(id), []byte(spec), 0o600); err != nil {
		return ImportResult{Error: "tunnel_err_invalid"}
	}
	ob, _ := json.Marshal(obj{"protocol": "socks", tunnelMark: id,
		"settings": obj{"servers": arr{obj{"address": "127.0.0.1", "port": tunnelSocksPort}}}})
	name := cfg.Name
	if name == "" {
		name = "HermesVPN " + tunnelLabel(cfg.Mode)
	}
	proto, addr := tunnelLabel(cfg.Mode), tunnelLabel(cfg.Mode)
	if locked {
		proto += " 🔒"
		addr = "🔒"
	} else if cfg.WS.Connect != "" {
		addr = cfg.WS.Connect
	}
	s.Add(Entry{ID: id, Name: name, URI: uri, Protocol: proto, Address: addr, Group: group,
		Ping: PingUnknown, RawOutbound: string(ob)})
	return ImportResult{Added: 1}
}

func tunnelIDOf(e Entry) string {
	if e.RawOutbound == "" {
		return ""
	}
	var o obj
	if json.Unmarshal([]byte(e.RawOutbound), &o) != nil {
		return ""
	}
	id, _ := o[tunnelMark].(string)
	return id
}

// ---------- runtime ----------

var (
	tunMu  sync.Mutex
	tunCur *engine.Tunnel
)

// startTunnel brings the route up (blocking, up to ~75 s for DNS) and returns
// the IPv4s it dials directly (they must not loop back into the TUN).
func startTunnel(id string) ([]string, error) {
	stopTunnel()
	b, err := os.ReadFile(tunnelSpecPath(id))
	if err != nil {
		return nil, errors.New("route file missing")
	}
	cfg, locked, err := loadTunnelSpec(string(b))
	if err != nil {
		return nil, err
	}
	var ips []string
	for _, h := range cfg.BypassHosts() {
		if ip := net.ParseIP(h); ip != nil {
			ips = append(ips, h)
			continue
		}
		if addrs, err := net.LookupIP(h); err == nil {
			for _, a := range addrs {
				if a.To4() != nil {
					ips = append(ips, a.String())
				}
			}
		}
	}
	cfg.Socks = "127.0.0.1:" + itoa(tunnelSocksPort)
	t, err := engine.Start(cfg, engine.Hooks{Log: func(string) {}})
	if err != nil {
		if locked {
			return nil, errors.New("connect failed")
		}
		return nil, err
	}
	tunMu.Lock()
	tunCur = t
	tunMu.Unlock()
	return ips, nil
}

func stopTunnel() {
	tunMu.Lock()
	t := tunCur
	tunCur = nil
	tunMu.Unlock()
	if t != nil {
		t.Close()
	}
}

func itoa(n int) string {
	b, _ := json.Marshal(n)
	return string(b)
}
