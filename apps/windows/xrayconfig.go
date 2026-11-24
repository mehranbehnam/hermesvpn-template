package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// Port of VlessConfig.kt. The routing/DNS part is kept identical to Android;
// only the inbound side differs (Windows Wintun TUN instead of Android's fd).

const (
	irDNSPrimary   = "178.22.122.100"
	irDNSSecondary = "185.51.200.2"
)

// Local ports. Preferred values match Android; if another program (another VPN
// client, or a leftover process) holds one, a free port is picked instead.
var (
	socksPort = 10808
	httpPort  = 10809
	apiPort   = 10813
)

func freePort(preferred int) int {
	if l, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", preferred)); err == nil {
		l.Close()
		return preferred
	}
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return preferred
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port
}

// choosePorts is called right before each connect.
func choosePorts() {
	socksPort = freePort(10808)
	httpPort = freePort(10809)
	if httpPort == socksPort {
		httpPort = freePort(0)
	}
	apiPort = freePort(10813)
	for apiPort == socksPort || apiPort == httpPort {
		apiPort = freePort(0)
	}
}

// Same list as HermesVpnService.PUBLIC_IPV4_ROUTES: everything except private/LAN ranges.
var publicIPv4Routes = []string{
	"0.0.0.0/5", "8.0.0.0/7", "11.0.0.0/8", "12.0.0.0/6", "16.0.0.0/4", "32.0.0.0/3", "64.0.0.0/3",
	"96.0.0.0/4", "112.0.0.0/5", "120.0.0.0/6", "124.0.0.0/7", "126.0.0.0/8", "128.0.0.0/3",
	"160.0.0.0/5", "168.0.0.0/8", "169.0.0.0/9", "169.128.0.0/10", "169.192.0.0/11", "169.224.0.0/12",
	"169.240.0.0/13", "169.248.0.0/14", "169.252.0.0/15", "169.255.0.0/16", "170.0.0.0/7",
	"172.0.0.0/12", "172.32.0.0/11", "172.64.0.0/10", "172.128.0.0/9", "173.0.0.0/8", "174.0.0.0/7",
	"176.0.0.0/4", "192.0.0.0/9", "192.128.0.0/11", "192.160.0.0/13", "192.169.0.0/16",
	"192.170.0.0/15", "192.172.0.0/14", "192.176.0.0/12", "192.192.0.0/10", "193.0.0.0/8",
	"194.0.0.0/7", "196.0.0.0/6", "200.0.0.0/5", "208.0.0.0/4",
}

type BuildOptions struct {
	Mode        string // "tun" or "proxy"
	Mux         bool
	BypassLAN   bool
	IPv6        bool
	RouteMode   string // "global" or "bypass_iran"
	DNSServer   string
	SplitMode   string // "exclude" / "include"
	SplitApps   []string
	WfpLeak     bool
	ProxyDomain []string
	DirectDom   []string
	// emergency route: proxy outbound is the local SOCKS of the engine (no UDP)
	Tunnel       bool
	TunnelDirect []string // IPs the engine dials itself → always direct
}

type obj = map[string]any
type arr = []any

func parseQuery(raw string) map[string]string {
	out := map[string]string{}
	for _, p := range strings.Split(raw, "&") {
		i := strings.Index(p, "=")
		if i < 0 {
			continue
		}
		v, err := url.QueryUnescape(p[i+1:])
		if err != nil {
			v = p[i+1:]
		}
		out[p[:i]] = v
	}
	return out
}

func get(m map[string]string, k, def string) string {
	if v, ok := m[k]; ok {
		return v
	}
	return def
}

func buildStreamSettings(security string, q map[string]string) obj {
	network := get(q, "type", "tcp")
	ss := obj{"network": network}
	switch security {
	case "tls":
		ss["security"] = "tls"
		sni := q["sni"]
		if sni == "" {
			sni = q["host"]
		}
		tls := obj{"serverName": sni, "fingerprint": get(q, "fp", "chrome")}
		if a := q["alpn"]; a != "" {
			al := arr{}
			for _, x := range strings.Split(a, ",") {
				al = append(al, x)
			}
			tls["alpn"] = al
		}
		if q["allowInsecure"] == "1" || q["insecure"] == "1" {
			tls["allowInsecure"] = true
		}
		ss["tlsSettings"] = tls
	case "reality":
		ss["security"] = "reality"
		ss["realitySettings"] = obj{
			"serverName":  q["sni"],
			"fingerprint": get(q, "fp", "chrome"),
			"publicKey":   q["pbk"],
			"shortId":     q["sid"],
			"spiderX":     get(q, "spx", "/"),
		}
	default:
		ss["security"] = "none"
	}
	switch network {
	case "ws":
		ss["wsSettings"] = obj{"path": get(q, "path", "/"), "host": q["host"]}
	case "grpc":
		ss["grpcSettings"] = obj{"serviceName": q["serviceName"], "multiMode": q["mode"] == "multi"}
	case "httpupgrade":
		ss["httpupgradeSettings"] = obj{"path": get(q, "path", "/"), "host": q["host"]}
	case "xhttp", "splithttp":
		ss["network"] = "xhttp"
		x := obj{"path": get(q, "path", "/"), "host": q["host"]}
		if m := q["mode"]; m != "" {
			x["mode"] = m
		}
		ss["xhttpSettings"] = x
	case "tcp":
		if q["headerType"] == "http" {
			ss["tcpSettings"] = obj{"header": obj{"type": "http", "request": obj{
				"path": arr{get(q, "path", "/")}, "headers": obj{"Host": arr{q["host"]}},
			}}}
		}
	}
	return ss
}

func splitHostPort(u *url.URL) (string, int, error) {
	host := u.Hostname()
	if host == "" {
		return "", 0, errors.New("آدرس سرور در لینک پیدا نشد")
	}
	port, err := strconv.Atoi(u.Port())
	if err != nil || port <= 0 {
		return "", 0, errors.New("پورت در لینک پیدا نشد")
	}
	return host, port, nil
}

func decodeVmess(link string) map[string]any {
	b, err := b64decodeAny(strings.TrimPrefix(strings.TrimSpace(link), "vmess://"))
	if err != nil {
		return nil
	}
	var m map[string]any
	if json.Unmarshal(b, &m) != nil {
		return nil
	}
	return m
}

// OutboundFromURI turns a share link into an Xray outbound. VLESS follows
// VlessConfig.buildXrayConfig exactly; the other protocols are an addition so
// that configs the app lets you import also connect.
func OutboundFromURI(link string) (obj, error) {
	link = strings.TrimSpace(link)
	scheme := strings.ToLower(link[:max(0, strings.Index(link, "://"))])
	switch scheme {
	case "vless":
		u, err := url.Parse(link)
		if err != nil {
			return nil, fmt.Errorf("لینک نامعتبر است: %v", err)
		}
		if u.User == nil || u.User.Username() == "" {
			return nil, errors.New("UUID در لینک پیدا نشد")
		}
		host, port, err := splitHostPort(u)
		if err != nil {
			return nil, err
		}
		q := parseQuery(u.RawQuery)
		sec := get(q, "security", "none")
		user := obj{"id": u.User.Username(), "encryption": get(q, "encryption", "none")}
		if sec == "reality" {
			user["flow"] = get(q, "flow", "xtls-rprx-vision")
		} else if f := q["flow"]; f != "" {
			user["flow"] = f
		}
		return obj{
			"protocol": "vless", "tag": "proxy",
			"settings":       obj{"vnext": arr{obj{"address": host, "port": port, "users": arr{user}}}},
			"streamSettings": buildStreamSettings(sec, q),
		}, nil
	case "trojan":
		u, err := url.Parse(link)
		if err != nil {
			return nil, err
		}
		host, port, err := splitHostPort(u)
		if err != nil {
			return nil, err
		}
		q := parseQuery(u.RawQuery)
		pw := ""
		if u.User != nil {
			pw = u.User.Username()
		}
		sec := get(q, "security", "tls")
		if q["sni"] == "" && q["host"] == "" && sec == "tls" {
			q["sni"] = host
		}
		return obj{
			"protocol": "trojan", "tag": "proxy",
			"settings":       obj{"servers": arr{obj{"address": host, "port": port, "password": pw}}},
			"streamSettings": buildStreamSettings(sec, q),
		}, nil
	case "vmess":
		v := decodeVmess(link)
		if v == nil {
			return nil, errors.New("لینک vmess نامعتبر است")
		}
		s := func(k string) string {
			if x, ok := v[k]; ok && x != nil {
				return strings.TrimSpace(fmt.Sprint(x))
			}
			return ""
		}
		port, _ := strconv.Atoi(s("port"))
		aid, _ := strconv.Atoi(s("aid"))
		q := map[string]string{"type": s("net"), "host": s("host"), "path": s("path"), "sni": s("sni"), "fp": s("fp"), "alpn": s("alpn"), "headerType": s("type")}
		if q["type"] == "" {
			q["type"] = "tcp"
		}
		sec := s("tls")
		if sec == "" {
			sec = "none"
		}
		scy := s("scy")
		if scy == "" {
			scy = "auto"
		}
		return obj{
			"protocol": "vmess", "tag": "proxy",
			"settings":       obj{"vnext": arr{obj{"address": s("add"), "port": port, "users": arr{obj{"id": s("id"), "alterId": aid, "security": scy}}}}},
			"streamSettings": buildStreamSettings(sec, q),
		}, nil
	case "ss":
		rest := strings.TrimPrefix(link, link[:5])
		if h := strings.Index(rest, "#"); h >= 0 {
			rest = rest[:h]
		}
		if q := strings.Index(rest, "?"); q >= 0 {
			rest = rest[:q]
		}
		rest = strings.TrimSuffix(rest, "/")
		var method, pw, hostport string
		if at := strings.LastIndex(rest, "@"); at >= 0 {
			ui := rest[:at]
			hostport = rest[at+1:]
			if d, err := url.PathUnescape(ui); err == nil {
				ui = d
			}
			if b, err := b64decodeAny(ui); err == nil && strings.Contains(string(b), ":") {
				ui = string(b)
			}
			i := strings.Index(ui, ":")
			if i < 0 {
				return nil, errors.New("لینک ss نامعتبر است")
			}
			method, pw = ui[:i], ui[i+1:]
		} else {
			b, err := b64decodeAny(rest)
			if err != nil {
				return nil, errors.New("لینک ss نامعتبر است")
			}
			full := string(b)
			at := strings.LastIndex(full, "@")
			i := strings.Index(full, ":")
			if at < 0 || i < 0 || i > at {
				return nil, errors.New("لینک ss نامعتبر است")
			}
			method, pw, hostport = full[:i], full[i+1:at], full[at+1:]
		}
		h, p, err := net.SplitHostPort(hostport)
		if err != nil {
			return nil, errors.New("پورت در لینک پیدا نشد")
		}
		port, _ := strconv.Atoi(p)
		return obj{
			"protocol": "shadowsocks", "tag": "proxy",
			"settings": obj{"servers": arr{obj{"address": h, "port": port, "method": method, "password": pw}}},
		}, nil
	case "socks":
		u, err := url.Parse(link)
		if err != nil {
			return nil, err
		}
		host, port, err := splitHostPort(u)
		if err != nil {
			return nil, err
		}
		srv := obj{"address": host, "port": port}
		if u.User != nil && u.User.Username() != "" {
			ui := u.User.Username()
			if p, ok := u.User.Password(); ok {
				srv["users"] = arr{obj{"user": ui, "pass": p}}
			} else if b, err := b64decodeAny(ui); err == nil && strings.Contains(string(b), ":") {
				i := strings.Index(string(b), ":")
				srv["users"] = arr{obj{"user": string(b)[:i], "pass": string(b)[i+1:]}}
			}
		}
		return obj{"protocol": "socks", "tag": "proxy", "settings": obj{"servers": arr{srv}}}, nil
	}
	return nil, errors.New("این نوع لینک در نسخه‌ی ویندوز پشتیبانی نمی‌شود")
}

var ipv4Re = regexp.MustCompile(`^[0-9.]+$`)

// pinAddress resolves the server domain once (IPv4) and pins it, exactly like
// Android. Returns the resolved IPs (used for diagnostics).
func pinAddress(out obj) []string {
	settings, _ := out["settings"].(obj)
	if settings == nil {
		return nil
	}
	var target obj
	for _, key := range []string{"vnext", "servers"} {
		if a, ok := settings[key].(arr); ok && len(a) > 0 {
			target, _ = a[0].(obj)
		}
	}
	if target == nil {
		return nil
	}
	addr, _ := target["address"].(string)
	if addr == "" || ipv4Re.MatchString(addr) {
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	ips, err := net.DefaultResolver.LookupIP(ctx, "ip4", addr)
	if err != nil || len(ips) == 0 {
		return nil
	}
	res := []string{}
	for _, ip := range ips {
		res = append(res, ip.String())
	}
	target["address"] = res[0]
	return res
}

func applyMux(out obj, enabled bool) {
	if !enabled {
		return
	}
	switch out["protocol"] {
	case "vless", "vmess", "trojan":
	default:
		return // socks (emergency route) and others: no mux
	}
	if _, ok := out["mux"]; ok {
		return
	}
	flow := ""
	if s, ok := out["settings"].(obj); ok {
		if v, ok := s["vnext"].(arr); ok && len(v) > 0 {
			if vo, ok := v[0].(obj); ok {
				if us, ok := vo["users"].(arr); ok && len(us) > 0 {
					if u, ok := us[0].(obj); ok {
						flow, _ = u["flow"].(string)
					}
				}
			}
		}
	}
	if strings.Contains(strings.ToLower(flow), "vision") {
		return
	}
	out["mux"] = obj{"enabled": true, "concurrency": 8, "xudpConcurrency": 8}
}

func rule(kv ...any) obj {
	r := obj{"type": "field"}
	for i := 0; i+1 < len(kv); i += 2 {
		r[kv[i].(string)] = kv[i+1]
	}
	return r
}

func strArr(s ...string) arr {
	a := arr{}
	for _, x := range s {
		a = append(a, x)
	}
	return a
}

// BuildConfig = VlessConfig.wrapOutbound for Windows.
func BuildConfig(outbound obj, o BuildOptions) (string, []string, error) {
	outbound["tag"] = "proxy"
	ips := pinAddress(outbound)
	applyMux(outbound, o.Mux)

	sniff := obj{"enabled": true, "destOverride": strArr("http", "tls")}
	inbounds := arr{}
	if o.Mode == "tun" {
		gw := strArr("10.10.14.1/30")
		routes := strArr("0.0.0.0/0")
		if o.BypassLAN {
			routes = strArr(publicIPv4Routes...)
		}
		if o.IPv6 {
			gw = append(gw, "fd00:1:fd00:1:fd00:1:fd00:1/126")
			routes = append(routes, "::/0")
		}
		dns := o.DNSServer
		if dns == "" {
			dns = "1.1.1.1"
		}
		ts := obj{
			"name": "HermesVPN", "desc": "HermesVPN", "mtu": 1500,
			"gateway": gw, "dns": strArr(dns), "autoSystemRoutingTable": routes,
		}
		if o.WfpLeak && !o.IPv6 {
			// keep IPv6 from leaking around the tunnel when it isn't routed into it
			ts["autoSystemWfpBlockLeak"] = strArr("misconfigtun")
		}
		inbounds = append(inbounds, obj{"tag": "tun-in", "protocol": "tun", "port": 0, "settings": ts, "sniffing": sniff})
	}
	inbounds = append(inbounds,
		obj{"tag": "socks-in", "protocol": "socks", "listen": "127.0.0.1", "port": socksPort,
			"settings": obj{"udp": true}, "sniffing": sniff},
		obj{"tag": "http-in", "protocol": "http", "listen": "127.0.0.1", "port": httpPort, "sniffing": sniff},
	)

	rules := arr{}
	if o.Tunnel {
		// The engine runs inside this exe: its own sockets enter the TUN too, so
		// send them (and the route's server IPs) out directly, never back into the proxy.
		if exe, err := os.Executable(); err == nil {
			// only what enters through the TUN: the app's own requests via its
			// local proxy port must still go through the route
			rules = append(rules, rule("inboundTag", strArr("tun-in"), "process", strArr(filepath.Base(exe)), "outboundTag", "direct"))
		}
		if len(o.TunnelDirect) > 0 {
			rules = append(rules, rule("ip", strArr(o.TunnelDirect...), "outboundTag", "direct"))
		}
		// no UDP through the route: refuse QUIC at once so apps fall back to TCP
		rules = append(rules, rule("network", "udp", "port", "443", "outboundTag", "block"))
	}
	if !o.IPv6 {
		rules = append(rules, rule("ip", strArr("::/0"), "outboundTag", "block"))
	}
	rules = append(rules, rule("port", 53, "outboundTag", "dns-out"))
	if o.Mode == "tun" && len(o.SplitApps) > 0 {
		if o.SplitMode == "include" {
			rules = append(rules, rule("inboundTag", strArr("tun-in"), "process", strArr(o.SplitApps...), "outboundTag", "proxy"))
			rules = append(rules, rule("inboundTag", strArr("tun-in"), "outboundTag", "direct"))
		} else {
			rules = append(rules, rule("process", strArr(o.SplitApps...), "outboundTag", "direct"))
		}
	}
	rules = append(rules,
		rule("ip", strArr("10.10.34.0/24"), "outboundTag", "proxy"),
		rule("ip", strArr("geoip:private"), "outboundTag", "direct"),
		rule("ip", strArr(irDNSPrimary, irDNSSecondary), "outboundTag", "direct"),
	)
	if len(o.ProxyDomain) > 0 {
		d := arr{}
		for _, x := range o.ProxyDomain {
			d = append(d, "domain:"+x)
		}
		rules = append(rules, rule("domain", d, "outboundTag", "proxy"))
	}
	if o.RouteMode != "global" {
		if len(o.DirectDom) > 0 {
			d := arr{}
			for _, x := range o.DirectDom {
				d = append(d, "domain:"+x)
			}
			rules = append(rules, rule("domain", d, "outboundTag", "direct"))
		}
		rules = append(rules,
			rule("domain", strArr("geosite:category-ir", "domain:ir"), "outboundTag", "direct"),
			rule("ip", strArr("geoip:ir"), "outboundTag", "direct"),
		)
	}

	irDomains := strArr("geosite:category-ir", "domain:ir")
	cfg := obj{
		"log":      obj{"loglevel": "warning"},
		"inbounds": inbounds,
		"outbounds": arr{
			outbound,
			obj{"protocol": "freedom", "tag": "direct"},
			obj{"protocol": "dns", "tag": "dns-out"},
			obj{"protocol": "blackhole", "tag": "block"},
		},
		"dns": obj{
			"servers": arr{
				obj{"address": irDNSPrimary, "domains": irDomains, "skipFallback": true},
				obj{"address": irDNSSecondary, "domains": irDomains, "skipFallback": true},
				foreignDNS(o.Tunnel, "1.1.1.1"), foreignDNS(o.Tunnel, "8.8.8.8"),
			},
			"queryStrategy": "UseIPv4",
		},
		"routing": obj{"domainStrategy": "IPIfNonMatch", "rules": rules},
		"stats":   obj{},
		"metrics": obj{"tag": "metrics", "listen": fmt.Sprintf("127.0.0.1:%d", apiPort)},
		"policy": obj{"system": obj{
			"statsInboundUplink": true, "statsInboundDownlink": true,
			"statsOutboundUplink": true, "statsOutboundDownlink": true,
		}},
	}
	b, err := json.MarshalIndent(cfg, "", " ")
	return string(b), ips, err
}

func foreignDNS(tunnel bool, ip string) string {
	if tunnel {
		return "tcp://" + ip
	}
	return ip
}

// BuildFromEntry picks the raw outbound or the share link.
func BuildFromEntry(e Entry, o BuildOptions) (string, []string, error) {
	var out obj
	if e.RawOutbound != "" {
		if err := json.Unmarshal([]byte(e.RawOutbound), &out); err != nil {
			return "", nil, fmt.Errorf("JSON نامعتبر است: %v", err)
		}
		delete(out, tunnelMark)
	} else {
		var err error
		out, err = OutboundFromURI(e.URI)
		if err != nil {
			return "", nil, err
		}
	}
	return BuildConfig(out, o)
}
