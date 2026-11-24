package main

import (
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

var schemes = []string{
	"vless://", "vmess://", "trojan://", "ss://", "ssr://",
	"socks://", "hysteria://", "hysteria2://", "hy2://", "tuic://",
}

type ImportResult struct {
	Added   int    `json:"added"`
	Skipped int    `json:"skipped"`
	Error   string `json:"error,omitempty"`
}

func isHTTPURL(s string) bool {
	return strings.HasPrefix(s, "http://") || strings.HasPrefix(s, "https://")
}

func hasScheme(line string) bool {
	l := strings.ToLower(line)
	for _, s := range schemes {
		if strings.HasPrefix(l, s) {
			return true
		}
	}
	return false
}

func ExtractLinks(payload string) []string {
	out := []string{}
	for _, f := range strings.FieldsFunc(payload, func(r rune) bool { return r == '\n' || r == '\r' || r == ' ' || r == '\t' }) {
		f = strings.TrimSpace(f)
		if hasScheme(f) {
			out = append(out, f)
		}
	}
	return out
}

func b64decodeAny(s string) ([]byte, error) {
	s = strings.TrimSpace(s)
	s = strings.NewReplacer("\n", "", "\r", "", " ", "").Replace(s)
	for _, enc := range []*base64.Encoding{base64.StdEncoding, base64.RawStdEncoding, base64.URLEncoding, base64.RawURLEncoding} {
		if b, err := enc.DecodeString(s); err == nil {
			return b, nil
		}
	}
	return base64.StdEncoding.DecodeString(s)
}

func decodeIfBase64(body string) string {
	l := strings.ToLower(body)
	for _, s := range schemes {
		if strings.Contains(l, s) {
			return body
		}
	}
	if b, err := b64decodeAny(body); err == nil {
		return string(b)
	}
	return body
}

func fetchText(u string, ua string) (string, error) {
	req, err := http.NewRequest("GET", u, nil)
	if err != nil {
		return "", err
	}
	if ua != "" {
		req.Header.Set("User-Agent", ua)
	}
	c := http.Client{Timeout: 25 * time.Second}
	resp, err := c.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	b, err := io.ReadAll(io.LimitReader(resp.Body, 16<<20))
	return strings.TrimSpace(string(b)), err
}

func fetchSub(u string) (string, bool) {
	body, err := fetchText(u, "v2rayNG/1.8.5")
	if err != nil {
		return "", false
	}
	return decodeIfBase64(body), true
}

func (s *Store) addAll(links []string, group string, replaceGroup bool) ImportResult {
	if len(links) == 0 {
		return ImportResult{Error: "no_links"}
	}
	if replaceGroup {
		in := map[string]bool{}
		for _, l := range links {
			in[l] = true
		}
		for _, e := range s.All() {
			if e.Group == group && !in[e.URI] {
				s.Remove(e.ID)
			}
		}
	}
	existing := map[string]bool{}
	for _, e := range s.All() {
		existing[e.URI] = true
	}
	r := ImportResult{}
	for _, u := range links {
		if existing[u] {
			r.Skipped++
			continue
		}
		e := ParseEntry(u, group, "")
		if e == nil {
			r.Skipped++
			continue
		}
		s.Add(*e)
		existing[u] = true
		r.Added++
	}
	return r
}

// Import = LinkImporter.import
func (s *Store) Import(raw, group string) ImportResult {
	text := strings.TrimSpace(raw)
	if text == "" {
		return ImportResult{Error: "empty"}
	}
	payload := text
	if isHTTPURL(text) {
		p, ok := fetchSub(text)
		if !ok {
			return ImportResult{Error: "network"}
		}
		payload = p
	}
	return s.addAll(ExtractLinks(payload), group, false)
}

func (s *Store) ImportSubscription(sub Subscription) ImportResult {
	payload, ok := fetchSub(sub.URL)
	if !ok {
		return ImportResult{Error: "network"}
	}
	links := ExtractLinks(payload)
	if len(links) == 0 {
		return ImportResult{Error: "no_links"}
	}
	r := s.addAll(links, sub.Name, true)
	sub.LastUpdate = nowMs()
	n := 0
	for _, e := range s.All() {
		if e.Group == sub.Name {
			n++
		}
	}
	sub.Count = n
	s.UpdateSub(sub)
	s.prefs.Set("last_subscription_update", nowMs())
	return r
}

func (s *Store) RefreshAllSubscriptions() ImportResult {
	subs := s.Subs()
	if len(subs) == 0 {
		return ImportResult{Error: "no_subscriptions"}
	}
	r := ImportResult{}
	for _, sub := range subs {
		x := s.ImportSubscription(sub)
		r.Added += x.Added
		r.Skipped += x.Skipped
	}
	return r
}

// ExtractPrimaryOutbound = VlessConfig.extractPrimaryOutbound
func ExtractPrimaryOutbound(full string) map[string]any {
	var root map[string]any
	if json.Unmarshal([]byte(full), &root) != nil {
		// maybe a bare outbound object
		return nil
	}
	if p, ok := root["protocol"].(string); ok && p != "" && root["outbounds"] == nil {
		if p != "freedom" && p != "blackhole" && p != "dns" {
			return root
		}
	}
	arr, _ := root["outbounds"].([]any)
	for _, x := range arr {
		o, ok := x.(map[string]any)
		if !ok {
			continue
		}
		p, _ := o["protocol"].(string)
		if p != "" && p != "freedom" && p != "blackhole" && p != "dns" {
			return o
		}
	}
	return nil
}

// ---- deep links (DeepLinkActivity) ----

func linkParam(u *url.URL, name string) string {
	if v := strings.TrimSpace(u.Query().Get(name)); v != "" {
		return v
	}
	for _, part := range strings.Split(u.Fragment, "&") {
		i := strings.Index(part, "=")
		if i > 0 && strings.TrimSpace(part[:i]) == name {
			raw := strings.TrimSpace(part[i+1:])
			if d, err := url.QueryUnescape(raw); err == nil {
				return d
			}
			return raw
		}
	}
	return ""
}

func decodePayload(raw string) (string, bool) {
	if strings.HasPrefix(strings.ToLower(raw), "vless://") || strings.HasPrefix(strings.ToLower(raw), "http") {
		return raw, true
	}
	if b, err := b64decodeAny(raw); err == nil && strings.TrimSpace(string(b)) != "" {
		return string(b), true
	}
	return "", false
}

func splitConfigs(text string) []string {
	out := []string{}
	for _, f := range strings.FieldsFunc(text, func(r rune) bool { return r == '\n' || r == '\r' || r == ',' || r == ' ' }) {
		f = strings.TrimSpace(f)
		if strings.HasPrefix(strings.ToLower(f), "vless://") {
			out = append(out, f)
		}
	}
	return out
}

// IsActivationLink tells whether a pasted string is an app.example.com/open link
// or a hermesvpn:// link (the Windows equivalent of Android's App Link).
func IsActivationLink(s string) bool {
	s = strings.TrimSpace(s)
	l := strings.ToLower(s)
	return strings.HasPrefix(l, "hermesvpn://") || strings.Contains(l, "app.example.com/open")
}
