package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// Version of this Windows build. Bump on every release (UpdateChecker compares versionCode).
const (
	AppVersionCode = 26
	AppVersionName = "1.0.25"
	AppBuildStamp  = "win-20261104"
)

// Approved Windows release. Written ONLY by ~/hermesvpn/release/approve-windows.sh after a
// build has been checked, so users are notified only about verified versions.
// 443 (Cloudflare, reachable from every Iranian ISP) first; :2087 only as a fallback — it is slow or
// blocked on many Iranian networks, which delayed 1.0.23 by two days for some users.
var updateManifestURLs = []string{
	"https://app.example.com/app/win-version.json",
	"https://hook.example.com:2087/dl/HermesVPN-Windows-update.json",
}

// fetchManifest tries each URL directly, then — if the tunnel is up — through the app's own local
// HTTP proxy, so a connected user always gets the update even when direct access is filtered.
func fetchManifest() (string, error) {
	var last error
	tryGet := func(u string, viaProxy bool) (string, error) {
		tr := &http.Transport{}
		if viaProxy {
			pu, _ := url.Parse(fmt.Sprintf("http://127.0.0.1:%d", httpPort))
			tr.Proxy = http.ProxyURL(pu)
		}
		cl := http.Client{Timeout: 15 * time.Second, Transport: tr}
		resp, err := cl.Get(u)
		if err != nil {
			return "", err
		}
		defer resp.Body.Close()
		if resp.StatusCode != 200 {
			return "", fmt.Errorf("HTTP %d", resp.StatusCode)
		}
		b, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
		body := strings.TrimSpace(string(b))
		if err == nil && !strings.HasPrefix(body, "{") {
			err = fmt.Errorf("not json")
		}
		return body, err
	}
	for _, u := range updateManifestURLs {
		if body, err := tryGet(u, false); err == nil {
			return body, nil
		} else {
			last = err
		}
	}
	if c, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", httpPort), 500*time.Millisecond); err == nil {
		c.Close()
		for _, u := range updateManifestURLs {
			if body, err := tryGet(u, true); err == nil {
				return body, nil
			} else {
				last = err
			}
		}
	}
	return "", last
}

type UpdateInfo struct {
	VersionCode int    `json:"versionCode"`
	VersionName string `json:"versionName"`
	Notes       string `json:"notes"`
	URL         string `json:"url"`
	Mandatory   bool   `json:"mandatory"`
}

// fetchUpdate reads the same version.json as Android, from its "windows"
// object: {"windows": {"versionCode": 2, "versionName": "1.0.1", "url": "...exe", "notes": "..."}}
func fetchUpdate() (*UpdateInfo, error) {
	body, err := fetchManifest()
	if err != nil {
		return nil, err
	}
	var info UpdateInfo
	if err := json.Unmarshal([]byte(body), &info); err != nil {
		return nil, err
	}
	if info.URL == "" || info.VersionCode <= AppVersionCode {
		return nil, nil
	}
	return &info, nil
}

// downloadUpdate saves the new exe next to the data dir and reports progress.
func downloadUpdate(u string, progress func(done, total int64)) (string, error) {
	cl := http.Client{Timeout: 15 * time.Minute}
	resp, err := cl.Get(u)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return "", fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	// a name different from the app's own exe: the installer kills "HermesVPN-Connect.exe"
	// before installing, so it must never run under that name (it would kill itself)
	dst := filepath.Join(dataDir(), "HermesVPN-Setup-update.exe")
	f, err := os.Create(dst)
	if err != nil {
		return "", err
	}
	total := resp.ContentLength
	var done int64
	buf := make([]byte, 64*1024)
	last := time.Now()
	for {
		n, rerr := resp.Body.Read(buf)
		if n > 0 {
			if _, werr := f.Write(buf[:n]); werr != nil {
				f.Close()
				return "", werr
			}
			done += int64(n)
			if time.Since(last) > 250*time.Millisecond {
				progress(done, total)
				last = time.Now()
			}
		}
		if rerr == io.EOF {
			break
		}
		if rerr != nil {
			f.Close()
			return "", rerr
		}
	}
	f.Close()
	progress(done, total)
	return dst, nil
}
