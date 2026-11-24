package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"time"
)

// Same server the Android app talks to (webhook domain on port 2087).
const apiBase = "https://hook.example.com:2087"

var httpClient = &http.Client{Timeout: 20 * time.Second}

type AccountResult struct {
	OK         bool        `json:"ok"`
	Error      string      `json:"error,omitempty"`
	Links      []VlessLink `json:"links"`
	ExpiryTime int64       `json:"expiry_time"`
	TotalGB    int64       `json:"total_gb"`
	SubID      string      `json:"sub_id,omitempty"`
	Account    string      `json:"account,omitempty"`
}

func doJSON(method, path string, body any, timeout time.Duration) (int, map[string]any, error) {
	var rd io.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		rd = bytes.NewReader(b)
	}
	req, err := http.NewRequest(method, apiBase+path, rd)
	if err != nil {
		return 0, nil, err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	req.Header.Set("User-Agent", "HermesVPN-Windows/"+AppVersionName)
	c := *httpClient
	c.Timeout = timeout
	resp, err := c.Do(req)
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	out := map[string]any{}
	if len(raw) == 0 || json.Unmarshal(raw, &out) != nil {
		out = map[string]any{"ok": false, "error": "network"}
	}
	return resp.StatusCode, out, nil
}

func toAccountResult(m map[string]any, err error) AccountResult {
	if err != nil || m == nil {
		return AccountResult{OK: false, Error: "network"}
	}
	b, _ := json.Marshal(m)
	var r AccountResult
	_ = json.Unmarshal(b, &r)
	if !r.OK && r.Error == "" {
		r.Error = "unknown"
	}
	return r
}

func apiLogin(username, password string) AccountResult {
	_, m, err := doJSON("POST", "/app/login", map[string]string{"username": username, "password": password}, 10*time.Second)
	return toAccountResult(m, err)
}

func apiVless(subID string) AccountResult {
	_, m, err := doJSON("GET", "/app/vless?sub_id="+url.QueryEscape(subID), nil, 10*time.Second)
	return toAccountResult(m, err)
}

func apiRedeem(token string) AccountResult {
	_, m, err := doJSON("POST", "/app/redeem", map[string]string{"token": token}, 15*time.Second)
	return toAccountResult(m, err)
}

func apiRegister(tg, email, newUser, password string) AccountResult {
	_, m, err := doJSON("POST", "/app/register", map[string]string{
		"tg_username": tg, "email": email, "new_username": newUser, "password": password,
	}, 10*time.Second)
	return toAccountResult(m, err)
}

// Generic call used by profile / purchase screens; returns the raw JSON.
func apiRaw(method, path string, body any, timeout time.Duration) map[string]any {
	_, m, err := doJSON(method, path, body, timeout)
	if err != nil {
		return map[string]any{"ok": false, "error": "network"}
	}
	return m
}
