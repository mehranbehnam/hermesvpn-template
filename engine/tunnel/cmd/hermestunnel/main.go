// hermestunnel: run an emergency route and expose it as a local SOCKS5 port.
//
//	hermestunnel run  [-socks 127.0.0.1:10820] < config(.json|.hvpn)
//	hermestunnel info < file.hvpn       (safe summary, no secrets)
//
// Output: one JSON line per event on stdout.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"hermesvpn/tunnel/engine"
)

func emit(m map[string]any) {
	b, _ := json.Marshal(m)
	fmt.Println(string(b))
}

func load() (engine.Config, bool, error) {
	data, err := io.ReadAll(io.LimitReader(os.Stdin, 1<<20))
	if err != nil {
		return engine.Config{}, false, err
	}
	if engine.IsHVPN(data) {
		c, err := engine.OpenHVPN(data)
		return c, true, err
	}
	var c engine.Config
	return c, false, json.Unmarshal(data, &c)
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: hermestunnel run|info")
		os.Exit(2)
	}
	fs := flag.NewFlagSet(os.Args[1], flag.ExitOnError)
	socks := fs.String("socks", "", "local SOCKS5 listen address")
	hostmap := fs.String("map", "", "host=ip,host=ip (resolved by the app)")
	fs.Parse(os.Args[2:])
	cfg, locked, err := load()
	if err != nil {
		emit(map[string]any{"ev": "error", "msg": err.Error()})
		os.Exit(1)
	}
	switch os.Args[1] {
	case "info":
		server := cfg.WS.Connect
		if cfg.Mode == "dnstt" || locked {
			server = ""
		}
		emit(map[string]any{"ev": "info", "name": cfg.Name, "mode": cfg.Mode, "locked": locked,
			"expires": cfg.Expires, "id": cfg.ID, "server": server, "bypass": cfg.BypassHosts()})
		return
	case "run":
	default:
		os.Exit(2)
	}
	if *socks != "" {
		cfg.Socks = *socks
	}
	if *hostmap != "" {
		m := map[string]string{}
		for _, kv := range strings.Split(*hostmap, ",") {
			if k, v, ok := strings.Cut(kv, "="); ok {
				m[k] = v
			}
		}
		cfg.ApplyHostMap(m)
	}
	t, err := engine.Start(cfg, engine.Hooks{Log: func(s string) {
		if !locked {
			emit(map[string]any{"ev": "log", "msg": s})
		}
	}})
	if err != nil {
		msg := err.Error()
		if locked {
			msg = "connect failed"
		}
		emit(map[string]any{"ev": "error", "msg": msg})
		os.Exit(1)
	}
	emit(map[string]any{"ev": "ready", "socks": t.Addr(), "mode": cfg.Mode})
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	ppid := os.Getppid()
	tk := time.NewTicker(2 * time.Second)
	for {
		select {
		case <-sig:
			t.Close()
			return
		case <-tk.C:
			if os.Getppid() != ppid { // parent app died
				t.Close()
				return
			}
			emit(map[string]any{"ev": "stat", "up": t.Up, "down": t.Down, "state": t.State})
		}
	}
}
