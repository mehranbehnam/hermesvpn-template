// Library form of dnstt-client's run(): one KCP+Noise+smux session over DNS,
// from which streams to the server's upstream (SSH on aragorn) are opened.
// Derived from dnstt (CC0, https://www.bamsoftware.com/software/dnstt/).
package dnsttc

import (
	"context"
	"crypto/tls"
	"fmt"
	"net"
	"net/http"
	"sync"
	"time"

	"github.com/xtaci/kcp-go/v5"
	"github.com/xtaci/smux"
	"www.bamsoftware.com/git/dnstt.git/dns"
	"www.bamsoftware.com/git/dnstt.git/noise"
	"www.bamsoftware.com/git/dnstt.git/turbotunnel"
)

const idleTimeout = 2 * time.Minute

type Options struct {
	Domain string // e.g. t.example.com
	PubKey string // 64 hex digits
	UDP    string // resolver host:port (plain DNS), e.g. 8.8.8.8:53
	DoH    string // optional https URL
	DoT    string // optional host:port
	// ListenPacket lets the caller supply a socket that bypasses the VPN
	// (Android protect()). nil = net.ListenUDP.
	ListenPacket func() (net.PacketConn, error)
}

type Client struct {
	mu   sync.Mutex
	sess *smux.Session
	kc   *kcp.UDPSession
	pc   net.PacketConn
}

func dnsNameCapacity(domain dns.Name) int {
	capacity := 255 - 1
	for _, label := range domain {
		capacity -= len(label) + 1
	}
	capacity = capacity * 63 / 64
	capacity = capacity * 5 / 8
	return capacity
}

func Dial(o Options) (*Client, error) {
	domain, err := dns.ParseName(o.Domain)
	if err != nil {
		return nil, fmt.Errorf("domain: %v", err)
	}
	pubkey, err := noise.DecodeKey(o.PubKey)
	if err != nil {
		return nil, fmt.Errorf("pubkey: %v", err)
	}
	var remote net.Addr
	var pconn net.PacketConn
	switch {
	case o.DoH != "":
		remote = turbotunnel.DummyAddr{}
		tr := http.DefaultTransport.(*http.Transport).Clone()
		tr.Proxy = nil
		pconn, err = NewHTTPPacketConn(tr, o.DoH, 32)
	case o.DoT != "":
		remote = turbotunnel.DummyAddr{}
		pconn, err = NewTLSPacketConn(o.DoT, (&tls.Dialer{}).DialContext)
	default:
		addr := o.UDP
		if addr == "" {
			addr = "8.8.8.8:53"
		}
		if _, _, e := net.SplitHostPort(addr); e != nil {
			addr = net.JoinHostPort(addr, "53")
		}
		var ua *net.UDPAddr
		ua, err = net.ResolveUDPAddr("udp", addr)
		if err == nil {
			remote = ua
			if o.ListenPacket != nil {
				pconn, err = o.ListenPacket()
			} else {
				pconn, err = net.ListenUDP("udp", nil)
			}
		}
	}
	if err != nil {
		return nil, err
	}
	pconn = NewDNSPacketConn(pconn, remote, domain)
	mtu := dnsNameCapacity(domain) - 8 - 1 - numPadding - 1
	if mtu < 80 {
		pconn.Close()
		return nil, fmt.Errorf("domain leaves only %d bytes", mtu)
	}
	kc, err := kcp.NewConn2(remote, nil, 0, 0, pconn)
	if err != nil {
		pconn.Close()
		return nil, err
	}
	kc.SetStreamMode(true)
	kc.SetNoDelay(0, 0, 0, 1)
	kc.SetWindowSize(turbotunnel.QueueSize/2, turbotunnel.QueueSize/2)
	kc.SetMtu(mtu)
	rw, err := noise.NewClient(kc, pubkey)
	if err != nil {
		kc.Close()
		pconn.Close()
		return nil, err
	}
	cfg := smux.DefaultConfig()
	cfg.Version = 2
	cfg.KeepAliveTimeout = idleTimeout
	cfg.MaxStreamBuffer = 1 * 1024 * 1024
	sess, err := smux.Client(rw, cfg)
	if err != nil {
		kc.Close()
		pconn.Close()
		return nil, err
	}
	return &Client{sess: sess, kc: kc, pc: pconn}, nil
}

// Open returns a new stream to the server's upstream (raw TCP to its target).
func (c *Client) Open(ctx context.Context) (net.Conn, error) {
	return c.sess.OpenStream()
}

func (c *Client) Closed() bool { return c.sess.IsClosed() }

func (c *Client) Close() error {
	c.sess.Close()
	c.kc.Close()
	return c.pc.Close()
}
