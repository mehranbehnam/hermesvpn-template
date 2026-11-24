// Package engine: HermesVPN emergency routes.
//
//	mode "dnstt"  : SSH over a DNS tunnel (SlowDNS)
//	mode "ssh-ws" : SSH over TCP/TLS with a custom SNI and HTTP payload
//	                (WebSocket-style upgrade, the HTTP Custom / HA Tunnel way)
//	mode "ssh"    : plain SSH
//
// Whatever the mode, the result is a local SOCKS5 proxy that the app's Xray
// core (or the system) uses as its outbound.
package engine

import (
	"bufio"
	"context"
	"crypto/tls"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/crypto/ssh"
	"hermesvpn/tunnel/dnsttc"
)

type Config struct {
	Name string `json:"name,omitempty"`
	Mode string `json:"mode"` // dnstt | ssh-ws | ssh
	SSH  struct {
		Host    string `json:"host,omitempty"` // for ssh / ssh-ws when no ws.connect
		Port    int    `json:"port,omitempty"`
		User    string `json:"user"`
		Pass    string `json:"pass"`
		HostKey string `json:"hostkey,omitempty"` // optional SHA256:... fingerprint pin
	} `json:"ssh"`
	DNSTT struct {
		Domain   string `json:"domain"`
		PubKey   string `json:"pubkey"`
		Resolver string `json:"resolver,omitempty"` // host[:port]; "" = 8.8.8.8
		DoH      string `json:"doh,omitempty"`
		DoT      string `json:"dot,omitempty"`
	} `json:"dnstt"`
	WS struct {
		Connect  string `json:"connect"` // host:port actually dialed
		TLS      bool   `json:"tls"`
		SNI      string `json:"sni,omitempty"`  // TLS server name sent
		Host     string `json:"host,omitempty"` // value for [host]
		Path     string `json:"path,omitempty"`
		Payload  string `json:"payload,omitempty"`  // HTTP Custom style, [crlf] [host] [path]
		Insecure bool   `json:"insecure,omitempty"` // skip cert check (fake SNI)
	} `json:"ws"`
	Socks   string `json:"socks,omitempty"`   // listen address, default 127.0.0.1:10820
	Expires int64  `json:"expires,omitempty"` // unix seconds, 0 = never (locked files)
	ID      string `json:"id,omitempty"`
}

// Dialer hook so Android can protect() sockets; nil = net.Dialer.
type Hooks struct {
	DialTCP      func(ctx context.Context, addr string) (net.Conn, error)
	ListenPacket func() (net.PacketConn, error)
	Log          func(string)
}

type Tunnel struct {
	cfg   Config
	hooks Hooks
	ln    net.Listener

	mu     sync.Mutex
	client *ssh.Client
	dc     *dnsttc.Client
	closed bool
	// counters
	Up, Down int64
	State    string
}

func (t *Tunnel) logf(f string, a ...any) {
	s := fmt.Sprintf(f, a...)
	if t.hooks.Log != nil {
		t.hooks.Log(s)
	} else {
		log.Print(s)
	}
}

// BypassHosts: hosts this route dials directly (must stay outside the VPN).
func (c *Config) BypassHosts() []string {
	var out []string
	add := func(hp string, _ string) {
		if hp == "" {
			return
		}
		h, _, err := net.SplitHostPort(hp)
		if err != nil {
			h = hp
		}
		if h != "" {
			out = append(out, h)
		}
	}
	switch c.Mode {
	case "dnstt":
		if c.DNSTT.DoH == "" && c.DNSTT.DoT == "" {
			r := c.DNSTT.Resolver
			if r == "" {
				r = "8.8.8.8"
			}
			add(r, "")
		} else if c.DNSTT.DoT != "" {
			add(c.DNSTT.DoT, "")
		}
	case "ssh-ws":
		if c.WS.Connect != "" {
			add(c.WS.Connect, "")
		} else {
			add(c.SSH.Host, "")
		}
	case "ssh":
		add(c.SSH.Host, "")
	}
	return out
}

// ApplyHostMap replaces hostnames with IPs resolved by the app (Android has no
// resolv.conf for a pure-Go binary).
func (c *Config) ApplyHostMap(m map[string]string) {
	rep := func(hp string) string {
		h, p, err := net.SplitHostPort(hp)
		if err != nil {
			if ip, ok := m[hp]; ok {
				return ip
			}
			return hp
		}
		if ip, ok := m[h]; ok {
			return net.JoinHostPort(ip, p)
		}
		return hp
	}
	c.WS.Connect = rep(c.WS.Connect)
	c.DNSTT.Resolver = rep(c.DNSTT.Resolver)
	c.DNSTT.DoT = rep(c.DNSTT.DoT)
	if ip, ok := m[c.SSH.Host]; ok {
		c.SSH.Host = ip
	}
}

func Start(cfg Config, h Hooks) (*Tunnel, error) {
	if cfg.Expires > 0 && time.Now().Unix() > cfg.Expires {
		return nil, errors.New("config expired")
	}
	if cfg.Socks == "" {
		cfg.Socks = "127.0.0.1:10820"
	}
	switch cfg.Mode {
	case "dnstt", "ssh-ws", "ssh":
	default:
		return nil, fmt.Errorf("unknown mode %q", cfg.Mode)
	}
	ln, err := net.Listen("tcp", cfg.Socks)
	if err != nil {
		return nil, err
	}
	t := &Tunnel{cfg: cfg, hooks: h, ln: ln, State: "connecting"}
	if _, err := t.sshClient(); err != nil {
		// keep going: it will retry per request, but report the first error
		t.logf("first connect failed: %v", err)
		ln.Close()
		return nil, err
	}
	go t.serve()
	go t.keepalive()
	return t, nil
}

func (t *Tunnel) Addr() string { return t.ln.Addr().String() }

func (t *Tunnel) Close() {
	t.mu.Lock()
	t.closed = true
	c, d := t.client, t.dc
	t.client, t.dc = nil, nil
	t.mu.Unlock()
	t.ln.Close()
	if c != nil {
		c.Close()
	}
	if d != nil {
		d.Close()
	}
}

func (t *Tunnel) dialTCP(ctx context.Context, addr string) (net.Conn, error) {
	if t.hooks.DialTCP != nil {
		return t.hooks.DialTCP(ctx, addr)
	}
	var d net.Dialer
	return d.DialContext(ctx, "tcp", addr)
}

// transport returns the raw byte stream that SSH runs over.
func (t *Tunnel) transport(ctx context.Context) (net.Conn, error) {
	c := &t.cfg
	switch c.Mode {
	case "dnstt":
		t.mu.Lock()
		dc := t.dc
		t.mu.Unlock()
		if dc == nil || dc.Closed() {
			var err error
			dc, err = dnsttc.Dial(dnsttc.Options{Domain: c.DNSTT.Domain, PubKey: c.DNSTT.PubKey,
				UDP: c.DNSTT.Resolver, DoH: c.DNSTT.DoH, DoT: c.DNSTT.DoT, ListenPacket: t.hooks.ListenPacket})
			if err != nil {
				return nil, err
			}
			t.mu.Lock()
			t.dc = dc
			t.mu.Unlock()
		}
		return dc.Open(ctx)
	case "ssh":
		return t.dialTCP(ctx, net.JoinHostPort(c.SSH.Host, strconv.Itoa(portOr(c.SSH.Port, 22))))
	case "ssh-ws":
		target := c.WS.Connect
		if target == "" {
			target = net.JoinHostPort(c.SSH.Host, strconv.Itoa(portOr(c.SSH.Port, 80)))
		}
		raw, err := t.dialTCP(ctx, target)
		if err != nil {
			return nil, err
		}
		var conn net.Conn = raw
		if c.WS.TLS {
			sni := c.WS.SNI
			if sni == "" {
				sni, _, _ = net.SplitHostPort(target)
			}
			tc := tls.Client(raw, &tls.Config{ServerName: sni, InsecureSkipVerify: c.WS.Insecure,
				NextProtos: []string{"http/1.1"}})
			tc.SetDeadline(time.Now().Add(15 * time.Second))
			if err := tc.Handshake(); err != nil {
				raw.Close()
				return nil, fmt.Errorf("tls: %v", err)
			}
			tc.SetDeadline(time.Time{})
			conn = tc
		}
		return upgrade(conn, c)
	}
	return nil, errors.New("bad mode")
}

func portOr(p, d int) int {
	if p == 0 {
		return d
	}
	return p
}

// ExpandPayload turns an HTTP Custom style payload into bytes.
func ExpandPayload(c *Config) string {
	host := c.WS.Host
	if host == "" {
		host = c.WS.SNI
	}
	if host == "" {
		host, _, _ = net.SplitHostPort(c.WS.Connect)
	}
	path := c.WS.Path
	if path == "" {
		path = "/"
	}
	p := c.WS.Payload
	if p == "" {
		p = "GET [path] HTTP/1.1[crlf]Host: [host][crlf]Upgrade: websocket[crlf]Connection: Upgrade[crlf]Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==[crlf]Sec-WebSocket-Version: 13[crlf]User-Agent: [ua][crlf][crlf]"
	}
	r := strings.NewReplacer("[crlf]", "\r\n", "[cr]", "\r", "[lf]", "\n", "[host]", host,
		"[path]", path, "[ua]", "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36",
		"[protocol]", "HTTP/1.1", "[split]", "")
	return r.Replace(p)
}

// upgrade sends the payload and swallows HTTP responses until the server
// answers 101 (or 200 for CONNECT-style proxies). After that the stream is raw SSH.
func upgrade(conn net.Conn, c *Config) (net.Conn, error) {
	conn.SetDeadline(time.Now().Add(20 * time.Second))
	defer conn.SetDeadline(time.Time{})
	if _, err := io.WriteString(conn, ExpandPayload(c)); err != nil {
		conn.Close()
		return nil, err
	}
	br := bufio.NewReader(conn)
	for i := 0; i < 3; i++ {
		line, err := br.ReadString('\n')
		if err != nil {
			conn.Close()
			return nil, fmt.Errorf("payload response: %v", err)
		}
		f := strings.Fields(line)
		code := 0
		if len(f) >= 2 && strings.HasPrefix(f[0], "HTTP/") {
			code, _ = strconv.Atoi(f[1])
		}
		for { // skip headers
			h, err := br.ReadString('\n')
			if err != nil {
				conn.Close()
				return nil, err
			}
			if strings.TrimSpace(h) == "" {
				break
			}
		}
		if code == 101 || code == 200 {
			return &bufConn{Conn: conn, r: br}, nil
		}
		if code == 0 {
			conn.Close()
			return nil, fmt.Errorf("not HTTP: %q", strings.TrimSpace(line))
		}
		if code >= 400 {
			conn.Close()
			return nil, fmt.Errorf("server answered %d", code)
		}
	}
	conn.Close()
	return nil, errors.New("no 101 from server")
}

type bufConn struct {
	net.Conn
	r *bufio.Reader
}

func (b *bufConn) Read(p []byte) (int, error) { return b.r.Read(p) }

func (t *Tunnel) sshClient() (*ssh.Client, error) {
	t.mu.Lock()
	if t.closed {
		t.mu.Unlock()
		return nil, errors.New("closed")
	}
	if t.client != nil {
		c := t.client
		t.mu.Unlock()
		return c, nil
	}
	t.mu.Unlock()

	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	tr, err := t.transport(ctx)
	if err != nil {
		t.State = "error: " + err.Error()
		return nil, err
	}
	pin := t.cfg.SSH.HostKey
	cc := &ssh.ClientConfig{
		User: t.cfg.SSH.User,
		Auth: []ssh.AuthMethod{ssh.Password(t.cfg.SSH.Pass),
			ssh.KeyboardInteractive(func(_, _ string, q []string, _ []bool) ([]string, error) {
				a := make([]string, len(q))
				for i := range a {
					a[i] = t.cfg.SSH.Pass
				}
				return a, nil
			})},
		HostKeyCallback: func(_ string, _ net.Addr, k ssh.PublicKey) error {
			if pin != "" && ssh.FingerprintSHA256(k) != pin {
				return errors.New("host key mismatch")
			}
			return nil
		},
		Timeout:       40 * time.Second,
		ClientVersion: "SSH-2.0-OpenSSH_9.6",
	}
	tr.SetDeadline(time.Now().Add(60 * time.Second))
	sc, chans, reqs, err := ssh.NewClientConn(tr, "server", cc)
	if err != nil {
		tr.Close()
		t.State = "error: " + err.Error()
		return nil, err
	}
	tr.SetDeadline(time.Time{})
	cl := ssh.NewClient(sc, chans, reqs)
	t.mu.Lock()
	if t.client != nil { // lost the race
		old := t.client
		t.mu.Unlock()
		cl.Close()
		return old, nil
	}
	t.client = cl
	t.mu.Unlock()
	t.State = "connected"
	t.logf("ssh connected (%s)", t.cfg.Mode)
	go func() {
		cl.Wait()
		t.mu.Lock()
		if t.client == cl {
			t.client = nil
		}
		t.mu.Unlock()
		t.State = "reconnecting"
		t.logf("ssh session ended")
	}()
	return cl, nil
}

func (t *Tunnel) keepalive() {
	for {
		time.Sleep(25 * time.Second)
		t.mu.Lock()
		if t.closed {
			t.mu.Unlock()
			return
		}
		c := t.client
		t.mu.Unlock()
		if c == nil {
			t.sshClient()
			continue
		}
		done := make(chan error, 1)
		go func() { _, _, err := c.SendRequest("keepalive@openssh.com", true, nil); done <- err }()
		select {
		case err := <-done:
			if err != nil {
				c.Close()
			}
		case <-time.After(20 * time.Second):
			c.Close()
		}
	}
}

func (t *Tunnel) dialRemote(addr string) (net.Conn, error) {
	for i := 0; i < 2; i++ {
		c, err := t.sshClient()
		if err != nil {
			return nil, err
		}
		conn, err := c.Dial("tcp", addr)
		if err == nil {
			return conn, nil
		}
		var oe *ssh.OpenChannelError
		if errors.As(err, &oe) {
			return nil, err // server refused this target; session is fine
		}
		c.Close()
	}
	return nil, errors.New("ssh unavailable")
}

func (t *Tunnel) serve() {
	for {
		c, err := t.ln.Accept()
		if err != nil {
			return
		}
		go t.handleSocks(c)
	}
}

// Minimal SOCKS5 (no auth, CONNECT only) + HTTP CONNECT, enough for Xray's socks outbound and browsers.
func (t *Tunnel) handleSocks(c net.Conn) {
	defer c.Close()
	c.SetDeadline(time.Now().Add(30 * time.Second))
	br := bufio.NewReader(c)
	b, err := br.Peek(1)
	if err != nil {
		return
	}
	var target string
	if b[0] == 5 {
		hdr := make([]byte, 2)
		if _, err := io.ReadFull(br, hdr); err != nil {
			return
		}
		if _, err := io.ReadFull(br, make([]byte, hdr[1])); err != nil {
			return
		}
		c.Write([]byte{5, 0})
		req := make([]byte, 4)
		if _, err := io.ReadFull(br, req); err != nil {
			return
		}
		if req[1] != 1 { // only CONNECT
			c.Write([]byte{5, 7, 0, 1, 0, 0, 0, 0, 0, 0})
			return
		}
		var host string
		switch req[3] {
		case 1:
			ip := make([]byte, 4)
			io.ReadFull(br, ip)
			host = net.IP(ip).String()
		case 4:
			ip := make([]byte, 16)
			io.ReadFull(br, ip)
			host = net.IP(ip).String()
		case 3:
			l, _ := br.ReadByte()
			n := make([]byte, l)
			io.ReadFull(br, n)
			host = string(n)
		default:
			return
		}
		pb := make([]byte, 2)
		if _, err := io.ReadFull(br, pb); err != nil {
			return
		}
		target = net.JoinHostPort(host, strconv.Itoa(int(binary.BigEndian.Uint16(pb))))
		rc, err := t.dialRemote(target)
		if err != nil {
			c.Write([]byte{5, 5, 0, 1, 0, 0, 0, 0, 0, 0})
			return
		}
		defer rc.Close()
		c.Write([]byte{5, 0, 0, 1, 0, 0, 0, 0, 0, 0})
		c.SetDeadline(time.Time{})
		t.pipe(c, br, rc)
		return
	}
	// HTTP CONNECT
	line, err := br.ReadString('\n')
	if err != nil {
		return
	}
	f := strings.Fields(line)
	if len(f) < 2 || f[0] != "CONNECT" {
		io.WriteString(c, "HTTP/1.1 405 Method Not Allowed\r\n\r\n")
		return
	}
	for {
		h, err := br.ReadString('\n')
		if err != nil || strings.TrimSpace(h) == "" {
			break
		}
	}
	rc, err := t.dialRemote(f[1])
	if err != nil {
		io.WriteString(c, "HTTP/1.1 502 Bad Gateway\r\n\r\n")
		return
	}
	defer rc.Close()
	io.WriteString(c, "HTTP/1.1 200 Connection established\r\n\r\n")
	c.SetDeadline(time.Time{})
	t.pipe(c, br, rc)
}

func (t *Tunnel) pipe(c net.Conn, br *bufio.Reader, rc net.Conn) {
	done := make(chan struct{}, 2)
	go func() {
		n, _ := io.Copy(rc, br)
		t.mu.Lock()
		t.Up += n
		t.mu.Unlock()
		if cw, ok := rc.(interface{ CloseWrite() error }); ok {
			cw.CloseWrite()
		}
		done <- struct{}{}
	}()
	go func() {
		n, _ := io.Copy(c, rc)
		t.mu.Lock()
		t.Down += n
		t.mu.Unlock()
		if cw, ok := c.(*net.TCPConn); ok {
			cw.CloseWrite()
		}
		done <- struct{}{}
	}()
	<-done
	<-done
}
