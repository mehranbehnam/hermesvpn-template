package main

import (
	"encoding/json"
	"strings"
	"sync"
	"time"
)

// RouteRules.kt: server-side domain lists, cached locally, refreshed daily.
const routesURL = "https://app.example.com/rules/routes.json"

type RouteRules struct {
	mu     sync.Mutex
	prefs  *Prefs
	proxy  []string
	direct []string
}

func parseRoutes(body string) ([]string, []string, bool) {
	var v struct {
		Proxy  []string `json:"proxy"`
		Direct []string `json:"direct"`
	}
	if json.Unmarshal([]byte(body), &v) != nil {
		return nil, nil, false
	}
	clean := func(a []string) []string {
		o := []string{}
		for _, x := range a {
			if x = strings.TrimSpace(x); x != "" {
				o = append(o, x)
			}
		}
		return o
	}
	return clean(v.Proxy), clean(v.Direct), true
}

func (r *RouteRules) Load() {
	if raw := r.prefs.GetString("route_rules_json", ""); raw != "" {
		if p, d, ok := parseRoutes(raw); ok {
			r.mu.Lock()
			r.proxy, r.direct = p, d
			r.mu.Unlock()
		}
	}
	if nowMs()-r.prefs.GetInt64("route_rules_fetched_at", 0) < 24*3600*1000 {
		return
	}
	go func() {
		body, err := fetchText(routesURL, "")
		if err != nil {
			return
		}
		if p, d, ok := parseRoutes(body); ok {
			r.mu.Lock()
			r.proxy, r.direct = p, d
			r.mu.Unlock()
			r.prefs.SetMany(map[string]any{"route_rules_json": body, "route_rules_fetched_at": time.Now().UnixMilli()})
		}
	}()
}

func (r *RouteRules) Lists() ([]string, []string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]string{}, r.proxy...), append([]string{}, r.direct...)
}
