package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
)

// Prefs mirrors Android's SharedPreferences("hermesvpn_prefs"): one JSON file
// with the same keys, so behaviour stays identical between the two apps.
type Prefs struct {
	mu   sync.Mutex
	path string
	data map[string]any
}

func dataDir() string {
	base, err := os.UserConfigDir() // %APPDATA% on Windows
	if err != nil || base == "" {
		base = os.TempDir()
	}
	d := filepath.Join(base, "HermesVPN")
	_ = os.MkdirAll(d, 0o700)
	return d
}

func NewPrefs() *Prefs {
	p := &Prefs{path: filepath.Join(dataDir(), "prefs.json"), data: map[string]any{}}
	if b, err := os.ReadFile(p.path); err == nil {
		_ = json.Unmarshal(b, &p.data)
	}
	return p
}

func (p *Prefs) saveLocked() {
	b, _ := json.MarshalIndent(p.data, "", " ")
	tmp := p.path + ".tmp"
	if os.WriteFile(tmp, b, 0o600) == nil {
		_ = os.Rename(tmp, p.path)
	}
}

func (p *Prefs) GetString(k, def string) string {
	p.mu.Lock()
	defer p.mu.Unlock()
	if v, ok := p.data[k].(string); ok {
		return v
	}
	return def
}

func (p *Prefs) GetBool(k string, def bool) bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	if v, ok := p.data[k].(bool); ok {
		return v
	}
	return def
}

func (p *Prefs) GetInt64(k string, def int64) int64 {
	p.mu.Lock()
	defer p.mu.Unlock()
	switch v := p.data[k].(type) {
	case float64:
		return int64(v)
	case int64:
		return v
	case int:
		return int64(v)
	}
	return def
}

func (p *Prefs) GetStrings(k string) []string {
	p.mu.Lock()
	defer p.mu.Unlock()
	out := []string{}
	if arr, ok := p.data[k].([]any); ok {
		for _, x := range arr {
			if s, ok := x.(string); ok {
				out = append(out, s)
			}
		}
	}
	if arr, ok := p.data[k].([]string); ok {
		out = append(out, arr...)
	}
	return out
}

func (p *Prefs) Set(k string, v any) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if v == nil {
		delete(p.data, k)
	} else {
		p.data[k] = v
	}
	p.saveLocked()
}

func (p *Prefs) SetMany(m map[string]any) {
	p.mu.Lock()
	defer p.mu.Unlock()
	for k, v := range m {
		if v == nil {
			delete(p.data, k)
		} else {
			p.data[k] = v
		}
	}
	p.saveLocked()
}

// Snapshot returns a copy for the UI (password is never sent back).
func (p *Prefs) Snapshot() map[string]any {
	p.mu.Lock()
	defer p.mu.Unlock()
	out := map[string]any{}
	for k, v := range p.data {
		if k == "auth_password" || k == "configs_json" || k == "subscriptions_json" || k == "last_xray_config" || k == "route_rules_json" {
			continue
		}
		out[k] = v
	}
	return out
}
